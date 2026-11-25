from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from unittest import skipUnless

from django.conf import settings
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.models import ClubAccount, LegacyAccountMap
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership
from orders.addon_payment_models import OrderAddonPayment
from orders.models import (
    Order, OrderParticipationSegment, OrderProvider, OrderSegmentPersonSettlement,
    OrderSegmentSettlementManifest,
)
from orders.tests import test_release_flow as flow
from orders.tests import test_segment_addon_settlement as addon_fixture
from orders.tests.factories import make_console_user, make_service, make_user
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.models import Store
from stores.tests_hosts import xinan_store
from wallet.models import IncomeHold, Transaction, Wallet


BREAKDOWN_SETTINGS = dict(
    ADDON_PAYMENT_INTERNAL_TESTING=True,
    ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
    ADDON_SETTLEMENT_INTERNAL_TESTING=True,
    ADDON_SEGMENT_SETTLEMENT_INTERNAL_TESTING=True,
    ADDON_HANDOVER_INTERNAL_TESTING=True,
    ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
    ADDON_SEGMENT_REFUND_REVISION_INTERNAL_TESTING=True,
    BOSS_FUNDING_SELECTION_ENABLED=True,
    SERVICE_POINTS_CHECKOUT_TESTING=True,
    SERVICE_POINTS_FUNDING_TESTING=True,
    SERVICE_POINTS_SETTLEMENT_TESTING=True,
    SERVICE_POINTS_REPORT_TESTING=True,
    SERVICE_POINTS_ASSIGN_TESTING=True,
    SERVICE_ORDINARY_FUNDING_TESTING=True,
    SERVICE_ORDINARY_TRANSFER_FUNDING_TESTING=True,
    ORIGINAL_ATTRIBUTION_ENABLED=True,
    SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    SEGMENT_REPORT_ENABLED=True,
    SEGMENT_SETTLEMENT_ENABLED=True,
    SEGMENT_STANDARD_TRANSFER_ENABLED=True,
    PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    PRESTART_HANDOFF_ENABLED=True,
    LEGACY_FORCE_AUTH_COMPAT=False,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    REQUIRE_RECHARGE_PROOF=True,
    KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False,
    CELERY_TASK_ALWAYS_EAGER=False,
    CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}},
)


