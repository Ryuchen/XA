from unittest.mock import patch
from unittest import skipUnless

from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError

from club_accounts.services import get_or_create_account_for_legacy_user
from console.points_redemption_config import save_points_terms
from engagement.funding_wallet import allocate_locked_order_funding, snapshot_service_funding_terms
from engagement.models import MemberProfile, OrderFundingAllocation, PointLedger
from engagement.services import config
from orders.models import Order, OrderPointsRedemption
from orders.points_service_payment import quote, record_locked
from orders.tests.factories import make_service, make_user
from stores.db import transaction
from wallet.models import Transaction
from wallet.services import get_wallet


@override_settings(SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True)
class ServicePointsReceiptTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.account = get_or_create_account_for_legacy_user(self.user)
        self.wallet = get_wallet(user=self.user, account=self.account)
        self.wallet.balance = 2000
        self.wallet.save(update_fields=['balance'])
        from orders.tests.test_points_redemption_preview import seed_recharge
        seed_recharge(self.wallet, principal=1000, gift=1000) if self._testMethodName == 'test_mixed_recharge_can_supply_both_original_principal_and_gift' else seed_recharge(self.wallet)
        self.profile, _ = MemberProfile.objects.update_or_create(account=self.account,
                                                                 defaults={'points_balance': 15000})
        self.service = make_service(price=1000)
        config()
        save_points_terms(group_point_hundredths=10000, group_coin_units=30,
                          max_ratio_basis_points=5000, version=0, audit=lambda *_: None)
        self.price = {'original_amount': 1000, 'boss_discount': 0, 'promo_discount': 0,
                      'coupon_discount': 0, 'promotion_id': None, 'game_rounds': 1}

    def make_token(self):
        return quote(self.account, self.service, self.price, 10000)[1]

    def create_payment(self, *, gift=0):
        order = Order.objects.create(customer=self.user, customer_account=self.account, service=self.service,
            amount=970, original_amount=1000, business_cycle=self.account.business_cycle)
        wallet = type(self.wallet).objects.select_for_update().get(pk=self.wallet.pk)
        allocate_locked_order_funding(order, wallet, gift=gift)
        before = wallet.balance
        wallet.balance -= order.amount
        wallet.save(update_fields=['balance'])
        payment = Transaction.objects.create(wallet=wallet, order=order, tx_type=Transaction.TxType.PAY,
            amount=-order.amount, balance_before=before, balance_after=wallet.balance)
        snapshot_service_funding_terms(order, paid_at=payment.created_at)
        return order, payment

    def test_atomic_receipt_records_actual_payment_and_points(self):
        token = self.make_token()
        with transaction.atomic():
            order, payment = self.create_payment()
            receipt = record_locked(order_id=order.pk, payment_id=payment.pk, token=token)
        self.profile.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((self.profile.points_balance, self.wallet.balance), (5000, 1030))
        self.assertEqual((receipt.deal_units, receipt.paid_units, receipt.discount_units,
                          receipt.points_hundredths, receipt.groups), (1000, 970, 30, 10000, 1))
        self.assertEqual((receipt.ledger.order_id, receipt.ledger.amount, receipt.payment_id),
                         (order.pk, -10000, payment.pk))
        self.assertEqual(sum(order.funding_allocations.values_list('amount', flat=True)), 970)

    def test_receipt_failure_rolls_back_order_wallet_and_points(self):
        token = self.make_token()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                order, payment = self.create_payment()
                with patch.object(OrderPointsRedemption.objects, 'create', side_effect=RuntimeError('fail')):
                    record_locked(order_id=order.pk, payment_id=payment.pk, token=token)
        self.profile.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((self.profile.points_balance, self.wallet.balance), (15000, 2000))
        self.assertFalse(Order.objects.exists())
        self.assertFalse(Transaction.objects.filter(tx_type=Transaction.TxType.PAY).exists())
        self.assertFalse(PointLedger.objects.exists())
        self.assertFalse(OrderFundingAllocation.objects.exists())

    def test_changed_config_balance_or_switch_prevents_payment(self):
        for change in ('config', 'balance', 'switch'):
            with self.subTest(change=change):
                with transaction.atomic():
                    token = self.make_token()
                    if change == 'config':
                        save_points_terms(group_point_hundredths=10000, group_coin_units=50,
                                          max_ratio_basis_points=5000, version=1, audit=lambda *_: None)
                    elif change == 'balance':
                        MemberProfile.objects.filter(pk=self.profile.pk).update(points_balance=5000)
                    else:
                        operation = config(); operation.points_enabled = False; operation.save(update_fields=['points_enabled'])
                    order, payment = self.create_payment()
                    with self.assertRaises(ValidationError):
                        record_locked(order_id=order.pk, payment_id=payment.pk, token=token)
                    transaction.set_rollback(True)
                self.assertFalse(Order.objects.exists())

    def test_each_usage_switch_blocks_order_payment_until_resumed(self):
        import json
        from engagement.models import PointFundingLot
        from engagement.points_usage import lock_usage
        for field in ('points_enabled', 'redemption_enabled', 'shop_enabled', 'lottery_enabled'):
            with self.subTest(field=field):
                token = self.make_token()
                with transaction.atomic():
                    operation, row, switches = lock_usage()
                    if field == 'points_enabled':
                        operation.points_enabled = False
                        operation.save(update_fields=['points_enabled'])
                    else:
                        switches[field] = False
                        row.value = json.dumps(switches)
                        row.save(update_fields=['value'])
                    order, payment = self.create_payment()
                    with self.assertRaises(ValidationError):
                        record_locked(order_id=order.pk, payment_id=payment.pk, token=token)
                    transaction.set_rollback(True)
                self.profile.refresh_from_db(); self.wallet.refresh_from_db()
                self.assertEqual((self.profile.points_balance, self.wallet.balance), (15000, 2000))
                self.assertFalse(Order.objects.exists())
                self.assertFalse(PointLedger.objects.exists())
        token = self.make_token()
        with transaction.atomic():
            order, payment = self.create_payment()
            record_locked(order_id=order.pk, payment_id=payment.pk, token=token)
        self.profile.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((self.profile.points_balance, self.wallet.balance), (5000, 1030))

    def test_wrong_wallet_payment_and_source_are_rejected(self):
        token = self.make_token()
        for bad in ('payment', 'source', 'price', 'funding_terms'):
            with self.subTest(bad=bad):
                with transaction.atomic():
                    order, payment = self.create_payment()
                    if bad == 'payment':
                        payment.tx_type = Transaction.TxType.TOPUP; payment.save(update_fields=['tx_type'])
                    elif bad == 'source':
                        order.funding_allocations.update(kind='GIFT')
                    elif bad == 'funding_terms':
                        order.funding_terms = None; order.save(update_fields=['funding_terms'])
                    else:
                        order.original_amount = 1010; order.save(update_fields=['original_amount'])
                    with self.assertRaises(ValidationError):
                        record_locked(order_id=order.pk, payment_id=payment.pk, token=token)
                    transaction.set_rollback(True)

    def test_receipt_refuses_mismatched_customer_or_missing_legacy_mapping(self):
        from club_accounts.models import LegacyAccountMap
        token = self.make_token()
        for fault in ('customer', 'mapping'):
            with self.subTest(fault=fault), transaction.atomic():
                order, payment = self.create_payment()
                if fault == 'customer':
                    order.customer = make_user()
                    order.save(update_fields=['customer'])
                else:
                    LegacyAccountMap.objects.filter(account=self.account).delete()
                with self.assertRaisesMessage(ValidationError, '归属不一致'):
                    record_locked(order_id=order.pk, payment_id=payment.pk, token=token)
                transaction.set_rollback(True)
        self.assertFalse(OrderPointsRedemption.objects.exists())

    def test_account_only_receipt_works_when_order_and_wallet_both_have_no_legacy_user(self):
        from club_accounts.models import LegacyAccountMap
        from orders.models import OrderRefund
        from orders.points_service_refund import refund_assets_locked
        token = self.make_token()
        with transaction.atomic():
            order, payment = self.create_payment()
            LegacyAccountMap.objects.filter(account=self.account).delete()
            order.customer = None; order.save(update_fields=['customer'])
            self.wallet.user = None; self.wallet.save(update_fields=['user'])
            receipt = record_locked(order_id=order.pk, payment_id=payment.pk, token=token)
            self.assertEqual(receipt.order.customer_account_id, self.account.pk)
            self.assertIsNone(receipt.payment.wallet.user_id)
            refund = OrderRefund.objects.create(order=order, amount=480, reason='账户退款测试')
            returned = refund_assets_locked(order_id=order.pk, refund_id=refund.pk, equivalent_units=500)
            self.assertEqual((returned.coin_units, returned.points_hundredths), (480, 5000))
            transaction.set_rollback(True)

    def test_orphan_redemption_debit_prevents_second_points_charge(self):
        token = self.make_token()
        with transaction.atomic():
            order, payment = self.create_payment()
            PointLedger.objects.create(account=self.account, order=order, event_key='orphan-redeem',
                kind='ORDER_POINTS_REDEEM', amount=-100, balance_after=14900, remark='隔离异常流水')
            with self.assertRaisesMessage(ValidationError, '已登记'):
                record_locked(order_id=order.pk, payment_id=payment.pk, token=token)
            transaction.set_rollback(True)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.points_balance, 15000)

    def test_receipt_rejects_mismatched_original_funding_lot(self):
        from engagement.models import PointFundingLot
        token = self.make_token()
        with transaction.atomic():
            order, payment = self.create_payment()
            lot = order.funding_allocations.get(kind='PREPAID').lot
            lot.kind = PointFundingLot.Kind.GIFT
            lot.gift_remaining = None
            lot.save(update_fields=['kind', 'gift_remaining'])
            with self.assertRaises(ValidationError):
                record_locked(order_id=order.pk, payment_id=payment.pk, token=token)
            transaction.set_rollback(True)
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (2000, 15000))
        self.assertFalse(Order.objects.exists())

    def test_mixed_recharge_can_supply_both_original_principal_and_gift(self):
        from engagement.models import PointFundingLot
        from orders.models import OrderRefund
        from orders.points_service_refund import refund_assets_locked
        lot = PointFundingLot.objects.get(account=self.account)
        token = quote(self.account, self.service, self.price, 10000, gift=100)[1]
        with transaction.atomic():
            order, payment = self.create_payment(gift=100)
            record_locked(order_id=order.pk, payment_id=payment.pk, token=token)
        self.assertEqual(set(order.funding_allocations.values_list('kind', flat=True)), {'PREPAID', 'GIFT'})
        self.assertEqual(order.funding_allocations.values('lot_id').distinct().count(), 1)
        for equivalent, coins in ((500, 480), (500, 490)):
            with transaction.atomic():
                refund = OrderRefund.objects.create(order=order, amount=coins, reason='混合充值退款测试')
                refund_assets_locked(order_id=order.pk, refund_id=refund.pk, equivalent_units=equivalent)
        lot.refresh_from_db(); self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((lot.remaining, lot.gift_remaining, self.wallet.balance, self.profile.points_balance),
                         (2000, 1000, 2000, 15000))

    def test_invalid_signature_and_replay_cannot_debit_again(self):
        token = self.make_token()
        with transaction.atomic():
            order, payment = self.create_payment()
            with self.assertRaises(ValidationError):
                record_locked(order_id=order.pk, payment_id=payment.pk, token=token + 'x')
            record_locked(order_id=order.pk, payment_id=payment.pk, token=token)
            with self.assertRaises(ValidationError):
                record_locked(order_id=order.pk, payment_id=payment.pk, token=token)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.points_balance, 5000)
        self.assertEqual(OrderPointsRedemption.objects.count(), 1)

    def test_expired_quote_fails_without_spending(self):
        token = self.make_token()
        with transaction.atomic():
            order, payment = self.create_payment()
            with patch('django.core.signing.time.time', return_value=4_102_444_800):
                with self.assertRaises(ValidationError):
                    record_locked(order_id=order.pk, payment_id=payment.pk, token=token)
            transaction.set_rollback(True)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.points_balance, 15000)

    @override_settings(SERVICE_ORDER_POINTS_RECEIPT_ENABLED=False)
    def test_default_closed_feature_has_no_quote(self):
        with self.assertRaises(ValidationError):
            self.make_token()


