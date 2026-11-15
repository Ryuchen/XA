from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import DatabaseError, close_old_connections
from django.db.migrations.recorder import MigrationRecorder
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from rest_framework.exceptions import ValidationError

from engagement.models import PointFundingLot
from orders import addon_payment
from orders.models import OrderAddonFundingAllocation, OrderAddonPayment, OrderSegmentFinancialSnapshot
from orders.tests import test_addon_intents as intent_fixture
from orders.tests.factories import make_user
from wallet.models import Transaction
from wallet.services import get_wallet
from club_accounts.services import get_or_create_account_for_legacy_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store


def prepare(fixture, *, inviter=True, host=None):
    fixture.setUp()
    if host:
        fixture.client.defaults['HTTP_HOST'] = host
    fixture.config.condition_mode = 'NONE'; fixture.config.conditions = ''
    fixture.config.save(update_fields=['condition_mode', 'conditions'])
    if inviter:
        user = make_user()
        account = get_or_create_account_for_legacy_user(user)
        fixture.order.inviter, fixture.order.inviter_account = user, account
        fixture.order.inviter_commission_rate_snapshot = 10
        fixture.order.save(update_fields=['inviter', 'inviter_account', 'inviter_commission_rate_snapshot'])
    response = fixture.client.post(fixture.url, fixture.payload, format='json')
    assert response.status_code == 200, response.data
    wallet = get_wallet(account=fixture.boss, user=fixture.boss_user)
    wallet.balance = 3000
    wallet.save(update_fields=['balance'])
    for kind, amount in [('PREPAID', 1600), ('GIFT', 900), ('COMPENSATION', 500)]:
        PointFundingLot.objects.create(account=fixture.boss, source_key=f'addon-test-{kind}',
                                       kind=kind, remaining=amount)
    return response.data['data']['id'], wallet