@override_settings(**BREAKDOWN_SETTINGS)
class SettlementBreakdownTest(APITransactionTestCase):
    setUp = addon_fixture.SegmentAddonSettlementTest.setUp
    login = addon_fixture.SegmentAddonSettlementTest.login
    ok = addon_fixture.SegmentAddonSettlementTest.ok
    recharge = addon_fixture.SegmentAddonSettlementTest.recharge
    new_order = addon_fixture.SegmentAddonSettlementTest.new_order
    replacement = addon_fixture.SegmentAddonSettlementTest.replacement
    transfer = addon_fixture.SegmentAddonSettlementTest.transfer
    prepare = addon_fixture.SegmentAddonSettlementTest.prepare
    act = addon_fixture.SegmentAddonSettlementTest.act
    review = addon_fixture.SegmentAddonSettlementTest.review
    finished_order = addon_fixture.SegmentAddonSettlementTest.finished_order
    submit_and_approve = addon_fixture.SegmentAddonSettlementTest.submit_and_approve
    mature = addon_fixture.SegmentAddonSettlementTest.mature
    settle = addon_fixture.SegmentAddonSettlementTest.settle
    completed_addon_after_transfer = addon_fixture.SegmentAddonSettlementTest.completed_addon_after_transfer

    def url(self, order):
        return f'/api/orders/orders/{order.pk}/settlement-breakdown/'

    def assert_breakdown_contract(self, data):
        self.assertIsInstance(data['unknown_items'], list)
        self.assertIsInstance(data['totals']['unknown'], bool)
        self.assertIsInstance(data['person_settlement']['unknown_items'], list)
        for line in data['main_segments'] + data['addon_earnings']:
            self.assertIsInstance(line['unknown_items'], list)

    def read(self, order, user):
        self.login(user)
        response = self.client.get(self.url(order))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['code'], 0, response.data)
        self.assertEqual(response.data['data']['order_id'], order.pk)
        self.assert_breakdown_contract(response.data['data'])
        return response.data['data']

    def settle_finished(self, order):
        # Keep this read-contract suite independent of the separate formal
        # deposit-account-cycle rollout; provider income itself is unchanged.
        order.service_deposit_percent = 0
        order.save(update_fields=['service_deposit_percent'])
        self.mature(order)
        with store_context(Store.objects.get(slug='shenglang')):
            return self.settle(order)

    def save_plan(self, manifest, plan):
        manifest.plan = plan
        manifest.plan_hash = hashlib.sha256(json.dumps(
            plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        manifest.save(update_fields=['plan', 'plan_hash'])

    def settled_main(self):
        order, replacement, old, current = self.finished_order()
        self.submit_and_approve(order, old, self.provider)
        self.assertCountEqual(self.settle_finished(order), [self.provider.pk])
        return order, replacement, old, current

    def staff_client(self, permissions):
        staff = make_console_user(permissions)
        account = get_or_create_account_for_legacy_user(staff)
        link_legacy_relations(staff, account)
        AdminMembership.objects.filter(user=staff).update(account=account)
        token, _ = issue_account_tokens(account)
        return APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)

    def staff_url(self, order):
        return f'/api/admin/orders/{order.pk}/settlement-breakdown/'

    def test_staff_reads_all_participating_providers_without_writing_business_facts(self):
        order, replacement, old, current = self.finished_order()
        self.submit_and_approve(order, old, self.provider)
        self.submit_and_approve(order, current, replacement)
        self.assertCountEqual(self.settle_finished(order), [self.provider.pk, replacement.pk])
        client = self.staff_client(['order:view', 'report:view'])

        with CaptureQueriesContext(connection) as queries:
            response = client.get(self.staff_url(order))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response['Cache-Control'], 'no-store')
        data = response.data['data']
        self.assertEqual(data['scope'], 'STAFF_ORDER')
        self.assertIsInstance(data['unknown_items'], list)
        for provider in data['providers']:
            self.assert_breakdown_contract(provider)
        self.assertEqual([row['current_provider']['account_id'] for row in data['providers']],
                         [self.accounts[self.provider.pk].pk, self.accounts[replacement.pk].pk])
        self.assertEqual([[line['segment_id'] for line in row['main_segments']] for row in data['providers']],
                         [[old.pk], [current.pk]])
        self.assertEqual([row['totals']['posted_income_raw'] for row in data['providers']], [3200, 4800])
        writes = [query['sql'] for query in queries if query['sql'].lstrip().upper()
                  .startswith(('INSERT', 'UPDATE', 'DELETE', 'REPLACE'))]
        self.assertEqual(writes, [])

    def test_staff_contract_requires_both_permissions_and_never_opens_provider_endpoint(self):
        order = self.new_order()
        url = self.staff_url(order)
        for permissions in ([], ['order:view'], ['report:view']):
            with self.subTest(permissions=permissions):
                self.assertEqual(self.staff_client(permissions).get(url).status_code, 403)
        self.assertEqual(APIClient(HTTP_HOST='admin.esports.club').get(url).status_code, 401)
        self.login(self.provider)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get(url, HTTP_HOST='admin.esports.club').status_code, 401)
        self.login(self.boss)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get(url, HTTP_HOST='admin.esports.club').status_code, 401)

        staff = self.staff_client(['order:view', 'report:view'])
        missing = staff.get(f'/api/admin/orders/{order.pk + 1000000}/settlement-breakdown/')
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(staff.post(url, {}, format='json').status_code, 405)
        self.assertEqual(staff.get(self.url(order), HTTP_HOST='admin.esports.club').status_code, 403)

    def test_staff_contract_marks_unlinked_historical_provider_as_unknown(self):
        order = self.new_order()
        legacy_provider = make_user(role='PROVIDER', nickname='未关联账号的历史陪玩')
        OrderProvider.objects.create(order=order, provider=legacy_provider,
                                     provider_name_snapshot=legacy_provider.nickname)
        response = self.staff_client(['order:view', 'report:view']).get(self.staff_url(order))
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data['data']
        self.assertEqual([row['current_provider']['account_id'] for row in data['providers']],
                         [self.accounts[self.provider.pk].pk])
        self.assertEqual(data['unknown_items'][0]['code'], 'PROVIDER_ACCOUNT_UNKNOWN')

    def test_staff_does_not_treat_mapped_addon_account_id_as_legacy_user_id(self):
        order, replacement, old, segment, payment_id = self.completed_addon_after_transfer()
        legacy_provider = make_user(role='PROVIDER', nickname='独立编号加购陪玩')
        addon_account = ClubAccount.objects.create_account(
            id=880001,
            username='addon-provider-880001',
            account_type=ClubAccount.AccountType.PROVIDER,
            nickname='独立编号加购陪玩',
            account_no='P880001',
        )
        LegacyAccountMap.objects.create(
            legacy_user_id=legacy_provider.pk,
            account=addon_account,
            legacy_role='PROVIDER',
        )
        payment = OrderAddonPayment.objects.select_related('intent').get(pk=payment_id)
        payment.intent.provider = addon_account
        payment.intent.save(update_fields=['provider'])
        OrderProvider.objects.create(
            order=order,
            provider=legacy_provider,
            provider_account=addon_account,
            provider_name_snapshot=legacy_provider.nickname,
        )

        response = self.staff_client(['order:view', 'report:view']).get(self.staff_url(order))
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data['data']
        self.assertNotIn('PROVIDER_ACCOUNT_UNKNOWN', {row['code'] for row in data['unknown_items']})
        self.assertIn(addon_account.pk,
                      {row['current_provider']['account_id'] for row in data['providers']})

    def test_single_provider_reads_verified_main_income_and_transaction(self):
        order, replacement, old, current = self.finished_order()
        self.submit_and_approve(order, old, self.provider)
        self.assertCountEqual(self.settle_finished(order), [self.provider.pk])

        data = self.read(order, self.provider)
        self.assertEqual(data['current_provider']['account_id'], self.accounts[self.provider.pk].pk)
        self.assertEqual(len(data['main_segments']), 1)
        line = data['main_segments'][0]
        self.assertEqual(line['segment_id'], old.pk)
        self.assertEqual(line['main_net_income_raw'], 3200)
        self.assertEqual(line['pending_income_raw'], 0)
        self.assertEqual(line['frozen_income_raw'], 0)
        self.assertEqual(line['posted_income_raw'], 3200)
        self.assertEqual(line['settlement_status'], 'POSTED')
        self.assertEqual(line['transactions'][0]['tx_type'], 'INCOME')
        self.assertEqual(data['totals']['main_net_income_raw'], 3200)
        self.assertEqual(data['totals']['addon_net_income_raw'], 0)
        self.assertEqual(data['totals']['net_income_raw'], 3200)
        self.assertEqual(data['unknown_items'], [])

    def test_unpaid_person_with_current_manifest_is_pending_not_unknown_or_zero_net(self):
        order, replacement, old, current = self.finished_order()
        self.submit_and_approve(order, old, self.provider)
        self.assertCountEqual(self.settle_finished(order), [self.provider.pk])

        data = self.read(order, replacement)
        line = data['main_segments'][0]
        self.assertEqual(line['main_net_income_raw'], 4800)
        self.assertEqual(line['pending_income_raw'], 4800)
        self.assertEqual(line['posted_income_raw'], 0)
        self.assertEqual(line['frozen_income_raw'], 0)
        self.assertEqual(line['settlement_status'], 'PENDING')
        self.assertEqual(data['totals']['net_income_raw'], 4800)
        self.assertEqual(data['unknown_items'], [])

    def test_two_providers_see_only_their_own_segment_and_income(self):
        order, replacement, old, current = self.finished_order()
        self.submit_and_approve(order, old, self.provider)
        self.submit_and_approve(order, current, replacement)
        self.assertCountEqual(self.settle_finished(order), [self.provider.pk, replacement.pk])

        first = self.read(order, self.provider)
        second = self.read(order, replacement)
        self.assertEqual([row['segment_id'] for row in first['main_segments']], [old.pk])
        self.assertEqual([row['segment_id'] for row in second['main_segments']], [current.pk])
        self.assertEqual(first['totals']['net_income_raw'], 3200)
        self.assertEqual(second['totals']['net_income_raw'], 4800)
        self.assertNotEqual(first['person_settlement']['income_transaction']['id'],
                            second['person_settlement']['income_transaction']['id'])
        stranger = self.replacement('无关陪玩')
        self.login(stranger)
        self.assertEqual(self.client.get(self.url(order)).status_code, 404)

    def test_double_escort_order_returns_each_providers_own_verified_share(self):
        replacement = self.replacement('双陪队友')
        now = timezone.now()
        order = Order.objects.create(customer_account=self.accounts[self.boss.pk],
            provider_account=self.accounts[self.provider.pk], service=self.service,
            amount=10000, original_amount=10000, status=Order.Status.COMPLETED,
            payment_status=Order.PaymentStatus.PAID, escort_mode=Order.EscortMode.DOUBLE,
            required_provider_count=2, provider_income=8000, settled_at=now, completed_at=now)
        incomes = []
        for user in (self.provider, replacement):
            account = self.accounts[user.pk]
            OrderProvider.objects.create(order=order, provider=user, provider_account=account,
                settlement_base=5000, provider_income=4000, settled_at=now)
            wallet = Wallet.objects.update_or_create(account=account, defaults={'balance': 4000})[0]
            incomes.append(Transaction.objects.create(wallet=wallet, order=order, amount=4000,
                tx_type=Transaction.TxType.INCOME, balance_before=0, balance_after=4000,
                remark=f'订单收入：{order.order_no}'))
        first = self.read(order, self.provider)
        second = self.read(order, replacement)
        self.assertEqual(first['totals']['net_income_raw'], 4000)
        self.assertEqual(second['totals']['net_income_raw'], 4000)
        self.assertEqual(first['person_settlement']['income_transaction']['id'], incomes[0].pk)
        self.assertEqual(second['person_settlement']['income_transaction']['id'], incomes[1].pk)
        self.assertEqual(first['main_segments'][0]['segment_id'], None)
        self.assertEqual(second['main_segments'][0]['segment_id'], None)

    def test_returned_provider_keeps_distinct_segments_without_inventing_posted_split(self):
        order, replacement, old = self.prepare()
        middle = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[replacement.pk], released_at__isnull=True)
        self.login(replacement)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id': middle.pk, 'entry_image': flow.screenshot()}, format='multipart'))
        self.ok(self.transfer(order.pk, replacement, self.provider, original_share={
            'retained': 2000, 'transferred': 4000, 'agreement': '双方确认转回'}))
        returned = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[self.provider.pk], released_at__isnull=True)
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id': returned.pk, 'entry_image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/',
            {'completion_image': flow.screenshot()}, format='multipart'))
        self.login(replacement)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-evidence/',
            {'segment_id': middle.pk, 'completion_image': flow.screenshot()}, format='multipart'))
        order.refresh_from_db()
        self.submit_and_approve(order, old, self.provider)
        self.submit_and_approve(order, middle, replacement)
        self.submit_and_approve(order, returned, self.provider)
        self.assertCountEqual(self.settle_finished(order), [self.provider.pk, replacement.pk])

        data = self.read(order, self.provider)
        self.assertEqual([row['segment_id'] for row in data['main_segments']], [old.pk, returned.pk])
        self.assertEqual([row['main_net_income_raw'] for row in data['main_segments']], [3200, 3200])
        self.assertEqual(data['person_settlement']['main_net_income_raw'], 6400)
        self.assertEqual(data['person_settlement']['posted_income_raw'], 6400)
        self.assertTrue(all(row['posted_income_raw'] is None for row in data['main_segments']))
        self.assertIn('SEGMENT_POSTED_UNKNOWN', {row['code'] for row in data['unknown_items']})
        self.assertEqual(data['totals']['posted_income_raw'], 6400)

    def test_main_and_each_addon_payment_are_separate_with_real_transaction_refs(self):
        order, replacement, old, segment, payment_id = self.completed_addon_after_transfer()
        self.submit_and_approve(order, old, self.provider)
        self.submit_and_approve(order, segment, replacement)
        self.assertCountEqual(self.settle_finished(order), [self.provider.pk, replacement.pk])

        data = self.read(order, replacement)
        self.assertEqual(len(data['main_segments']), 1)
        self.assertEqual(len(data['addon_earnings']), 1)
        addon = data['addon_earnings'][0]
        self.assertEqual((addon['payment_id'], addon['segment_id']), (payment_id, segment.pk))
        self.assertEqual(addon['addon_name'], '转单后加购')
        self.assertEqual(addon['net_income_raw'], 4000)
        self.assertEqual(addon['posted_income_raw'], 4000)
        self.assertEqual(data['totals']['main_net_income_raw'], 4800)
        self.assertEqual(data['totals']['addon_net_income_raw'], 4000)
        self.assertEqual(data['totals']['net_income_raw'], 8800)
        main_tx = data['main_segments'][0]['transactions'][0]['id']
        addon_tx = addon['transactions'][0]['id']
        self.assertNotEqual(main_tx, addon_tx)
        self.assertEqual({row['id'] for row in data['person_settlement']['transactions']}, {main_tx, addon_tx})

    def test_frozen_then_released_income_uses_hold_and_real_release_transaction(self):
        order, replacement, old, current = self.finished_order()
        order.service_deposit_percent = 0
        order.save(update_fields=['service_deposit_percent'])
        self.submit_and_approve(order, old, self.provider)
        self.assertCountEqual(self.settle_finished(order), [self.provider.pk])
        income = Transaction.objects.get(order=order, tx_type='INCOME',
            wallet__account=self.accounts[self.provider.pk])
        with store_context(Store.objects.get(slug='shenglang')), transaction.atomic():
            from wallet.income import hold_provider_income
            hold = hold_provider_income(income, profile=self.provider.escort_profile,
                hold_hours=24, deposit_percent=0, apply_without_hold=True)
            self.assertIsNotNone(hold)

        frozen = self.read(order, self.provider)
        self.assertEqual(frozen['main_segments'][0]['settlement_status'], 'FROZEN')
        self.assertEqual(frozen['main_segments'][0]['posted_income_raw'], 3200)
        self.assertEqual(frozen['main_segments'][0]['frozen_income_raw'], 3200)
        self.assertEqual(frozen['totals']['frozen_income_raw'], 3200)
        self.assertEqual({row['tx_type'] for row in frozen['main_segments'][0]['transactions']},
                         {'INCOME', 'EARNINGS_FREEZE'})

        IncomeHold.objects.filter(pk=hold.pk).update(release_at=timezone.now() - timedelta(minutes=1))
        with store_context(Store.objects.get(slug='shenglang')), transaction.atomic():
            from wallet.income import release_due_income
            self.assertEqual(release_due_income(now=timezone.now()), 1)
        released = self.read(order, self.provider)
        self.assertEqual(released['main_segments'][0]['settlement_status'], 'POSTED')
        self.assertEqual(released['main_segments'][0]['frozen_income_raw'], 0)
        self.assertEqual(released['main_segments'][0]['posted_income_raw'], 3200)
        self.assertIn('EARNINGS_RELEASE', {row['tx_type'] for row in released['main_segments'][0]['transactions']})

    def test_missing_income_hold_with_freeze_flow_keeps_frozen_unknown(self):
        order, replacement, old, current = self.settled_main()
        account = self.accounts[self.provider.pk]
        income = Transaction.objects.get(order=order, tx_type=Transaction.TxType.INCOME,
            wallet__account=account)
        Transaction.objects.create(wallet=income.wallet, order=order, amount=-income.amount,
            tx_type=Transaction.TxType.EARNINGS_FREEZE, balance_before=income.amount,
            balance_after=0, remark='缺少冻结记录的收益冻结流水')
        self.assertFalse(IncomeHold.objects.filter(income_transaction=income).exists())

        data = self.read(order, self.provider)
        line = data['main_segments'][0]
        self.assertIsNone(line['frozen_income_raw'])
        self.assertEqual(line['settlement_status'], 'REVIEW')
        self.assertEqual(data['status'], 'REVIEW')
        self.assertIsNone(data['totals']['frozen_income_raw'])
        self.assertIn('INCOME_HOLD_MISSING', {row['code'] for row in data['unknown_items']})

    def test_missing_income_hold_with_release_flow_keeps_frozen_unknown(self):
        order, replacement, old, current = self.settled_main()
        account = self.accounts[self.provider.pk]
        income = Transaction.objects.get(order=order, tx_type=Transaction.TxType.INCOME,
            wallet__account=account)
        Transaction.objects.create(wallet=income.wallet, order=order, amount=income.amount,
            tx_type=Transaction.TxType.EARNINGS_RELEASE, balance_before=0,
            balance_after=income.amount, remark='缺少冻结记录的收益解冻流水')
        self.assertFalse(IncomeHold.objects.filter(income_transaction=income).exists())

        data = self.read(order, self.provider)
        line = data['main_segments'][0]
        self.assertIsNone(line['frozen_income_raw'])
        self.assertEqual(line['settlement_status'], 'REVIEW')
        self.assertEqual(data['status'], 'REVIEW')
        self.assertIsNone(data['totals']['frozen_income_raw'])
        self.assertIn('INCOME_HOLD_MISSING', {row['code'] for row in data['unknown_items']})

    def test_corrupt_manifest_keeps_posted_fact_but_marks_net_unknown(self):
        order, replacement, old, current = self.finished_order()
        self.submit_and_approve(order, old, self.provider)
        self.assertCountEqual(self.settle_finished(order), [self.provider.pk])
        manifest = OrderSegmentSettlementManifest.objects.get(order=order)
        manifest.plan_hash = '0' * 64
        manifest.save(update_fields=['plan_hash'])

        data = self.read(order, self.provider)
        line = data['main_segments'][0]
        self.assertIsNone(line['main_net_income_raw'])
        self.assertIsNone(data['totals']['main_net_income_raw'])
        self.assertIsNone(data['totals']['net_income_raw'])
        self.assertEqual(line['posted_income_raw'], 3200)
        self.assertEqual(data['totals']['posted_income_raw'], 3200)
        self.assertIn('MANIFEST_HASH_MISMATCH', {row['code'] for row in data['unknown_items']})

    def test_foreign_main_income_reference_is_not_exposed(self):
        order, replacement, old, current = self.settled_main()
        account = self.accounts[self.provider.pk]
        foreign_account = self.accounts[replacement.pk]
        foreign_wallet = Wallet.objects.create(account=foreign_account, balance=3200)
        foreign_income = Transaction.objects.create(wallet=foreign_wallet, order=order, amount=3200,
            tx_type=Transaction.TxType.INCOME, balance_before=0, balance_after=3200,
            remark=f'订单收入：{order.order_no}')
        manifest = OrderSegmentSettlementManifest.objects.get(order=order)
        settlement = OrderSegmentPersonSettlement.objects.get(manifest=manifest, account=account)
        settlement.income_transaction = foreign_income
        settlement.save(update_fields=['income_transaction'])

        data = self.read(order, self.provider)
        self.assertIsNone(data['main_segments'][0]['posted_income_raw'])
        self.assertIsNone(data['person_settlement']['income_transaction'])
        self.assertNotIn(foreign_income.pk, {row['id'] for row in data['transaction_refs']})
        self.assertEqual(data['status'], 'REVIEW')
        self.assertIn('PERSON_INCOME_TRANSACTION_INVALID',
                      {row['code'] for row in data['unknown_items']})

    def test_corrupt_manifest_marks_addon_net_unknown_but_keeps_verified_credit(self):
        order, replacement, old, segment, payment_id = self.completed_addon_after_transfer()
        self.submit_and_approve(order, old, self.provider)
        self.submit_and_approve(order, segment, replacement)
        self.assertCountEqual(self.settle_finished(order), [self.provider.pk, replacement.pk])
        manifest = OrderSegmentSettlementManifest.objects.get(order=order)
        manifest.plan_hash = '0' * 64
        manifest.save(update_fields=['plan_hash'])

        data = self.read(order, replacement)
        addon = data['addon_earnings'][0]
        self.assertIsNone(addon['net_income_raw'])
        self.assertIsNone(addon['pending_income_raw'])
        self.assertEqual(addon['posted_income_raw'], 4000)
        self.assertEqual(addon['frozen_income_raw'], 0)
        self.assertEqual(addon['settlement_status'], 'REVIEW')
        self.assertIsNone(data['totals']['addon_net_income_raw'])
        self.assertIsNone(data['totals']['net_income_raw'])
        self.assertEqual(data['totals']['posted_income_raw'], 8800)
        self.assertIn('MANIFEST_HASH_MISMATCH', {row['code'] for row in data['unknown_items']})

    def test_duplicate_addon_plan_parts_do_not_inflate_totals(self):
        order, replacement, old, segment, payment_id = self.completed_addon_after_transfer()
        self.submit_and_approve(order, old, self.provider)
        self.submit_and_approve(order, segment, replacement)
        self.assertCountEqual(self.settle_finished(order), [self.provider.pk, replacement.pk])
        manifest = OrderSegmentSettlementManifest.objects.get(order=order)
        plan = deepcopy(manifest.plan)
        funding = plan.get('points_funding') or plan.get('ordinary_transfer_funding')
        part = next(row for row in funding['addons'] if row['payment_id'] == payment_id)
        funding['addons'].append(deepcopy(part))
        self.save_plan(manifest, plan)

        data = self.read(order, replacement)
        self.assertEqual(len(data['addon_earnings']), 2)
        self.assertIsNone(data['totals']['addon_net_income_raw'])
        self.assertIsNone(data['totals']['posted_income_raw'])
        self.assertIsNone(data['totals']['pending_income_raw'])
        self.assertIsNone(data['totals']['frozen_income_raw'])
        self.assertEqual(data['status'], 'REVIEW')
        self.assertIn('ADDON_PART_DUPLICATE', {row['code'] for row in data['unknown_items']})
        self.assertTrue(all(row['settlement_status'] == 'REVIEW' for row in data['addon_earnings']))

    def test_unsettled_provider_has_known_zero_posted_and_pending_net(self):
        order, replacement, old, current = self.finished_order()
        self.submit_and_approve(order, old, self.provider)
        self.assertCountEqual(self.settle_finished(order), [self.provider.pk])

        data = self.read(order, replacement)
        line = data['main_segments'][0]
        self.assertEqual((line['main_net_income_raw'], line['pending_income_raw'],
                          line['frozen_income_raw'], line['posted_income_raw']), (4800, 4800, 0, 0))
        self.assertEqual(line['settlement_status'], 'PENDING')
        self.assertEqual(data['status'], 'PENDING')
        self.assertEqual(data['totals']['unknown'], False)
        self.assertEqual(data['unknown_items'], [])

    def test_verified_zero_income_is_posted_not_pending_or_unknown(self):
        order, replacement, old, current = self.settled_main()
        account = self.accounts[self.provider.pk]
        manifest = OrderSegmentSettlementManifest.objects.get(order=order)
        settlement = OrderSegmentPersonSettlement.objects.get(manifest=manifest, account=account)
        income = settlement.income_transaction
        plan = deepcopy(manifest.plan)
        person = next(row for row in plan['people'] if row['account_id'] == account.pk)
        person['income'] = 0
        segment = next(row for row in plan['segments']
                       if row['segment_id'] == old.pk and row['account_id'] == account.pk)
        segment['income'] = 0
        self.save_plan(manifest, plan)
        settlement.amount = 0
        settlement.income_transaction = None
        settlement.save(update_fields=['amount', 'income_transaction'])
        income.delete()

        data = self.read(order, self.provider)
        line = data['main_segments'][0]
        self.assertEqual((line['main_net_income_raw'], line['pending_income_raw'],
                          line['frozen_income_raw'], line['posted_income_raw']), (0, 0, 0, 0))
        self.assertEqual(line['settlement_status'], 'POSTED')
        self.assertEqual(data['status'], 'POSTED')
        self.assertEqual(data['totals']['net_income_raw'], 0)
        self.assertEqual(data['unknown_items'], [])

    def test_malformed_manifest_values_return_review_without_server_error(self):
        order, replacement, old, current = self.settled_main()
        account_id = self.accounts[self.provider.pk].pk
        manifest = OrderSegmentSettlementManifest.objects.get(order=order)
        original = deepcopy(manifest.plan)

        mutations = []
        plan = deepcopy(original)
        next(row for row in plan['people'] if row['account_id'] == account_id)['segment_ids'] = old.pk
        mutations.append(('person_ids', plan))
        plan = deepcopy(original)
        next(row for row in plan['people'] if row['account_id'] == account_id)['income'] = True
        mutations.append(('boolean_income', plan))
        plan = deepcopy(original)
        next(row for row in plan['people'] if row['account_id'] == account_id)['income'] = 2 ** 63
        mutations.append(('oversized_income', plan))
        plan = deepcopy(original)
        next(row for row in plan['segments'] if row['segment_id'] == old.pk)['income'] = -1
        mutations.append(('negative_income', plan))
        plan = deepcopy(original)
        plan['points_funding'] = []
        mutations.append(('funding_list', plan))
        plan = deepcopy(original)
        plan['points_funding'] = {'addons': [{
            'provider_account_id': account_id, 'payment_id': [], 'segment_id': old.pk,
            'provider_income_units': 0,
        }]}
        mutations.append(('payment_list', plan))

        for name, plan in mutations:
            with self.subTest(name=name):
                self.save_plan(manifest, plan)
                data = self.read(order, self.provider)
                self.assertEqual(data['status'], 'REVIEW')
                self.assertTrue(data['unknown_items'])

    def test_extra_main_income_without_receipt_makes_total_posted_unknown(self):
        order, replacement, old, current = self.settled_main()
        account = self.accounts[self.provider.pk]
        wallet = Wallet.objects.get(account=account)
        extra = Transaction.objects.create(wallet=wallet, order=order, amount=3200,
            tx_type=Transaction.TxType.INCOME, balance_before=3200, balance_after=6400,
            remark='同订单人工收益流水')

        data = self.read(order, self.provider)
        self.assertEqual(data['main_segments'][0]['posted_income_raw'], 3200)
        self.assertIsNone(data['totals']['posted_income_raw'])
        self.assertIsNone(data['totals']['pending_income_raw'])
        self.assertIsNone(data['totals']['frozen_income_raw'])
        self.assertEqual(data['status'], 'REVIEW')
        self.assertIn('INCOME_TRANSACTION_UNRECONCILED', {row['code'] for row in data['unknown_items']})
        self.assertIn(extra.pk, {row['id'] for row in data['transaction_refs']})

    def test_shared_main_and_addon_income_transaction_is_not_counted_twice(self):
        order, replacement, old, segment, payment_id = self.completed_addon_after_transfer()
        self.submit_and_approve(order, old, self.provider)
        self.submit_and_approve(order, segment, replacement)
        self.assertCountEqual(self.settle_finished(order), [self.provider.pk, replacement.pk])
        from orders.models import OrderSegmentAddonSettlement
        account = self.accounts[replacement.pk]
        manifest = OrderSegmentSettlementManifest.objects.get(order=order)
        settlement = OrderSegmentPersonSettlement.objects.get(manifest=manifest, account=account)
        addon_income = OrderSegmentAddonSettlement.objects.get(payment_id=payment_id).income_transaction
        plan = deepcopy(manifest.plan)
        person = next(row for row in plan['people'] if row['account_id'] == account.pk)
        person['income'] = addon_income.amount
        segment_plan = next(row for row in plan['segments']
                            if row['segment_id'] == segment.pk and row['account_id'] == account.pk)
        segment_plan['income'] = addon_income.amount
        self.save_plan(manifest, plan)
        old_main_income = settlement.income_transaction
        settlement.amount = addon_income.amount
        settlement.income_transaction = addon_income
        settlement.save(update_fields=['amount', 'income_transaction'])
        old_main_income.delete()

        data = self.read(order, replacement)
        self.assertEqual(data['totals']['net_income_raw'], 8000)
        self.assertIsNone(data['totals']['posted_income_raw'])
        self.assertIsNone(data['totals']['pending_income_raw'])
        self.assertIsNone(data['totals']['frozen_income_raw'])
        self.assertEqual(data['status'], 'REVIEW')
        self.assertIn('INCOME_TRANSACTION_SHARED',
                      {row['code'] for row in data['unknown_items']})
        for line in data['main_segments'] + data['addon_earnings']:
            self.assertEqual(line['settlement_status'], 'REVIEW')
            self.assertIsNone(line['posted_income_raw'])
        self.assertEqual([row['id'] for row in data['transaction_refs']], [addon_income.pk])

    def test_missing_addon_receipt_does_not_turn_posted_into_zero(self):
        order, replacement, old, segment, payment_id = self.completed_addon_after_transfer()
        self.submit_and_approve(order, old, self.provider)
        self.submit_and_approve(order, segment, replacement)
        self.assertCountEqual(self.settle_finished(order), [self.provider.pk, replacement.pk])
        from orders.models import OrderSegmentAddonSettlement
        OrderSegmentAddonSettlement.objects.filter(payment_id=payment_id).delete()

        data = self.read(order, replacement)
        addon = data['addon_earnings'][0]
        self.assertEqual(addon['net_income_raw'], 4000)
        self.assertIsNone(addon['posted_income_raw'])
        self.assertIsNone(addon['pending_income_raw'])
        self.assertEqual(addon['settlement_status'], 'REVIEW')
        self.assertEqual(data['totals']['addon_net_income_raw'], 4000)
        self.assertIsNone(data['totals']['posted_income_raw'])
        self.assertIn('ADDON_SETTLEMENT_MISSING', {row['code'] for row in data['unknown_items']})

    def test_missing_addon_and_person_receipts_keeps_addon_credit_unknown(self):
        order, replacement, old, segment, payment_id = self.completed_addon_after_transfer()
        self.submit_and_approve(order, old, self.provider)
        self.submit_and_approve(order, segment, replacement)
        self.assertCountEqual(self.settle_finished(order), [self.provider.pk, replacement.pk])
        from orders.models import OrderSegmentAddonSettlement, OrderSegmentPersonSettlement
        account = self.accounts[replacement.pk]
        OrderSegmentAddonSettlement.objects.filter(payment_id=payment_id).delete()
        OrderSegmentPersonSettlement.objects.filter(
            manifest__order=order, account=account,
        ).delete()

        data = self.read(order, replacement)
        addon = data['addon_earnings'][0]
        self.assertEqual(addon['net_income_raw'], 4000)
        self.assertIsNone(addon['posted_income_raw'])
        self.assertIsNone(addon['pending_income_raw'])
        self.assertIsNone(addon['frozen_income_raw'])
        self.assertEqual(addon['settlement_status'], 'REVIEW')
        self.assertEqual(data['totals']['addon_net_income_raw'], 4000)
        self.assertIsNone(data['totals']['posted_income_raw'])
        self.assertIsNone(data['totals']['pending_income_raw'])
        codes = {row['code'] for row in data['unknown_items']}
        self.assertIn('PERSON_SETTLEMENT_MISSING', codes)
        self.assertIn('ADDON_SETTLEMENT_MISSING', codes)
        self.assertIn('INCOME_TRANSACTION_UNRECONCILED', codes)

    def test_corrupt_addon_identity_does_not_expose_foreign_payment_or_credit(self):
        order, replacement, old, segment, payment_id = self.completed_addon_after_transfer()
        self.submit_and_approve(order, old, self.provider)
        self.submit_and_approve(order, segment, replacement)
        self.assertCountEqual(self.settle_finished(order), [self.provider.pk, replacement.pk])
        payment = OrderAddonPayment.objects.get(pk=payment_id)
        payment.intent.provider = self.accounts[self.provider.pk]
        payment.intent.save(update_fields=['provider'])
        manifest = OrderSegmentSettlementManifest.objects.get(order=order)
        plan = deepcopy(manifest.plan)
        funding = plan.get('points_funding') or plan.get('ordinary_transfer_funding')
        part = next(row for row in funding['addons'] if row['payment_id'] == payment_id)
        part['segment_id'] = old.pk
        self.save_plan(manifest, plan)

        data = self.read(order, replacement)
        self.assertEqual(len(data['addon_earnings']), 1)
        addon = data['addon_earnings'][0]
        self.assertIsNone(addon['segment_id'])
        self.assertIsNone(addon['payment_id'])
        self.assertIsNone(addon['addon_name'])
        self.assertIsNone(addon['posted_income_raw'])
        self.assertEqual(addon['transactions'], [])
        self.assertNotIn(str(payment_id), addon['line_id'])
        self.assertNotIn(str(old.pk), addon['line_id'])
        self.assertTrue(all(row['segment_id'] is None for row in addon['unknown_items']),
                        addon['unknown_items'])
        self.assertIn('ADDON_PART_INVALID', {row['code'] for row in data['unknown_items']})

    def test_cancelled_hold_with_release_reference_is_unknown_not_zero_frozen(self):
        order, replacement, old, current = self.finished_order()
        order.service_deposit_percent = 0
        order.save(update_fields=['service_deposit_percent'])
        self.submit_and_approve(order, old, self.provider)
        self.assertCountEqual(self.settle_finished(order), [self.provider.pk])
        income = Transaction.objects.get(order=order, tx_type='INCOME',
            wallet__account=self.accounts[self.provider.pk])
        with store_context(Store.objects.get(slug='shenglang')), transaction.atomic():
            from wallet.income import hold_provider_income
            hold = hold_provider_income(income, profile=self.provider.escort_profile,
                hold_hours=24, deposit_percent=0, apply_without_hold=True)
        wallet = income.wallet
        release = Transaction.objects.create(wallet=wallet, order=order, amount=income.amount,
            tx_type=Transaction.TxType.EARNINGS_RELEASE, balance_before=0, balance_after=income.amount)
        hold.cancelled_at = timezone.now()
        hold.release_transaction = release
        hold.save(update_fields=['cancelled_at', 'release_transaction'])

        data = self.read(order, self.provider)
        self.assertIsNone(data['main_segments'][0]['frozen_income_raw'])
        self.assertEqual(data['status'], 'REVIEW')
        self.assertIn('INCOME_HOLD_STATE_INVALID', {row['code'] for row in data['unknown_items']})

    def test_successful_read_executes_only_selects_and_preserves_financial_rows(self):
        order, replacement, old, current = self.settled_main()
        order.refresh_from_db()
        before = (
            Order.objects.filter(pk=order.pk).values_list('updated_at', flat=True).get(),
            Transaction.objects.filter(order=order).count(),
            Wallet.objects.filter(account=self.accounts[self.provider.pk]).values_list('balance', flat=True).get(),
            IncomeHold.objects.filter(income_transaction__order=order).count(),
            OrderSegmentPersonSettlement.objects.filter(manifest__order=order).count(),
        )
        self.login(self.provider)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url(order))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(queries.captured_queries)
        read_only_prefixes = ('SELECT', 'BEGIN', 'COMMIT', 'SAVEPOINT', 'RELEASE', 'ROLLBACK')
        self.assertTrue(all(query['sql'].lstrip().upper().startswith(read_only_prefixes)
                            for query in queries.captured_queries),
                        [query['sql'] for query in queries.captured_queries])
        order.refresh_from_db()
        after = (
            Order.objects.filter(pk=order.pk).values_list('updated_at', flat=True).get(),
            Transaction.objects.filter(order=order).count(),
            Wallet.objects.filter(account=self.accounts[self.provider.pk]).values_list('balance', flat=True).get(),
            IncomeHold.objects.filter(income_transaction__order=order).count(),
            OrderSegmentPersonSettlement.objects.filter(manifest__order=order).count(),
        )
        self.assertEqual(after, before)

    def test_invalid_order_ids_and_endpoint_responses_are_not_cacheable(self):
        order, replacement, old = self.prepare()
        self.login(self.provider)
        # The int URL converter rejects these before the view can add headers.
        for order_id in (-1, 'not-an-integer'):
            with self.subTest(order_id=order_id):
                response = self.client.get(f'/api/orders/orders/{order_id}/settlement-breakdown/')
                self.assertEqual(response.status_code, 404)
        for order_id in (0, 2 ** 63):
            with self.subTest(order_id=order_id):
                response = self.client.get(f'/api/orders/orders/{order_id}/settlement-breakdown/')
                self.assertEqual(response.status_code, 404, response.data)
                self.assertEqual(response['Cache-Control'], 'no-store')
        success = self.client.get(self.url(order))
        self.assertEqual(success.status_code, 200, success.data)
        self.assertEqual(success['Cache-Control'], 'no-store')
        missing_id = Order.objects.order_by('-pk').first().pk + 1000000
        missing = self.client.get(f'/api/orders/orders/{missing_id}/settlement-breakdown/')
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing['Cache-Control'], 'no-store')
        self.login(self.boss)
        denied = self.client.get(self.url(order))
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied['Cache-Control'], 'no-store')

    def test_boss_staff_and_anonymous_are_not_provider_readers(self):
        order, replacement, old = self.prepare()
        self.login(self.boss)
        self.assertEqual(self.client.get(self.url(order)).status_code, 403)
        self.login(self.admin)
        self.assertEqual(self.client.get(self.url(order)).status_code, 403)
        self.client.credentials()
        self.assertEqual(self.client.get(self.url(order)).status_code, 401)
        self.login(self.provider)
        self.assertEqual(self.client.post(self.url(order), {}, format='json').status_code, 405)


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(**BREAKDOWN_SETTINGS, ALLOWED_HOSTS=['*'])
class SettlementBreakdownStoresTest(APITransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def make_store_fact(self, store, host, amount, order_no):
        with store_context(store):
            provider = ClubAccount.objects.create_account(
                id=900001, username='same-provider', account_type='PROVIDER',
                nickname='同编号陪玩', account_no='P900001')
            boss = ClubAccount.objects.create_account(
                id=900002, username='same-boss', account_type='BOSS',
                nickname='同编号老板', account_no='B900002')
            service = make_service(name=f'隔离服务-{host}', price=10000)
            order = Order.objects.create(id=900001, order_no=order_no, customer_account=boss,
                provider_account=provider, service=service, amount=10000, original_amount=10000,
                status=Order.Status.COMPLETED, payment_status=Order.PaymentStatus.PAID,
                provider_income=amount, settled_at=timezone.now(), completed_at=timezone.now())
            OrderProvider.objects.create(order=order, provider_account=provider,
                settlement_base=10000, provider_income=amount, settled_at=timezone.now())
            wallet = Wallet.objects.create(account=provider, balance=amount)
            income = Transaction.objects.create(wallet=wallet, order=order, amount=amount,
                tx_type=Transaction.TxType.INCOME, balance_before=0, balance_after=amount,
                remark=f'订单收入：{order_no}')
            token = issue_account_tokens(provider)[0]
            return APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token), order, income, token

    def test_same_order_and_account_ids_are_isolated_and_cross_store_token_is_rejected(self):
        first_client, first_order, first_income, first_token = self.make_store_fact(
            resolve_store(), 'sl.esports.club', 3200, 'SL-SAME-001')
        second_client, second_order, second_income, _ = self.make_store_fact(
            xinan_store(), 'xa.esports.club', 4800, 'XA-SAME-001')
        self.assertEqual(first_order.pk, second_order.pk)
        url = f'/api/orders/orders/{first_order.pk}/settlement-breakdown/'

        first = first_client.get(url)
        second = second_client.get(url)
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(first.data['data']['store_slug'], 'shenglang')
        self.assertEqual(second.data['data']['store_slug'], 'xinan')
        self.assertEqual(first.data['data']['order_no'], 'SL-SAME-001')
        self.assertEqual(second.data['data']['order_no'], 'XA-SAME-001')
        self.assertEqual(first.data['data']['totals']['net_income_raw'], 3200)
        self.assertEqual(second.data['data']['totals']['net_income_raw'], 4800)
        self.assertEqual(first.data['data']['person_settlement']['income_transaction']['id'], first_income.pk)
        self.assertEqual(second.data['data']['person_settlement']['income_transaction']['id'], second_income.pk)

        crossed = APIClient(HTTP_HOST='xa.esports.club',
            HTTP_AUTHORIZATION='Bearer ' + first_token)
        self.assertEqual(crossed.get(url).status_code, 401)