@override_settings(SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True)
@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class ServicePointsReceiptStoreTests(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_signed_quote_cannot_be_replayed_in_another_store(self):
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store

        fixtures = []
        xinan = xinan_store()
        for store in (resolve_store(), xinan):
            with store_context(store):
                user = make_user()
                account = get_or_create_account_for_legacy_user(user)
                wallet = get_wallet(user=user, account=account)
                wallet.balance = 2000; wallet.save(update_fields=['balance'])
                from orders.tests.test_points_redemption_preview import seed_recharge
                seed_recharge(wallet)
                MemberProfile.objects.update_or_create(account=account, defaults={'points_balance': 15000})
                service = make_service(price=1000)
                config()
                save_points_terms(group_point_hundredths=10000, group_coin_units=30,
                                  max_ratio_basis_points=5000, version=0, audit=lambda *_: None)
                price = {'original_amount': 1000, 'boss_discount': 0, 'promo_discount': 0,
                         'coupon_discount': 0, 'promotion_id': None, 'game_rounds': 1}
                fixtures.append((user, account, wallet, service, quote(account, service, price, 10000)[1]))
        # IDs intentionally coincide, so rejection must come from the store
        # binding rather than incidental differences between database rows.
        self.assertEqual(fixtures[0][1].pk, fixtures[1][1].pk)
        self.assertEqual(fixtures[0][3].pk, fixtures[1][3].pk)
        with store_context(xinan), transaction.atomic():
            user, account, wallet, service, _ = fixtures[1]
            order = Order.objects.create(customer=user, customer_account=account, service=service,
                amount=970, original_amount=1000, business_cycle=account.business_cycle)
            allocate_locked_order_funding(order, wallet)
            wallet.balance = 1030; wallet.save(update_fields=['balance'])
            payment = Transaction.objects.create(wallet=wallet, order=order, tx_type=Transaction.TxType.PAY,
                amount=-970, balance_before=2000, balance_after=1030)
            snapshot_service_funding_terms(order, paid_at=payment.created_at)
            with self.assertRaises(ValidationError):
                record_locked(order_id=order.pk, payment_id=payment.pk, token=fixtures[0][4])
            self.assertFalse(PointLedger.objects.exists())