@override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class AddonPaymentTests(TestCase):
    def setUp(self):
        self.fixture = intent_fixture.AddonIntentTests(methodName='test_boss_intent_records_sources_snapshot_and_idempotency_without_payment')
        self.intent_id, self.wallet = prepare(self.fixture)

    def test_wallet_sources_addon_has_no_referral_and_idempotent_receipt(self):
        first = addon_payment.pay(self.intent_id, actor=self.fixture.boss)
        second = addon_payment.pay(self.intent_id, actor=self.fixture.boss)
        self.assertEqual(first.pk, second.pk)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 1500)
        self.assertEqual((first.commission_fee, first.provider_income, first.inviter_income,
                          first.store_income, first.funding_required), (300, 1200, 0, 0, 0))
        self.assertEqual(first.inviter_rate, 0)
        self.assertEqual(first.inviter_id, self.fixture.order.inviter_account_id)
        self.assertIsNone(first.buyer_transaction.order_id)
        self.assertEqual(first.buyer_transaction.amount, -1500)
        self.assertEqual(dict(OrderAddonFundingAllocation.objects.filter(payment=first)
            .values_list('kind', 'amount')), {'PREPAID':1200, 'GIFT':200, 'COMPENSATION':100})
        self.assertEqual(dict(PointFundingLot.objects.filter(account=self.fixture.boss)
            .values_list('kind', 'remaining')), {'PREPAID':400, 'GIFT':700, 'COMPENSATION':400})
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet, tx_type='PAY').count(), 1)
        self.assertFalse(Transaction.objects.filter(tx_type='INCOME').exists())
        item = self.fixture.client.get(self.fixture.url).data['data']['items'][0]
        self.assertTrue(item['paid'])
        self.assertEqual(item['payment_id'], first.pk)
        Transaction.objects.filter(pk=first.buyer_transaction_id).update(operator_account_id=self.fixture.other.pk)
        with self.assertRaises(ValidationError):
            addon_payment.pay(self.intent_id, actor=self.fixture.boss)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 1500)

    def test_main_settlement_waits_for_addon_integration(self):
        from orders.services import settle_order
        from orders.settlement import SettlementError
        from stores.db import transaction
        addon_payment.pay(self.intent_id, actor=self.fixture.boss)
        for enabled in (True, False):
            with self.subTest(feature_enabled=enabled), override_settings(ADDON_PAYMENT_INTERNAL_TESTING=enabled):
                with transaction.atomic():
                    with self.assertRaises(SettlementError):
                        settle_order(self.fixture.order)
                item = self.fixture.client.get(self.fixture.url).data['data']['items'][0]
                self.assertTrue(item['paid'])
                self.assertFalse(item['payment_available'])
        self.assertFalse(Transaction.objects.filter(tx_type='INCOME').exists())

    def test_missing_migrated_payment_table_never_allows_settlement(self):
        from stores.db import transaction
        connection = transaction.get_connection()
        with connection.cursor() as cursor:
            tables = connection.introspection.table_names(cursor)
        missing = [name for name in tables if name != OrderAddonPayment._meta.db_table]
        MigrationRecorder.Migration.objects.using(connection.alias).create(
            app='orders', name='0175_orderaddonfundingallocation_orderaddonpayment_and_more')
        with patch.object(connection.introspection, 'table_names', return_value=missing):
            with self.assertRaises(DatabaseError):
                addon_payment.guard_main_settlement(self.fixture.order)

    def test_independent_debit_evidence_blocks_if_receipt_and_migration_are_missing(self):
        from orders.settlement import SettlementError
        from stores.db import transaction
        addon_payment.pay(self.intent_id, actor=self.fixture.boss)
        connection = transaction.get_connection()
        with connection.cursor() as cursor:
            tables = connection.introspection.table_names(cursor)
        missing = [name for name in tables if name != OrderAddonPayment._meta.db_table]
        MigrationRecorder.Migration.objects.using(connection.alias).filter(
            app='orders', name='0175_orderaddonfundingallocation_orderaddonpayment_and_more').delete()
        with patch.object(connection.introspection, 'table_names', return_value=missing), \
                override_settings(ADDON_PAYMENT_INTERNAL_TESTING=False):
            with self.assertRaises(SettlementError):
                addon_payment.guard_main_settlement(self.fixture.order)

    def test_independent_debit_evidence_blocks_if_receipt_row_is_missing(self):
        from orders.settlement import SettlementError
        payment = addon_payment.pay(self.intent_id, actor=self.fixture.boss)
        OrderAddonFundingAllocation.objects.filter(payment=payment).delete()
        payment.delete()
        with self.assertRaises(ValidationError):
            addon_payment.pay(self.intent_id, actor=self.fixture.boss)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 1500)
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet, tx_type='PAY').count(), 1)
        with override_settings(ADDON_PAYMENT_INTERNAL_TESTING=False):
            with self.assertRaises(SettlementError):
                addon_payment.guard_main_settlement(self.fixture.order)
            result = self.fixture.client.get(self.fixture.url)
            self.assertEqual(result.status_code, 400)

    def test_price_change_and_insufficient_balance_do_not_debit(self):
        self.fixture.config.version += 1; self.fixture.config.save(update_fields=['version'])
        with self.assertRaises(ValidationError):
            addon_payment.pay(self.intent_id, actor=self.fixture.boss)
        self.fixture.config.version -= 1; self.fixture.config.save(update_fields=['version'])
        self.wallet.balance = 1000; self.wallet.save(update_fields=['balance'])
        with self.assertRaises(ValidationError):
            addon_payment.pay(self.intent_id, actor=self.fixture.boss)
        self.assertFalse(OrderAddonPayment.objects.exists())
        self.assertFalse(Transaction.objects.filter(wallet=self.wallet, tx_type='PAY').exists())

    def test_untracked_balance_is_not_silently_classified_as_principal(self):
        self.wallet.balance = 3100; self.wallet.save(update_fields=['balance'])
        with self.assertRaises(ValidationError):
            addon_payment.pay(self.intent_id, actor=self.fixture.boss)
        self.assertFalse(OrderAddonPayment.objects.exists())
        self.assertEqual(PointFundingLot.objects.count(), 3)

    def test_posting_failure_rolls_back_wallet_lots_and_transaction(self):
        with patch('orders.addon_payment.OrderAddonFundingAllocation.objects.bulk_create', side_effect=RuntimeError('write failed')):
            with self.assertRaises(RuntimeError):
                addon_payment.pay(self.intent_id, actor=self.fixture.boss)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 3000)
        self.assertEqual(sum(PointFundingLot.objects.values_list('remaining', flat=True)), 3000)
        self.assertFalse(Transaction.objects.filter(wallet=self.wallet, tx_type='PAY').exists())
        self.assertFalse(OrderAddonPayment.objects.exists())

    def test_disabled_service_and_unrelated_boss_cannot_charge(self):
        with override_settings(ADDON_PAYMENT_INTERNAL_TESTING=False):
            with self.assertRaises(ValidationError):
                addon_payment.pay(self.intent_id, actor=self.fixture.boss)
        with self.assertRaises(ValidationError):
            addon_payment.pay(self.intent_id, actor=self.fixture.other)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 3000)

    def test_fixed_commission_rounds_fee_down_to_internal_unit(self):
        # Change the main snapshot before creating a fresh, reviewed intent.
        self.fixture.coverage.deal_amount = 900; self.fixture.coverage.save(update_fields=['deal_amount'])
        OrderSegmentFinancialSnapshot.objects.filter(segment=self.fixture.segment).update(
            incoming_basis=900, commission_type='FIXED', commission_rate=0, commission_fixed=200,
            fixed_rule_total=200, income_at_entry=700)
        data = {**self.fixture.payload, 'request_id':'fixed-addon-intent'}
        response = self.fixture.client.post(self.fixture.url, data, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        payment = addon_payment.pay(response.data['data']['id'], actor=self.fixture.boss)
        self.assertEqual((payment.commission_fee, payment.provider_income), (333, 1167))

    def test_mixed_recharge_lot_keeps_gift_and_principal_separate(self):
        PointFundingLot.objects.filter(account=self.fixture.boss).delete()
        PointFundingLot.objects.create(account=self.fixture.boss, source_key='recharge:1', kind='RECHARGE',
                                       remaining=2500, gift_remaining=900)
        PointFundingLot.objects.create(account=self.fixture.boss, source_key='compensation:1',
                                       kind='COMPENSATION', remaining=500)
        payment = addon_payment.pay(self.intent_id, actor=self.fixture.boss)
        lot = PointFundingLot.objects.get(source_key='recharge:1')
        self.assertEqual((lot.remaining, lot.gift_remaining), (1100, 700))
        self.assertEqual(dict(payment.allocations.values_list('kind', 'amount')),
                         {'PREPAID':1200, 'GIFT':200, 'COMPENSATION':100})

    def test_staff_review_and_boss_confirmation_are_verified_before_debit(self):
        self.fixture.config.condition_mode = 'STAFF_REVIEW'; self.fixture.config.conditions = '客服核实玩法'
        self.fixture.config.save(update_fields=['condition_mode', 'conditions'])
        payload = {**self.fixture.payload, 'request_id':'reviewed-addon', 'confirmation_method':'电话',
                   'confirmation_content':'老板确认三小时、人员、金额及钱包来源', 'review_note':'已核实玩法条件'}
        response = self.fixture.staff.post(self.fixture.staff_url, payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        intent_id = response.data['data']['id']
        actor = response.wsgi_request.account
        payment = addon_payment.pay(intent_id, actor=actor)
        self.assertEqual(payment.operator_id, actor.pk)
        self.assertEqual(payment.buyer_transaction.amount, -1500)

    def test_boss_can_pay_reviewed_intent_through_api_once(self):
        url = self.fixture.url + f'{self.intent_id}/pay/'
        self.assertEqual(self.fixture.other_client.post(url, {}, format='json').status_code, 404)
        first = self.fixture.client.post(url, {}, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        self.assertTrue(first.data['data']['paid'])
        second = self.fixture.client.post(url, {}, format='json')
        self.assertEqual(second.data['data']['payment_id'], first.data['data']['payment_id'])
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 1500)
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet, tx_type='PAY').count(), 1)

    def test_authorized_staff_payment_is_audited_and_disabled_path_cannot_debit(self):
        from console.models import AdminAuditLog
        url = self.fixture.staff_url + f'{self.intent_id}/pay/'
        self.assertEqual(self.fixture.denied.post(url, {}, format='json').status_code, 403)
        with override_settings(ADDON_PAYMENT_INTERNAL_TESTING=False):
            self.assertEqual(self.fixture.staff.post(url, {}, format='json').status_code, 400)
        paid = self.fixture.staff.post(url, {}, format='json')
        self.assertEqual(paid.status_code, 200, paid.data)
        self.assertTrue(paid.data['data']['paid'])
        audit = AdminAuditLog.objects.get(resource='order-addon-payment')
        self.assertEqual(audit.request_body['intent_id'], self.intent_id)
        self.assertEqual(audit.request_body['amount'], 1500)
        self.assertEqual(self.fixture.staff.post(url, {}, format='json').status_code, 200)
        self.assertEqual(AdminAuditLog.objects.filter(resource='order-addon-payment').count(), 1)


