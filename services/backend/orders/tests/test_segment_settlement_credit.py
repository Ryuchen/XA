from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APITransactionTestCase
from stores.db import transaction

from orders.models import AfterSale, OrderSegmentPersonSettlement, OrderSegmentReportSubmission, OrderSegmentSettlementManifest
from orders.segment_settlement import settle_due_segment_people
from orders.settlement import SettlementError
from orders.tests import test_segment_settlement_plan as fixture
from wallet.models import Transaction
from wallet.services import get_wallet


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   SEGMENT_REPORT_ENABLED=True, SEGMENT_SETTLEMENT_ENABLED=True,
                   SEGMENT_STANDARD_TRANSFER_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
                   PRESTART_HANDOFF_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class SegmentSettlementCreditTest(APITransactionTestCase):
    setUp = fixture.SegmentSettlementPlanTest.setUp
    login = fixture.SegmentSettlementPlanTest.login
    ok = fixture.SegmentSettlementPlanTest.ok
    recharge = fixture.SegmentSettlementPlanTest.recharge
    new_order = fixture.SegmentSettlementPlanTest.new_order
    replacement = fixture.SegmentSettlementPlanTest.replacement
    transfer = fixture.SegmentSettlementPlanTest.transfer
    prepare = fixture.SegmentSettlementPlanTest.prepare
    act = fixture.SegmentSettlementPlanTest.act
    review = fixture.SegmentSettlementPlanTest.review
    finished_order = fixture.SegmentSettlementPlanTest.finished_order
    submit_and_approve = fixture.SegmentSettlementPlanTest.submit_and_approve

    def mature(self, order):
        now = timezone.now()
        if order.completed_at > now - timedelta(hours=72):
            order.completed_at = now - timedelta(hours=80)
            order.save(update_fields=['completed_at'])
        OrderSegmentReportSubmission.objects.filter(report__segment__attribution__order=order,
            submitted_at__gt=now-timedelta(hours=75)).update(
            submitted_at=now-timedelta(hours=79), reviewed_at=now-timedelta(hours=78))

    def settle(self, order):
        from orders.models import Order
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            return settle_due_segment_people(locked)

    def test_each_person_releases_once_then_order_finalizes(self):
        order, replacement, old, current = self.finished_order()
        self.submit_and_approve(order, old, self.provider)
        self.mature(order)
        self.assertEqual(self.settle(order), [self.provider.pk])
        self.assertEqual(self.settle(order), [])
        self.login(self.provider)
        own = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        self.assertEqual((own['expected_income'], own['settlement_status'], own['my_assignment_settled']),
                         (3200, 'SETTLED', True))
        self.assertEqual(OrderSegmentPersonSettlement.objects.filter(manifest__order=order).count(), 1)
        self.assertFalse(OrderSegmentSettlementManifest.objects.get(order=order).finalized_at)
        self.submit_and_approve(order, current, replacement)
        self.mature(order)
        self.assertEqual(self.settle(order), [replacement.pk])
        self.assertEqual(self.settle(order), [])
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.assertEqual((order.provider_income, order.shop_income), (8000, 2000))
        self.assertEqual(OrderSegmentPersonSettlement.objects.filter(manifest__order=order).count(), 2)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 2)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='SHOP_INCOME').count(), 1)
        self.login(self.provider)
        own = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        self.assertEqual((own['expected_income'], own['settlement_status']), (3200, 'SETTLED'))
        self.assertEqual(own['my_participation_segments'][0]['segment_report']['status'], 'APPROVED')

    def test_open_case_only_blocks_affected_person(self):
        order, replacement, old, current = self.finished_order()
        self.submit_and_approve(order, old, self.provider)
        self.submit_and_approve(order, current, replacement)
        self.mature(order)
        case = AfterSale.objects.create(order=order, account=order.customer_account, reason='核对',
            affected_provider_account_ids=[self.accounts[self.provider.pk].pk])
        self.assertEqual(self.settle(order), [replacement.pk])
        self.assertEqual(self.settle(order), [])
        case.status = 'RESOLVED'; case.save(update_fields=['status'])
        self.assertEqual(self.settle(order), [self.provider.pk])
        self.assertTrue(OrderSegmentSettlementManifest.objects.get(order=order).finalized_at)

    def test_unverifiable_payment_and_tampered_record_block_credit(self):
        order, replacement, old, current = self.finished_order()
        self.submit_and_approve(order, old, self.provider)
        self.mature(order)
        payment = Transaction.objects.get(order=order, tx_type='PAY')
        payment.amount -= 1; payment.save(update_fields=['amount'])
        with self.assertRaisesMessage(SettlementError, '付款流水不完整'):
            self.settle(order)
        self.assertFalse(OrderSegmentSettlementManifest.objects.filter(order=order).exists())
        payment.amount += 1; payment.save(update_fields=['amount'])
        self.assertEqual(self.settle(order), [self.provider.pk])
        record = OrderSegmentPersonSettlement.objects.get(manifest__order=order)
        record.amount += 1; record.save(update_fields=['amount'])
        with self.assertRaisesMessage(SettlementError, '份额或报单版本发生变化'):
            self.settle(order)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 1)

    def test_income_deposit_uses_payment_snapshot_and_links_both_ledger_sides(self):
        order, replacement, old, current = self.finished_order()
        profile = self.provider.escort_profile
        profile.deposit_mode = 'EARNINGS'; profile.deposit_paid = 0
        profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        self.submit_and_approve(order, old, self.provider)
        self.mature(order)
        self.assertEqual(self.settle(order), [self.provider.pk])
        record = OrderSegmentPersonSettlement.objects.get(manifest__order=order)
        profile.refresh_from_db()
        self.assertEqual((record.income_transaction.amount, record.deposit_debit.amount,
                          record.deposit_credit.amount, profile.deposit_paid), (3200, -1920, 1920, 1920))
        self.assertEqual(get_wallet(account=self.accounts[self.provider.pk], user=self.provider).balance, 1280)
        self.assertEqual(self.settle(order), [])

    @override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True)
    def test_segment_credit_records_refund_gap_separately_from_ordinary_deposit(self):
        from orders.tests.factories import make_completed_order
        from wallet.models import RefundDepositIncomeApplication, get_platform_wallet
        from wallet.test_refund_deposit_deficit import RefundDepositDeficitTest
        order, replacement, old, current = self.finished_order()
        self.account = self.accounts[self.provider.pk]
        self.profile = self.provider.escort_profile
        self.profile.deposit_mode = 'EARNINGS'; self.profile.deposit_paid = 1000
        self.profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        self.wallet = get_wallet(account=self.account, user=self.provider)
        self.platform = get_platform_wallet()
        self.platform.balance = 10000; self.platform.save(update_fields=['balance'])
        self.order = make_completed_order(self.boss, self.provider, amount=10000,
            provider_account=self.account, payment_status='PAID')
        deficit = RefundDepositDeficitTest.deficit(self, 200)
        self.submit_and_approve(order, old, self.provider)
        self.mature(order)
        self.assertEqual(self.settle(order), [self.provider.pk])
        record = OrderSegmentPersonSettlement.objects.get(manifest__order=order)
        application = RefundDepositIncomeApplication.objects.get(source_transaction=record.income_transaction)
        deficit.refresh_from_db(); self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((application.applied_amount, deficit.remaining, record.deposit_debit.amount,
                          record.deposit_credit.amount, self.wallet.balance, self.profile.deposit_paid),
                         (200, 0, -1800, 1800, 1200, 2800))
        self.assertEqual(self.settle(order), [])
        repayment = application.repayments.get()
        repayment.amount -= 1; repayment.save(update_fields=['amount'])
        with self.assertRaisesMessage(SettlementError, '收益补押金流水异常'):
            self.settle(order)

    @override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True)
    def test_direct_deficit_does_not_hide_unregistered_legacy_recovery(self):
        from orders.models import OrderRefund, RefundRecoveryLine
        from orders.tests.factories import make_completed_order
        from wallet.models import RefundDepositDeficit, get_platform_wallet
        order, replacement, old, current = self.finished_order()
        self.submit_and_approve(order, old, self.provider)
        self.mature(order)
        account = self.accounts[self.provider.pk]
        profile = self.provider.escort_profile
        profile.deposit_mode = 'EARNINGS'; profile.save(update_fields=['deposit_mode'])
        wallet = get_wallet(account=account, user=self.provider)
        platform = get_platform_wallet()
        legacy_order = make_completed_order(self.boss, self.provider, amount=10000,
                                           provider_account=account, payment_status='PAID')
        legacy_refund = OrderRefund.objects.create(order=legacy_order, amount=100, reason='历史退款')
        original = Transaction.objects.create(wallet=wallet, order=legacy_order, tx_type='DEPOSIT', amount=-100,
                                              balance_before=100, balance_after=0)
        reversal = Transaction.objects.create(wallet=wallet, order=legacy_order, tx_type='DEPOSIT', amount=100,
                                              balance_before=0, balance_after=100, reversal_of=original)
        RefundRecoveryLine.objects.create(refund=legacy_refund, original_transaction=original,
                                          reversal_transaction=reversal, wallet=wallet, source='DEPOSIT', amount=100)
        direct_refund = OrderRefund.objects.create(order=legacy_order, amount=200, reason='直接押金退款')
        direct_tx = Transaction.objects.create(wallet=platform, order=legacy_order, tx_type='DEPOSIT_INCOME', amount=-200,
                                               balance_before=200, balance_after=0)
        RefundDepositDeficit.objects.create(refund=direct_refund, profile=profile, account=account,
            amount=200, remaining=200, source_kind='DIRECT', source_store_transaction=direct_tx,
            direct_key=f'{direct_refund.pk}:{profile.pk}')
        with self.assertRaisesMessage(SettlementError, '历史退款押金扣减'):
            self.settle(order)
        self.assertFalse(OrderSegmentPersonSettlement.objects.filter(manifest__order=order).exists())

    @override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
    def test_zero_income_settlement_releases_only_that_orders_recovery_permit(self):
        from orders.tests.factories import make_completed_order
        from wallet.models import RefundDepositRecoveryPermit, get_platform_wallet
        from wallet.test_refund_deposit_deficit import RefundDepositDeficitTest
        self.service.commission_rate = 100
        self.service.save(update_fields=['commission_rate'])
        order, replacement, old, current = self.finished_order()
        self.account = self.accounts[self.provider.pk]
        self.profile = self.provider.escort_profile
        self.profile.deposit_mode = 'EARNINGS'; self.profile.deposit_paid = 1000
        self.profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        self.wallet = get_wallet(account=self.account, user=self.provider)
        self.platform = get_platform_wallet()
        self.platform.balance = 10000; self.platform.save(update_fields=['balance'])
        self.order = make_completed_order(self.boss, self.provider, amount=10000,
            provider_account=self.account, payment_status='PAID')
        deficit = RefundDepositDeficitTest.deficit(self, 200)
        permit = RefundDepositRecoveryPermit.objects.create(profile=self.profile, account=self.account, order=order)
        self.submit_and_approve(order, old, self.provider)
        self.mature(order)
        self.assertEqual(self.settle(order), [self.provider.pk])
        record = OrderSegmentPersonSettlement.objects.get(manifest__order=order, account=self.account)
        permit.refresh_from_db(); deficit.refresh_from_db()
        self.assertEqual((record.amount, record.income_transaction_id, permit.close_reason,
                          permit.zero_income_settlement_id, deficit.remaining),
                         (0, None, 'ZERO_INCOME_SETTLED', record.pk, 200))
        self.assertEqual(self.settle(order), [])
        from orders.tests.factories import make_order
        from wallet.refund_recovery_order import close_for_zero_settlement
        later_order = make_order(self.boss, provider=self.provider, provider_account=self.account,
                                 status='GRABBED', payment_status='PAID')
        later_permit = RefundDepositRecoveryPermit.objects.create(profile=self.profile, account=self.account,
                                                                   order=later_order)
        with transaction.atomic():
            close_for_zero_settlement(self.profile, record)
        later_permit.refresh_from_db()
        self.assertIsNone(later_permit.closed_at)
        permit.close_reason = 'GAP_PAID'; permit.save(update_fields=['close_reason'])
        with self.assertRaisesMessage(SettlementError, '零收益补差机会凭证异常'):
            self.settle(order)

    @override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
    def test_service_handoff_keeps_both_permits_until_each_person_settles(self):
        from wallet.test_refund_recovery_grab import RefundRecoveryGrabTest
        from wallet.models import RefundDepositRecoveryPermit
        from orders.models import OrderParticipationSegment
        from orders.tests import test_release_flow as flow
        replacement = self.replacement()
        old_gap = RefundRecoveryGrabTest.add_gap_for(self, self.provider)
        new_gap = RefundRecoveryGrabTest.add_gap_for(self, replacement)
        order = self.new_order()
        order.service_clock_version = 1; order.service_freeze_hours = 72; order.service_deposit_percent = 60
        order.save(update_fields=['service_clock_version', 'service_freeze_hours', 'service_deposit_percent'])
        old_permit = RefundDepositRecoveryPermit.objects.get(order=order, account=self.accounts[self.provider.pk])
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
            'retained':4000, 'transferred':6000, 'agreement':'双方确认服务中转单份额'}))
        old_permit.refresh_from_db(); self.assertIsNone(old_permit.closed_at)
        new_permit = RefundDepositRecoveryPermit.objects.get(order=order, account=self.accounts[replacement.pk])
        old = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[self.provider.pk], released_at__isnull=False)
        current = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[replacement.pk], released_at__isnull=True)
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-evidence/',
            {'segment_id':old.pk, 'completion_image':flow.screenshot()}, format='multipart'))
        self.login(replacement)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id':current.pk, 'entry_image':flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/',
            {'completion_image':flow.screenshot()}, format='multipart'))
        order.refresh_from_db()
        self.submit_and_approve(order, old, self.provider)
        self.mature(order)
        self.assertEqual(self.settle(order), [self.provider.pk])
        old_permit.refresh_from_db(); new_permit.refresh_from_db(); old_gap.refresh_from_db(); new_gap.refresh_from_db()
        self.assertEqual((old_permit.close_reason, old_gap.remaining, new_permit.closed_at, new_gap.remaining),
                         ('GAP_PAID', 0, None, 200))
        self.submit_and_approve(order, current, replacement)
        self.mature(order)
        self.assertEqual(self.settle(order), [replacement.pk])
        new_permit.refresh_from_db(); new_gap.refresh_from_db()
        self.assertEqual((new_permit.close_reason, new_gap.remaining), ('GAP_PAID', 0))
        self.assertEqual(OrderSegmentPersonSettlement.objects.filter(manifest__order=order).count(), 2)

    @override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
    def test_returning_to_same_service_reuses_open_recovery_permit(self):
        from wallet.test_refund_recovery_grab import RefundRecoveryGrabTest
        from wallet.models import RefundDepositRecoveryPermit
        from orders.models import OrderParticipationSegment
        from orders.tests import test_release_flow as flow
        replacement = self.replacement()
        RefundRecoveryGrabTest.add_gap_for(self, self.provider)
        order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
            'retained':4000, 'transferred':6000, 'agreement':'首次转单分配'}))
        middle = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[replacement.pk], released_at__isnull=True)
        self.login(replacement)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id':middle.pk, 'entry_image':flow.screenshot()}, format='multipart'))
        self.ok(self.transfer(order.pk, replacement, self.provider, original_share={
            'retained':3000, 'transferred':3000, 'agreement':'原陪玩转回继续履约'}))
        rows = RefundDepositRecoveryPermit.objects.filter(order=order, account=self.accounts[self.provider.pk])
        self.assertEqual(rows.count(), 1)
        self.assertIsNone(rows.get().closed_at)
        self.assertEqual(OrderParticipationSegment.objects.filter(attribution__order=order,
            provider_account=self.accounts[self.provider.pk]).count(), 2)

    @override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
    def test_recovery_service_handoff_requires_segment_settlement_to_be_enabled(self):
        from wallet.test_refund_recovery_grab import RefundRecoveryGrabTest
        from wallet.models import RefundDepositRecoveryPermit
        from orders.tests import test_release_flow as flow
        replacement = self.replacement()
        RefundRecoveryGrabTest.add_gap_for(self, replacement)
        order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        with override_settings(SEGMENT_SETTLEMENT_ENABLED=False):
            result = self.transfer(order.pk, self.provider, replacement, original_share={
                'retained':4000, 'transferred':6000, 'agreement':'分段结算关闭'} )
        self.assertNotEqual(result.data['code'], 0)
        order.refresh_from_db()
        self.assertEqual(order.provider_account_id, self.accounts[self.provider.pk].pk)
        self.assertFalse(RefundDepositRecoveryPermit.objects.filter(order=order).exists())

    @override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
    def test_recovery_service_handoff_rolls_back_when_permit_creation_fails(self):
        from wallet.test_refund_recovery_grab import RefundRecoveryGrabTest
        from wallet.models import RefundDepositRecoveryPermit
        from orders.models import OrderOriginalShareTransfer, OrderSegmentFinancialCoverage
        from orders.tests import test_release_flow as flow
        replacement = self.replacement()
        RefundRecoveryGrabTest.add_gap_for(self, replacement)
        order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        before = (order.transactions.count(), order.amount)
        with patch('wallet.refund_recovery_order.reserve', side_effect=ValueError('隔离测试机会写入失败')):
            result = self.transfer(order.pk, self.provider, replacement, original_share={
                'retained':4000, 'transferred':6000, 'agreement':'双方确认转单份额'})
        self.assertNotEqual(result.data['code'], 0)
        order.refresh_from_db()
        self.assertEqual((order.transactions.count(), order.amount), before)
        self.assertEqual(order.provider_account_id, self.accounts[self.provider.pk].pk)
        self.assertFalse(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists())
        self.assertEqual(OrderSegmentFinancialCoverage.objects.get(attribution__order=order).status, 'ACTIVE')
        self.assertFalse(RefundDepositRecoveryPermit.objects.filter(order=order).exists())

    @override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
    def test_service_handoff_rejects_existing_permit_for_a_different_order(self):
        from wallet.test_refund_recovery_grab import RefundRecoveryGrabTest
        from wallet.models import RefundDepositRecoveryPermit
        from orders.tests.factories import make_order
        from orders.tests import test_release_flow as flow
        replacement = self.replacement()
        RefundRecoveryGrabTest.add_gap_for(self, replacement)
        account = self.accounts[replacement.pk]
        other = make_order(self.boss, provider=replacement, provider_account=account,
                           status='GRABBED', payment_status='PAID')
        permit = RefundDepositRecoveryPermit.objects.create(profile=replacement.escort_profile,
                                                             account=account, order=other)
        order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        result = self.transfer(order.pk, self.provider, replacement, original_share={
            'retained':4000, 'transferred':6000, 'agreement':'另一订单尚未结算'})
        self.assertNotEqual(result.data['code'], 0)
        order.refresh_from_db(); permit.refresh_from_db()
        self.assertEqual(order.provider_account_id, self.accounts[self.provider.pk].pk)
        self.assertIsNone(permit.closed_at)
        self.assertEqual(RefundDepositRecoveryPermit.objects.filter(account=account, closed_at__isnull=True).count(), 1)

    @skipUnlessDBFeature('has_select_for_update')
    @override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
    def test_simultaneous_service_handoffs_share_one_recovery_permit(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections
        from rest_framework.test import APIClient
        from club_accounts.tokens import issue_account_tokens
        from wallet.test_refund_recovery_grab import RefundRecoveryGrabTest
        from wallet.models import RefundDepositRecoveryPermit
        from orders.models import Order
        from orders.tests import test_release_flow as flow
        replacement = self.replacement()
        RefundRecoveryGrabTest.add_gap_for(self, replacement)
        self.ok(self.recharge())
        self.login(self.boss)
        order_ids = []
        for key in ('service-race-a', 'service-race-b'):
            data = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
                'provider_id':self.accounts[self.provider.pk].pk, 'client_request_id':key}, format='json'))
            order_ids.append(data['id'])
        self.login(self.provider)
        for order_id in order_ids:
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/',
                {'entry_image':flow.screenshot()}, format='multipart'))
        token = issue_account_tokens(self.accounts[self.admin.pk])[0]
        old_id, new_id = self.accounts[self.provider.pk].pk, self.accounts[replacement.pk].pk
        barrier = Barrier(2)
        def handoff(order_id):
            close_old_connections()
            try:
                client = APIClient(HTTP_AUTHORIZATION='Bearer '+token)
                barrier.wait(timeout=10)
                return client.post(f'/api/admin/orders/{order_id}/transfer/', {'reason':'并发测试',
                    'transfers':[{'old_provider_id':old_id, 'new_provider_id':new_id,
                        'split_type':'PERCENT', 'split_value':0, 'penalty_amount':0,
                        'original_share':{'retained':4000, 'transferred':6000, 'agreement':'双方确认份额'}}]},
                    format='json').data['code']
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(handoff, order_ids))
        self.assertEqual(sum(code == 0 for code in results), 1)
        self.assertEqual(RefundDepositRecoveryPermit.objects.filter(account_id=new_id, closed_at__isnull=True).count(), 1)
        self.assertEqual(Order.objects.filter(pk__in=order_ids, provider_account_id=new_id).count(), 1)

    def test_unrelated_unready_cash_deposit_does_not_block_other_person(self):
        order, replacement, old, current = self.finished_order()
        profile = self.provider.escort_profile
        profile.deposit_mode = 'CASH'; profile.deposit_paid = 0
        profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        self.submit_and_approve(order, current, replacement)
        self.mature(order)
        self.assertEqual(self.settle(order), [replacement.pk])
        self.submit_and_approve(order, old, self.provider)
        self.mature(order)
        with self.assertRaisesMessage(SettlementError, '现金押金不足'):
            self.settle(order)
        self.assertEqual(OrderSegmentPersonSettlement.objects.filter(manifest__order=order).count(), 1)

    def test_consumption_confirmation_rechecks_finalized_ledger(self):
        from engagement.confirmation import process_order
        from engagement.models import ConsumptionConfirmation
        order, replacement, old, current = self.finished_order()
        self.submit_and_approve(order, old, self.provider)
        self.submit_and_approve(order, current, replacement)
        self.mature(order)
        self.assertEqual(len(self.settle(order)), 2)
        order.refresh_from_db()
        manifest = OrderSegmentSettlementManifest.objects.get(order=order)
        store = manifest.store_transaction
        store.amount -= 1; store.save(update_fields=['amount'])
        with self.assertRaisesMessage(SettlementError, '门店收入流水异常'):
            process_order(order.pk)
        self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())

    def test_referral_is_posted_once_and_reconciled_when_inviter_is_provider(self):
        from engagement.confirmation import process_order
        order, replacement, old, current = self.finished_order()
        order.inviter = self.provider; order.inviter_account = self.accounts[self.provider.pk]
        order.inviter_commission_rate_snapshot = 10
        order.save(update_fields=['inviter', 'inviter_account', 'inviter_commission_rate_snapshot'])
        self.submit_and_approve(order, old, self.provider)
        self.submit_and_approve(order, current, replacement)
        self.mature(order)
        self.assertTrue(process_order(order.pk))
        self.assertFalse(process_order(order.pk))
        order.refresh_from_db()
        self.assertEqual((order.provider_income, order.inviter_commission, order.shop_income), (8000, 1000, 1000))
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME',
            remark=f'推荐分佣：{order.order_no}', amount=1000).count(), 1)

    def test_credit_failure_rolls_back_manifest_and_all_wallet_changes(self):
        order, replacement, old, current = self.finished_order()
        self.submit_and_approve(order, old, self.provider)
        self.submit_and_approve(order, current, replacement)
        self.mature(order)
        before = get_wallet(account=self.accounts[self.provider.pk], user=self.provider).balance
        from orders.services import _credit_wallet
        calls = []
        def fail_second(**kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                raise SettlementError('模拟第二笔失败')
            return _credit_wallet(**kwargs)
        with patch('orders.services._credit_wallet', side_effect=fail_second):
            with self.assertRaisesMessage(SettlementError, '模拟第二笔失败'):
                self.settle(order)
        self.assertFalse(OrderSegmentSettlementManifest.objects.filter(order=order).exists())
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
        self.assertEqual(get_wallet(account=self.accounts[self.provider.pk], user=self.provider).balance, before)

    def test_scheduler_releases_individual_shares_and_confirms_consumption(self):
        from engagement.tasks import confirm_due_consumption
        from engagement.models import ConsumptionConfirmation
        order, replacement, old, current = self.finished_order()
        self.submit_and_approve(order, old, self.provider)
        self.mature(order)
        self.assertEqual(confirm_due_consumption(), 0)
        self.assertEqual(OrderSegmentPersonSettlement.objects.filter(manifest__order=order).count(), 1)
        self.assertEqual(confirm_due_consumption(), 0)
        self.submit_and_approve(order, current, replacement)
        self.mature(order)
        self.assertEqual(confirm_due_consumption(), 1)
        self.assertEqual(confirm_due_consumption(), 0)
        order.refresh_from_db()
        self.assertTrue(order.settled_at)
        self.assertTrue(ConsumptionConfirmation.objects.filter(order=order).exists())
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 2)

    def test_protection_blocks_old_refund_even_after_payout_switch_is_disabled(self):
        from orders.refunds import RefundError, build_refund_plan
        from orders.services import refund_to_customer
        from orders.models import Order
        order, replacement, old, current = self.finished_order()
        self.submit_and_approve(order, old, self.provider)
        self.mature(order)
        self.assertEqual(self.settle(order), [self.provider.pk])
        with override_settings(SEGMENT_SETTLEMENT_ENABLED=False, SEGMENT_SETTLEMENT_ENFORCE_ENABLED=True,
                               SEGMENT_ECONOMICS_CAPTURE_ENABLED=False, SEGMENT_ECONOMICS_ENFORCE_ENABLED=False):
            with self.assertRaisesMessage(RefundError, '本单逐段退款分摊及资金处理待核对'):
                build_refund_plan(order, refund_amount=10)
            with transaction.atomic():
                locked = Order.objects.select_for_update().get(pk=order.pk)
                with self.assertRaisesMessage(RefundError, '本单逐段退款分摊及资金处理待核对'):
                    refund_to_customer(locked, refund_amount=10)
                from orders.services import settle_order
                self.assertEqual(settle_order(locked), [])
        with override_settings(SEGMENT_SETTLEMENT_ENABLED=True, ORIGINAL_ATTRIBUTION_ENABLED=False,
                               SEGMENT_ECONOMICS_CAPTURE_ENABLED=False, SEGMENT_ECONOMICS_ENFORCE_ENABLED=False):
            with self.assertRaisesMessage(RefundError, '本单逐段退款分摊及资金处理待核对'):
                build_refund_plan(order, refund_amount=10)
            with transaction.atomic():
                locked = Order.objects.select_for_update().get(pk=order.pk)
                self.assertEqual(settle_order(locked), [])
        self.assertFalse(order.refund_records.exists())
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 1)