@override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class AddonPaymentRaceTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_payment_debits_once(self):
        fixture = intent_fixture.AddonIntentTests(methodName='test_boss_intent_records_sources_snapshot_and_idempotency_without_payment')
        intent_id, wallet = prepare(fixture)
        barrier = Barrier(2)

        def pay(_):
            from club_accounts.models import ClubAccount
            close_old_connections()
            actor = ClubAccount.objects.get(pk=fixture.boss.pk)
            barrier.wait(timeout=10)
            result = addon_payment.pay(intent_id, actor=actor)
            pk = result.pk
            close_old_connections()
            return pk

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(pay, range(2)))
        self.assertEqual(results[0], results[1])
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 1500)
        self.assertEqual(OrderAddonPayment.objects.count(), 1)
        self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='PAY').count(), 1)


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class AddonPaymentStoresTests(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_matching_ids_debit_only_the_correct_store(self):
        stores = (resolve_store(), xinan_store())
        records = []
        for store, host in zip(stores, ('sl.esports.club', 'xa.esports.club')):
            with store_context(store):
                fixture = intent_fixture.AddonIntentTests(methodName='test_boss_intent_records_sources_snapshot_and_idempotency_without_payment')
                intent_id, wallet = prepare(fixture, host=host)
                records.append((fixture, intent_id, wallet.pk))
        self.assertEqual(records[0][1:], records[1][1:])
        with store_context(stores[0]):
            addon_payment.pay(records[0][1], actor=records[0][0].boss)
            with override_settings(ADDON_PAYMENT_INTERNAL_TESTING=False):
                from orders.settlement import SettlementError
                with self.assertRaises(SettlementError):
                    addon_payment.guard_main_settlement(records[0][0].order)
        with store_context(stores[1]):
            wallet = get_wallet(account=records[1][0].boss, create=False)
            self.assertEqual(wallet.balance, 3000)
            self.assertFalse(OrderAddonPayment.objects.exists())
            with override_settings(ADDON_PAYMENT_INTERNAL_TESTING=False):
                addon_payment.guard_main_settlement(records[1][0].order)
                item = records[1][0].client.get(records[1][0].url).data['data']['items'][0]
                self.assertFalse(item['paid'])
            with self.assertRaises(ValidationError):
                addon_payment.pay(records[1][1], actor=records[0][0].boss)
            wallet.refresh_from_db()
            self.assertEqual(wallet.balance, 3000)
            addon_payment.pay(records[1][1], actor=records[1][0].boss)
            wallet.refresh_from_db()
            self.assertEqual(wallet.balance, 1500)
