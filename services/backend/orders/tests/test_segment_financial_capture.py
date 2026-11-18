import os
from unittest.mock import patch

from django.db import DatabaseError, connection
from django.conf import settings
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITransactionTestCase
from unittest import skipUnless

from orders.models import (Order, OrderSegmentFinancialCoverage,
                           OrderSegmentFinancialSnapshot, OrderSegmentTransferBasis)
from orders.tests import test_prestart_handoff as fixture
from orders.tests import test_release_flow as flow
from orders.tests import test_game_commission_payment as game_fixture
from orders.tests import test_staff_team_dispatch as team_fixture
from wallet.models import Transaction, Wallet


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   PRESTART_HANDOFF_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class SegmentFinancialCaptureTest(APITransactionTestCase):
    setUp = fixture.PrestartHandoffTest.setUp
    login = fixture.PrestartHandoffTest.login
    ok = fixture.PrestartHandoffTest.ok
    recharge = fixture.PrestartHandoffTest.recharge
    new_order = fixture.PrestartHandoffTest.new_order
    replacement = fixture.PrestartHandoffTest.replacement
    transfer = fixture.PrestartHandoffTest.transfer

    def test_new_paid_assignment_records_deal_and_entry_rule_without_payout(self):
        order = self.new_order()
        coverage = OrderSegmentFinancialCoverage.objects.get(attribution__order=order)
        snapshot = OrderSegmentFinancialSnapshot.objects.get(segment__attribution__order=order)
        self.assertEqual((coverage.status, coverage.deal_amount, coverage.source),
                         ('ACTIVE', 10000, 'WALLET_NO_POINTS'))
        self.assertEqual((snapshot.incoming_basis, snapshot.retained_basis, snapshot.commission_type,
                          snapshot.commission_rate, snapshot.income_at_entry), (10000, None, 'PERCENT', 20, 8000))
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    def test_prestart_replacement_and_return_keep_separate_rate_snapshots(self):
        replacement = self.replacement(); order = self.new_order()
        self.service.commission_override = True; self.service.commission_rate = 30
        self.service.save(update_fields=['commission_override', 'commission_rate'])
        self.ok(self.transfer(order.pk, self.provider, replacement))
        self.service.commission_rate = 25; self.service.save(update_fields=['commission_rate'])
        self.ok(self.transfer(order.pk, replacement, self.provider))
        rows = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order)
                    .order_by('segment__pk'))
        self.assertEqual([(item.incoming_basis, item.retained_basis, item.commission_rate, item.income_at_entry)
                          for item in rows], [(10000, 0, 20, 8000), (10000, 0, 30, 7000), (10000, None, 25, 7500)])
        self.assertEqual(OrderSegmentFinancialCoverage.objects.get(attribution__order=order).status, 'ACTIVE')
        self.login(self.admin)
        capture = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/'))['original_attribution']['financial_capture']
        self.assertEqual(capture['status'], 'ACTIVE')
        self.assertEqual([item['commission_rate'] for item in capture['segments']], [20, 30, 25])
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '正式转单逐段结算链仍在接线（18-盘点转单分段收敛），门禁保持关闭；最终联调启用')
    def test_in_service_transfer_is_explicitly_review_until_segment_split_is_integrated(self):
        replacement = self.replacement(); order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
            'retained':4000, 'transferred':6000, 'agreement':'双方确认份额'}))
        coverage = OrderSegmentFinancialCoverage.objects.get(attribution__order=order)
        self.assertEqual(coverage.status, 'REVIEW')
        self.assertIn('接入抽成及结算', coverage.review_reason)
        self.assertEqual(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order).count(), 1)
        allocation = OrderSegmentTransferBasis.objects.get(transfer__previous_segment__attribution__order=order)
        self.assertEqual((allocation.basis_before, allocation.retained_basis, allocation.transferred_basis),
                         (10000, 4000, 6000))
        self.assertEqual(OrderSegmentFinancialSnapshot.objects.get(segment__attribution__order=order).retained_basis, 4000)
        self.login(self.admin)
        capture = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/'))['original_attribution']['financial_capture']
        self.assertEqual(capture['transfers'][0]['transferred_basis'], 6000)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '正式转单逐段结算链仍在接线（18-盘点转单分段收敛），门禁保持关闭；最终联调启用')
    def test_retained_provider_can_find_order_when_legacy_report_is_missing(self):
        from wallet.models import ProviderReport
        replacement = self.replacement(); order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
            'retained':4000, 'transferred':6000, 'agreement':'双方确认份额'}))
        ProviderReport.objects.filter(order=order, provider_account=self.accounts[self.provider.pk]).delete()
        self.login(self.provider)
        choices = self.ok(self.client.get('/api/wallet/report-order-choices/', {'q':order.order_no}))
        self.assertEqual([row['id'] for row in choices['list']], [order.pk])
        with override_settings(SEGMENT_ECONOMICS_CAPTURE_ENABLED=False, SEGMENT_ECONOMICS_ENFORCE_ENABLED=True):
            paused = self.ok(self.client.get('/api/wallet/report-order-choices/', {'q':order.order_no}))
            self.assertEqual([row['id'] for row in paused['list']], [order.pk])
        report = self.ok(self.client.post('/api/wallet/reports/', {'order_id':order.pk}, format='json'))
        self.assertEqual(report['order_id'], order.pk)
        self.assertIsNone(report['expected_income'])

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '正式转单逐段结算链仍在接线（18-盘点转单分段收敛），门禁保持关闭；最终联调启用')
    def test_transferred_provider_without_retained_share_is_not_offered_report(self):
        from wallet.models import ProviderReport
        replacement = self.replacement(); order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
            'retained':0, 'transferred':10000, 'agreement':'双方确认份额'}))
        ProviderReport.objects.filter(order=order, provider_account=self.accounts[self.provider.pk]).delete()
        self.login(self.provider)
        choices = self.ok(self.client.get('/api/wallet/report-order-choices/', {'q':order.order_no}))
        self.assertEqual(choices['list'], [])
        denied = self.client.post('/api/wallet/reports/', {'order_id':order.pk}, format='json')
        self.assertNotEqual(denied.data['code'], 0)

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '正式转单逐段结算链仍在接线（18-盘点转单分段收敛），门禁保持关闭；最终联调启用')
    def test_returning_provider_preserves_first_segment_and_splits_only_current_basis(self):
        replacement = self.replacement(); order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
            'retained':4000, 'transferred':6000, 'agreement':'双方确认份额'}))
        from orders.models import OrderParticipationSegment
        incoming = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[replacement.pk], released_at__isnull=True)
        self.login(replacement)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id':incoming.pk, 'entry_image':flow.screenshot()}, format='multipart'))
        self.ok(self.transfer(order.pk, replacement, self.provider, original_share={
            'retained':2000, 'transferred':4000, 'agreement':'双方确认转回'}))
        rows = list(OrderSegmentTransferBasis.objects.filter(transfer__previous_segment__attribution__order=order).order_by('pk'))
        self.assertEqual([(item.basis_before, item.retained_basis, item.transferred_basis) for item in rows],
                         [(10000, 4000, 6000), (6000, 2000, 4000)])
        self.assertEqual(OrderSegmentFinancialSnapshot.objects.get(segment__attribution__order=order).retained_basis, 4000)
        self.assertEqual(OrderSegmentFinancialCoverage.objects.get(attribution__order=order).status, 'REVIEW')

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '正式转单逐段结算链仍在接线（18-盘点转单分段收敛），门禁保持关闭；最终联调启用')
    def test_discounted_deal_remainder_and_original_price_are_separate(self):
        from coupons.models import Coupon, UserCoupon
        from django.utils import timezone
        from datetime import timedelta
        self.service.price = 9000; self.service.save(update_fields=['price'])
        replacement = self.replacement(); self.ok(self.recharge())
        coupon = Coupon.objects.create(name='隔离金额券', discount_type='DIRECT', threshold=0,
            amount=7990, valid_to=timezone.now()+timedelta(days=1), is_active=True)
        owned = UserCoupon.objects.create(account=self.accounts[self.boss.pk], coupon=coupon)
        self.login(self.boss)
        result = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'provider_id':self.accounts[self.provider.pk].pk, 'user_coupon_id':owned.pk,
            'client_request_id':'discounted-segment-basis'}, format='json'))
        order = Order.objects.get(pk=result['id'])
        self.assertEqual((order.original_amount, order.amount), (9000, 1010))
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
            'retained':3000, 'transferred':6000, 'agreement':'双方确认比例'}))
        allocation = OrderSegmentTransferBasis.objects.get(transfer__previous_segment__attribution__order=order)
        self.assertEqual((allocation.basis_before, allocation.retained_basis, allocation.transferred_basis,
                          allocation.remainder_numerator), (1010, 336, 674, 6000))

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '正式转单逐段结算链仍在接线（18-盘点转单分段收敛），门禁保持关闭；最终联调启用')
    def test_missing_previous_allocation_does_not_invent_next_basis(self):
        replacement = self.replacement(); order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        with override_settings(SEGMENT_ECONOMICS_CAPTURE_ENABLED=False):
            self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
                'retained':4000, 'transferred':6000, 'agreement':'关闭期间转单'}))
        # The coverage may still say ACTIVE after that gap; the independently
        # recorded original transfer nevertheless keeps settlement pending.
        from stores.db import transaction
        from orders.segment_financial_capture import transfer_settlement_pending
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            self.assertTrue(transfer_settlement_pending(locked))
        # No segment-start record exists for the replacement while disabled;
        # retaining zero is sufficient to test fail-closed accounting capture.
        self.ok(self.transfer(order.pk, replacement, self.provider, original_share={
            'retained':0, 'transferred':6000, 'agreement':'重新转回核对'}))
        coverage = OrderSegmentFinancialCoverage.objects.get(attribution__order=order)
        self.assertEqual(coverage.status, 'REVIEW')
        self.assertIn('成交', coverage.review_reason)
        self.assertFalse(OrderSegmentTransferBasis.objects.filter(transfer__previous_segment__attribution__order=order).exists())

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '正式转单逐段结算链仍在接线（18-盘点转单分段收敛），门禁保持关闭；最终联调启用')
    def test_allocation_failure_rolls_back_transfer_and_original_shares(self):
        replacement = self.replacement(); order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        before = Transaction.objects.filter(order=order).count()
        with patch('orders.segment_financial_capture.OrderSegmentTransferBasis.objects.create',
                   side_effect=DatabaseError('allocation failure')):
            with self.assertRaises(DatabaseError):
                self.transfer(order.pk, self.provider, replacement, original_share={
                    'retained':4000, 'transferred':6000, 'agreement':'双方确认份额'})
        order.refresh_from_db()
        self.assertEqual(order.provider_account_id, self.accounts[self.provider.pk].pk)
        coverage = OrderSegmentFinancialCoverage.objects.get(attribution__order=order)
        snapshot = OrderSegmentFinancialSnapshot.objects.get(segment__attribution__order=order)
        self.assertEqual(coverage.status, 'ACTIVE'); self.assertIsNone(snapshot.retained_basis)
        self.assertEqual(coverage.attribution.segments.count(), 1)
        self.assertEqual(Transaction.objects.filter(order=order).count(), before)

    @override_settings(REQUIRE_ORDER_EVIDENCE_IMAGES=False)
    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '正式转单逐段结算链仍在接线（18-盘点转单分段收敛），门禁保持关闭；最终联调启用')
    def test_pending_transfer_cannot_pay_using_legacy_split(self):
        from stores.db import transaction
        from orders.services import settle_order
        replacement = self.replacement(); order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {}, format='json'))
        self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
            'retained':4000, 'transferred':6000, 'agreement':'双方确认份额'}))
        order.refresh_from_db()
        order.status = Order.Status.COMPLETED; order.settlement_mode = Order.SettlementMode.COMPLETION
        order.save(update_fields=['status', 'settlement_mode'])
        before = Transaction.objects.filter(order=order).count()
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            self.assertEqual(settle_order(locked), [])
        # Capture can be paused after migrations without dropping the guard on
        # already-recorded orders; this switch remains independently enabled.
        with override_settings(SEGMENT_ECONOMICS_CAPTURE_ENABLED=False, SEGMENT_ECONOMICS_ENFORCE_ENABLED=True):
            with transaction.atomic():
                locked = Order.objects.select_for_update().get(pk=order.pk)
                self.assertEqual(settle_order(locked), [])
        order.refresh_from_db()
        self.assertIsNone(order.settled_at)
        self.assertEqual(Transaction.objects.filter(order=order).count(), before)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '正式转单逐段结算链仍在接线（18-盘点转单分段收敛），门禁保持关闭；最终联调启用')
    def test_old_completion_mode_cannot_pay_during_captured_transfer(self):
        replacement = self.replacement(); order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        order.refresh_from_db(); order.settlement_mode = Order.SettlementMode.COMPLETION
        order.save(update_fields=['settlement_mode'])
        before = Transaction.objects.filter(order=order).count()
        result = self.transfer(order.pk, self.provider, replacement, split_value=50, original_share={
            'retained':4000, 'transferred':6000, 'agreement':'双方确认份额'})
        self.assertIn('旧结算模式', result.data['msg'])
        order.refresh_from_db(); self.assertEqual(order.provider_account_id, self.accounts[self.provider.pk].pk)
        self.assertEqual(Transaction.objects.filter(order=order).count(), before)

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '正式转单逐段结算链仍在接线（18-盘点转单分段收敛），门禁保持关闭；最终联调启用')
    def test_paused_capture_with_enforcement_cannot_skip_an_existing_order_event(self):
        replacement = self.replacement(); order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        with override_settings(SEGMENT_ECONOMICS_CAPTURE_ENABLED=False, SEGMENT_ECONOMICS_ENFORCE_ENABLED=True):
            result = self.transfer(order.pk, self.provider, replacement, original_share={
                'retained':4000, 'transferred':6000, 'agreement':'双方确认份额'})
        self.assertIn('记录暂不可用', result.data['msg'])
        order.refresh_from_db(); self.assertEqual(order.provider_account_id, self.accounts[self.provider.pk].pk)
        self.assertFalse(OrderSegmentTransferBasis.objects.filter(transfer__previous_segment__attribution__order=order).exists())

    def test_paused_capture_with_enforcement_also_blocks_prestart_reassignment(self):
        replacement = self.replacement(); order = self.new_order()
        with override_settings(SEGMENT_ECONOMICS_CAPTURE_ENABLED=False, SEGMENT_ECONOMICS_ENFORCE_ENABLED=True):
            result = self.transfer(order.pk, self.provider, replacement)
        self.assertIn('记录暂不可用', result.data['msg'])
        order.refresh_from_db(); self.assertEqual(order.provider_account_id, self.accounts[self.provider.pk].pk)
        coverage = OrderSegmentFinancialCoverage.objects.get(attribution__order=order)
        self.assertEqual((coverage.status, coverage.attribution.segments.count()), ('ACTIVE', 1))

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '正式转单逐段结算链仍在接线（18-盘点转单分段收敛），门禁保持关闭；最终联调启用')
    def test_pending_transfer_cannot_pay_platform_subsidy_through_separate_path(self):
        from orders.models import OrderPlatformSubsidy, OrderPlatformSubsidyShare
        from orders.subsidies import settle_platform_subsidy_share
        from wallet.models import get_platform_wallet
        replacement = self.replacement(); order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
            'retained':4000, 'transferred':6000, 'agreement':'双方确认份额'}))
        order.refresh_from_db(); order.status = Order.Status.COMPLETED; order.save(update_fields=['status'])
        subsidy = OrderPlatformSubsidy.objects.create(order=order, coupon_id_snapshot=1, basis=10000)
        assignment = order.providers.get(provider_account=self.accounts[replacement.pk])
        share = OrderPlatformSubsidyShare.objects.create(subsidy=subsidy, assignment=assignment, amount=8000)
        platform = get_platform_wallet(); platform.balance = 10000; platform.save(update_fields=['balance'])
        self.assertFalse(settle_platform_subsidy_share(share.pk))
        share.refresh_from_db(); platform.refresh_from_db()
        self.assertIsNone(share.paid_at); self.assertEqual(platform.balance, 10000)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').exists())
        # Turning off capture does not waive service clocks, report evidence,
        # or the requirement to reconcile the subsidy and order funding.
        with override_settings(SEGMENT_ECONOMICS_CAPTURE_ENABLED=False):
            self.assertFalse(settle_platform_subsidy_share(share.pk))
        self.assertFalse(settle_platform_subsidy_share(share.pk))
        platform.refresh_from_db(); self.assertEqual(platform.balance, 10000)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').exists())

    def test_fixed_total_snapshot_changes_at_prestart_reassignment(self):
        self.service.commission_override = True; self.service.commission_type = 'FIXED'
        self.service.commission_fixed = 2000
        self.service.save(update_fields=['commission_override', 'commission_type', 'commission_fixed'])
        replacement = self.replacement(); order = self.new_order()
        self.service.commission_fixed = 3000; self.service.save(update_fields=['commission_fixed'])
        self.ok(self.transfer(order.pk, self.provider, replacement))
        rows = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order)
                    .order_by('segment__pk'))
        self.assertEqual([(item.commission_type, item.fixed_rule_total, item.commission_fixed,
                           item.income_at_entry, item.retained_basis) for item in rows], [
            ('FIXED', 2000, 2000, 8000, 0), ('FIXED', 3000, 3000, 7000, None)])
        self.assertEqual(OrderSegmentFinancialCoverage.objects.get(attribution__order=order).status, 'ACTIVE')

    def test_historical_missing_coverage_is_not_backfilled(self):
        with override_settings(SEGMENT_ECONOMICS_CAPTURE_ENABLED=False):
            order = self.new_order()
        replacement = self.replacement()
        self.ok(self.transfer(order.pk, self.provider, replacement))
        self.assertFalse(OrderSegmentFinancialCoverage.objects.filter(attribution__order=order).exists())
        self.assertFalse(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order).exists())

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '正式转单逐段结算链仍在接线（18-盘点转单分段收敛），门禁保持关闭；最终联调启用')
    def test_disabled_feature_does_not_query_new_financial_tables(self):
        with override_settings(SEGMENT_ECONOMICS_CAPTURE_ENABLED=False), CaptureQueriesContext(connection) as queries:
            self.new_order()
        sql = ' '.join(item['sql'].lower() for item in queries.captured_queries)
        self.assertNotIn('orders_ordersegmentfinancialcoverage', sql)
        self.assertNotIn('orders_ordersegmentfinancialsnapshot', sql)

    def test_snapshot_write_failure_rolls_back_purchase(self):
        self.ok(self.recharge()); self.login(self.boss)
        balance = Wallet.objects.get(account=self.accounts[self.boss.pk]).balance
        with patch('orders.segment_financial_capture._capture', side_effect=DatabaseError('snapshot failure')):
            with self.assertRaises(DatabaseError):
                self.client.post('/api/orders/create/', {'service_id':self.service.pk,
                    'provider_id':self.accounts[self.provider.pk].pk,
                    'client_request_id':'segment-financial-failure'}, format='json')
        self.assertFalse(Order.objects.exists())
        self.assertEqual(Wallet.objects.get(account=self.accounts[self.boss.pk]).balance, balance)
        self.assertFalse(Transaction.objects.filter(tx_type='PAY').exists())


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class SegmentFinancialTeamTest(APITransactionTestCase):
    setUp = team_fixture.StaffTeamDispatchTest.setUp
    auth = team_fixture.StaffTeamDispatchTest.auth
    create = team_fixture.StaffTeamDispatchTest.create
    assign = team_fixture.StaffTeamDispatchTest.assign

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '正式转单逐段结算链仍在接线（18-盘点转单分段收敛），门禁保持关闭；最终联调启用')
    def test_duo_captures_each_assignment_without_doubling_deal_amount(self):
        self.service.price = 5000; self.service.save(update_fields=['price'])
        order = self.create(); self.assertEqual(self.assign(order).data['code'], 0)
        coverage = OrderSegmentFinancialCoverage.objects.get(attribution__order=order)
        rows = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order))
        self.assertEqual(coverage.status, 'ACTIVE')
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(item.incoming_basis for item in rows), coverage.deal_amount)

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '正式转单逐段结算链仍在接线（18-盘点转单分段收敛），门禁保持关闭；最终联调启用')
    def test_duo_transfer_splits_only_the_affected_seat(self):
        self.service.price = 5000; self.service.save(update_fields=['price'])
        order = self.create(); self.assertEqual(self.assign(order).data['code'], 0)
        for provider in self.providers[:2]:
            self.auth(provider)
            result = self.client.post(f'/api/orders/orders/{order.pk}/start/',
                {'entry_image':team_fixture.screenshot()}, format='multipart')
            self.assertEqual(result.data['code'], 0, result.data)
        self.auth(self.staff)
        result = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'更换陪玩', 'transfers':[{
            'old_provider_id':self.accounts[self.providers[0].pk].pk,
            'new_provider_id':self.accounts[self.providers[2].pk].pk,
            'split_type':'PERCENT', 'split_value':0, 'penalty_amount':0,
            'original_share':{'retained':2000, 'transferred':3000, 'agreement':'双方确认份额'}}]}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        allocation = OrderSegmentTransferBasis.objects.get(transfer__previous_segment__attribution__order=order)
        self.assertEqual((allocation.basis_before, allocation.retained_basis, allocation.transferred_basis), (5000, 2000, 3000))
        unchanged = OrderSegmentFinancialSnapshot.objects.get(segment__attribution__order=order,
            segment__provider_account=self.accounts[self.providers[1].pk])
        self.assertEqual((unchanged.incoming_basis, unchanged.retained_basis), (5000, None))
        admin = self.client.get(f'/api/admin/orders/{order.pk}/').data['data']
        for field in ('provider_income', 'inviter_commission', 'shop_income'):
            self.assertIsNone(admin[field], field)
        by_provider = {person['provider']: person for person in admin['providers']}
        self.assertIsNone(by_provider[self.accounts[self.providers[0].pk].pk]['expected_income'])
        self.assertIsNotNone(by_provider[self.accounts[self.providers[1].pk].pk]['expected_income'])
        with override_settings(SEGMENT_ECONOMICS_CAPTURE_ENABLED=False, SEGMENT_ECONOMICS_ENFORCE_ENABLED=True):
            paused = self.client.get(f'/api/admin/orders/{order.pk}/').data['data']
            self.assertIsNone(paused['provider_income'])
            by_provider = {person['provider']: person for person in paused['providers']}
            self.assertIsNone(by_provider[self.accounts[self.providers[0].pk].pk]['provider_income'])
            self.assertIsNotNone(by_provider[self.accounts[self.providers[1].pk].pk]['provider_income'])
        self.auth(self.providers[1])
        detail = self.client.get(f'/api/orders/orders/{order.pk}/').data['data']
        self.assertIsNotNone(detail['expected_income'])
        self.assertNotEqual(detail['settlement_status'], 'REVIEW')
        self.auth(self.providers[0])
        detail = self.client.get(f'/api/orders/orders/{order.pk}/').data['data']
        self.assertIsNone(detail['expected_income'])
        self.assertEqual(detail['settlement_status'], 'REVIEW')

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '正式转单逐段结算链仍在接线（18-盘点转单分段收敛），门禁保持关闭；最终联调启用')
    def test_captured_transfer_rejects_legacy_manual_split_without_mutation(self):
        self.service.price = 5000; self.service.save(update_fields=['price'])
        order = self.create(); self.assertEqual(self.assign(order).data['code'], 0)
        for provider in self.providers[:2]:
            self.auth(provider)
            self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/start/',
                {'entry_image': team_fixture.screenshot()}, format='multipart').data['code'], 0)
        self.auth(self.staff)
        result = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'更换陪玩', 'transfers':[{
            'old_provider_id':self.accounts[self.providers[0].pk].pk,
            'new_provider_id':self.accounts[self.providers[2].pk].pk,
            'split_type':'PERCENT', 'split_value':50, 'penalty_amount':0,
            'original_share':{'retained':2000, 'transferred':3000, 'agreement':'双方确认份额'}}]}, format='json')
        self.assertNotEqual(result.data['code'], 0)
        self.assertIn('不支持手工', result.data['msg'])
        self.assertFalse(OrderSegmentTransferBasis.objects.filter(transfer__previous_segment__attribution__order=order).exists())
        self.assertTrue(order.providers.filter(provider_account=self.accounts[self.providers[0].pk], released_at__isnull=True).exists())


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   SEGMENT_STANDARD_TRANSFER_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
                   PRESTART_HANDOFF_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class SegmentStandardTransferTest(APITransactionTestCase):
    def setUp(self):
        fixture.PrestartHandoffTest.setUp(self)
        self.card.staff_account = self.accounts[self.admin.pk]
        self.card.save(update_fields=['staff_account'])
    login = fixture.PrestartHandoffTest.login
    ok = fixture.PrestartHandoffTest.ok
    recharge = fixture.PrestartHandoffTest.recharge
    new_order = fixture.PrestartHandoffTest.new_order
    replacement = fixture.PrestartHandoffTest.replacement
    transfer = fixture.PrestartHandoffTest.transfer

    def test_percentage_rule_change_and_return_are_separate_snapshots(self):
        replacement = self.replacement(); order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        self.service.commission_override = True; self.service.commission_rate = 30
        self.service.save(update_fields=['commission_override', 'commission_rate'])
        self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
            'retained':4000, 'transferred':6000, 'agreement':'双方确认份额'}))
        from orders.models import OrderParticipationSegment
        incoming = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[replacement.pk], released_at__isnull=True)
        self.login(replacement)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id':incoming.pk, 'entry_image':flow.screenshot()}, format='multipart'))
        self.service.commission_rate = 40; self.service.save(update_fields=['commission_rate'])
        self.ok(self.transfer(order.pk, replacement, self.provider, original_share={
            'retained':2000, 'transferred':4000, 'agreement':'双方确认转回'}))
        rows = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order).order_by('segment__pk'))
        self.assertEqual([(x.incoming_basis, x.retained_basis, x.commission_rate, x.income_at_entry) for x in rows],
                         [(10000, 4000, 20, 8000), (6000, 2000, 30, 4200), (4000, None, 40, 2400)])
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    def test_fixed_rule_change_records_incoming_rounding_from_whole_deal(self):
        self.service.price = 9000; self.service.commission_override = True
        self.service.commission_type = 'FIXED'; self.service.commission_fixed = 2000
        self.service.save(update_fields=['price','commission_override','commission_type','commission_fixed'])
        replacement = self.replacement(); order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        self.service.commission_fixed = 2030; self.service.save(update_fields=['commission_fixed'])
        self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
            'retained':3000, 'transferred':6000, 'agreement':'双方确认份额'}))
        rows = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order).order_by('segment__pk'))
        self.assertEqual([(x.incoming_basis, x.retained_basis, x.fixed_rule_total,
                           x.commission_fixed, x.income_at_entry) for x in rows],
                         [(9000, 3000, 2000, 2000, 7000), (6000, None, 2030, 1354, 4646)])
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    def test_missing_payment_scope_rejects_transfer_without_changing_history(self):
        replacement = self.replacement()
        with override_settings(PRESTART_SCOPE_SNAPSHOT_ENABLED=False):
            order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        result = self.transfer(order.pk, self.provider, replacement, original_share={
            'retained':4000, 'transferred':6000, 'agreement':'双方确认份额'})
        self.assertNotEqual(result.data['code'], 0)
        self.assertFalse(OrderSegmentTransferBasis.objects.filter(transfer__previous_segment__attribution__order=order).exists())
        order.refresh_from_db(); self.assertEqual(order.provider_account_id, self.accounts[self.provider.pk].pk)

    def test_current_promotion_rule_is_captured_for_new_participant(self):
        from datetime import timedelta
        from django.utils import timezone
        from promotions.models import Promotion
        replacement = self.replacement(); order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        now = timezone.now()
        Promotion.objects.create(title='转入时固定抽成', commission_type='FIXED', commission_fixed=2000,
                                 start_at=now-timedelta(minutes=1), end_at=now+timedelta(hours=1))
        self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
            'retained':4000, 'transferred':6000, 'agreement':'双方确认份额'}))
        incoming = OrderSegmentFinancialSnapshot.objects.get(segment__attribution__order=order,
            segment__provider_account=self.accounts[replacement.pk])
        self.assertEqual((incoming.commission_type, incoming.commission_source, incoming.fixed_rule_total,
                          incoming.commission_fixed, incoming.income_at_entry), ('FIXED', 'promotion', 2000, 1200, 4800))

    def test_fixed_to_percentage_change_preserves_both_rule_modes(self):
        self.service.commission_override = True; self.service.commission_type = 'FIXED'
        self.service.commission_fixed = 2000
        self.service.save(update_fields=['commission_override', 'commission_type', 'commission_fixed'])
        replacement = self.replacement(); order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        self.service.commission_type = 'PERCENT'; self.service.commission_rate = 35
        self.service.save(update_fields=['commission_type', 'commission_rate'])
        self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
            'retained':4000, 'transferred':6000, 'agreement':'双方确认份额'}))
        rows = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order).order_by('segment__pk'))
        self.assertEqual([(item.commission_type, item.commission_rate, item.fixed_rule_total, item.income_at_entry)
                          for item in rows], [('FIXED', 0, 2000, 8000), ('PERCENT', 35, None, 3900)])

    def test_incoming_snapshot_failure_rolls_back_transfer(self):
        replacement = self.replacement(); order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        before = Transaction.objects.filter(order=order).count()
        with patch('orders.segment_financial_capture.OrderSegmentFinancialSnapshot.objects.create',
                   side_effect=DatabaseError('entry snapshot failed')):
            with self.assertRaises(DatabaseError):
                self.transfer(order.pk, self.provider, replacement, original_share={
                    'retained':4000, 'transferred':6000, 'agreement':'双方确认份额'})
        order.refresh_from_db(); self.assertEqual(order.provider_account_id, self.accounts[self.provider.pk].pk)
        self.assertFalse(OrderSegmentTransferBasis.objects.filter(transfer__previous_segment__attribution__order=order).exists())
        original = OrderSegmentFinancialSnapshot.objects.get(segment__attribution__order=order)
        self.assertIsNone(original.retained_basis)
        self.assertEqual(Transaction.objects.filter(order=order).count(), before)


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   SEGMENT_STANDARD_TRANSFER_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class SegmentStandardTeamTransferTest(APITransactionTestCase):
    setUp = team_fixture.StaffTeamDispatchTest.setUp
    auth = team_fixture.StaffTeamDispatchTest.auth
    create = team_fixture.StaffTeamDispatchTest.create
    assign = team_fixture.StaffTeamDispatchTest.assign

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '正式转单逐段结算链仍在接线（18-盘点转单分段收敛），门禁保持关闭；最终联调启用')
    def test_same_fixed_rule_preserves_seat_fee_and_other_provider(self):
        self.service.price = 300; self.service.commission_fixed = 3
        self.service.save(update_fields=['price', 'commission_fixed'])
        order = self.create(); self.assertEqual(self.assign(order).data['code'], 0)
        for provider in self.providers[:2]:
            self.auth(provider)
            self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/start/',
                {'entry_image':team_fixture.screenshot()}, format='multipart').data['code'], 0)
        self.auth(self.staff)
        result = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'更换陪玩', 'transfers':[{
            'old_provider_id':self.accounts[self.providers[0].pk].pk,
            'new_provider_id':self.accounts[self.providers[2].pk].pk,
            'original_share':{'retained':100, 'transferred':200, 'agreement':'双方确认份额'}}]}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        rows = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order).order_by('segment__pk'))
        self.assertEqual([(x.incoming_basis, x.retained_basis, x.fixed_rule_total, x.commission_fixed, x.income_at_entry)
                          for x in rows], [(300, 100, 3, 2, 298), (300, None, 3, 1, 299), (200, None, 3, 2, 198)])
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '正式转单逐段结算链仍在接线（18-盘点转单分段收敛），门禁保持关闭；最终联调启用')
    def test_fixed_team_transfer_preserves_other_seat_and_full_order_denominator(self):
        self.service.price = 5000; self.service.save(update_fields=['price'])
        order = self.create(); self.assertEqual(self.assign(order).data['code'], 0)
        for provider in self.providers[:2]:
            self.auth(provider)
            self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/start/',
                {'entry_image':team_fixture.screenshot()}, format='multipart').data['code'], 0)
        self.service.commission_fixed = 2001; self.service.save(update_fields=['commission_fixed'])
        self.auth(self.staff)
        result = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'更换陪玩', 'transfers':[{
            'old_provider_id':self.accounts[self.providers[0].pk].pk,
            'new_provider_id':self.accounts[self.providers[2].pk].pk,
            'original_share':{'retained':2000, 'transferred':3000, 'agreement':'双方确认份额'}}]}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        rows = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order).order_by('segment__pk'))
        self.assertEqual([(x.incoming_basis, x.retained_basis, x.fixed_rule_total, x.commission_fixed) for x in rows],
                         [(5000, 2000, 1001, 501), (5000, None, 1001, 500), (3000, None, 2001, 601)])
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '正式转单逐段结算链仍在接线（18-盘点转单分段收敛），门禁保持关闭；最终联调启用')
    def test_same_rule_caps_incoming_fixed_fee_and_records_store_cost(self):
        from coupons.models import Coupon, UserCoupon
        from django.utils import timezone
        from datetime import timedelta
        self.service.price = 100; self.service.commission_fixed = 7
        self.service.save(update_fields=['price', 'commission_fixed'])
        coupon = Coupon.objects.create(name='隔离金额券', discount_type='DIRECT', threshold=0,
            amount=190, valid_to=timezone.now()+timedelta(days=1), is_active=True)
        owned = UserCoupon.objects.create(account=self.accounts[self.boss.pk], coupon=coupon)
        self.auth(self.boss)
        created = self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'dispatch_route':'STAFF', 'user_coupon_id':owned.pk,
            'client_request_id':'fixed-seat-store-shortfall'}, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order = Order.objects.get(pk=created.data['data']['id'])
        self.assertEqual((order.original_amount, order.amount), (200, 10))
        self.assertEqual(self.assign(order).data['code'], 0)
        for provider in self.providers[:2]:
            self.auth(provider)
            self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/start/',
                {'entry_image':team_fixture.screenshot()}, format='multipart').data['code'], 0)
        self.auth(self.staff)
        result = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'更换陪玩', 'transfers':[{
            'old_provider_id':self.accounts[self.providers[0].pk].pk,
            'new_provider_id':self.accounts[self.providers[2].pk].pk,
            'original_share':{'retained':80, 'transferred':20, 'agreement':'双方确认份额'}}]}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        rows = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order).order_by('segment__pk'))
        self.assertEqual([(x.incoming_basis, x.retained_basis, x.commission_fixed, x.income_at_entry) for x in rows],
                         [(5, 4, 4, 1), (5, None, 3, 2), (1, None, 1, 0)])
        allocation = OrderSegmentTransferBasis.objects.get(transfer__previous_segment__attribution__order=order)
        self.assertEqual(allocation.fixed_fee_store_shortfall, 1)
        detail = self.client.get(f'/api/admin/orders/{order.pk}/').data['data']['original_attribution']['financial_capture']
        self.assertEqual(detail['transfers'][0]['fixed_fee_store_shortfall'], 1)
        from orders.models import OrderParticipationSegment
        incoming = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[self.providers[2].pk], released_at__isnull=True)
        self.auth(self.providers[2])
        started = self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id':incoming.pk, 'entry_image':team_fixture.screenshot()}, format='multipart')
        self.assertEqual(started.data['code'], 0, started.data)
        order.refresh_from_db()
        from orders.segment_settlement_plan import build_segment_settlement_plan
        plan = build_segment_settlement_plan(order)
        self.assertEqual([(line.basis, line.fee, line.income) for line in plan.segments],
                         [(4, 2, 2), (5, 3, 2), (1, 1, 0)])
        self.assertEqual((plan.fixed_fee_shortfall, plan.store_income), (1, 6))
        self.auth(self.staff)
        returned = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'转回原陪玩', 'transfers':[{
            'old_provider_id':self.accounts[self.providers[2].pk].pk,
            'new_provider_id':self.accounts[self.providers[0].pk].pk,
            'original_share':{'retained':10, 'transferred':10, 'agreement':'双方确认转回'}}]}, format='json')
        self.assertEqual(returned.data['code'], 0, returned.data)
        allocations = list(OrderSegmentTransferBasis.objects.filter(
            transfer__previous_segment__attribution__order=order).order_by('pk'))
        self.assertEqual([item.fixed_fee_store_shortfall for item in allocations], [1, 0])
        last = OrderSegmentFinancialSnapshot.objects.get(segment__attribution__order=order,
            segment__provider_account=self.accounts[self.providers[0].pk], segment__released_at__isnull=True)
        self.assertEqual((last.incoming_basis, last.commission_fixed, last.income_at_entry), (1, 1, 0))
        self.auth(self.providers[0])
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id':last.segment_id, 'entry_image':team_fixture.screenshot()}, format='multipart').data['code'], 0)
        order.refresh_from_db()
        plan = build_segment_settlement_plan(order)
        self.assertEqual(plan.fixed_fee_shortfall, 1)
        self.assertEqual(sum(line.income for line in plan.segments) + plan.store_income + plan.inviter_income,
                         plan.deal_amount)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   GAME_COMMISSION_PAYMENT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   PRESTART_SCOPE_SNAPSHOT_ENABLED=True, SEGMENT_GAME_TRANSFER_ENABLED=True,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class SegmentFinancialGameTest(APITransactionTestCase):
    setUp = game_fixture.GameCommissionPaymentTest.setUp
    link = game_fixture.GameCommissionPaymentTest.link
    auth = game_fixture.GameCommissionPaymentTest.auth
    create = game_fixture.GameCommissionPaymentTest.create

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '正式转单逐段结算链仍在接线（18-盘点转单分段收敛），门禁保持关闭；最终联调启用')
    def test_paid_game_snapshot_survives_later_configuration_change(self):
        result = self.create(provider_id=self.accounts[0].pk)
        self.assertEqual(result.data['code'], 0, result.data)
        snapshot = OrderSegmentFinancialSnapshot.objects.get(segment__attribution__order_id=result.data['data']['id'])
        self.assertEqual((snapshot.commission_rate, snapshot.game_commission_snapshot['version']), (30, 1))
        self.config.rules = {'companion':11, 'grind':50}; self.config.version = 2; self.config.save()
        snapshot.refresh_from_db()
        self.assertEqual((snapshot.commission_rate, snapshot.game_commission_snapshot['version']), (30, 1))

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '正式转单逐段结算链仍在接线（18-盘点转单分段收敛），门禁保持关闭；最终联调启用')
    def test_game_transfer_captures_current_rule_and_return_creates_new_snapshot(self):
        from tempfile import TemporaryDirectory
        from club_accounts.services import get_or_create_account_for_legacy_user
        from orders.models import OrderParticipationSegment
        from orders.tests.factories import make_console_user
        with TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media, REQUIRE_ORDER_EVIDENCE_IMAGES=True):
            result = self.create(provider_id=self.accounts[0].pk)
            self.assertEqual(result.data['code'], 0, result.data)
            order = Order.objects.get(pk=result.data['data']['id'])
            self.auth(self.accounts[0])
            started = self.client.post(f'/api/orders/orders/{order.pk}/start/',
                {'entry_image':flow.screenshot()}, format='multipart')
            self.assertEqual(started.data['code'], 0, started.data)
            staff = make_console_user(['order:view', 'order:dispatch', 'report:view', 'report:audit'])
            operator = get_or_create_account_for_legacy_user(staff)
            staff.admin_membership.account = operator; staff.admin_membership.save(update_fields=['account'])
            self.config.rules = {'companion':11, 'grind':50}; self.config.version = 2; self.config.save()
            self.auth(operator)
            transferred = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'双方协商', 'transfers':[
                {'old_provider_id':self.accounts[0].pk, 'new_provider_id':self.accounts[1].pk,
                 'original_share':{'retained':2000, 'transferred':3000, 'agreement':'双方确认份额'}}]}, format='json')
            self.assertEqual(transferred.data['code'], 0, transferred.data)
            rows = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order).order_by('segment__pk'))
            self.assertEqual([(x.incoming_basis, x.retained_basis, x.commission_rate, x.income_at_entry,
                               x.game_commission_snapshot['version']) for x in rows],
                             [(5000, 2000, 30, 3500, 1), (3000, None, 50, 1500, 2)])
            self.auth(self.accounts[1])
            detail = self.client.get(f'/api/orders/orders/{order.pk}/').data['data']
            self.assertIsNone(detail['expected_income']); self.assertEqual(detail['settlement_status'], 'REVIEW')
            incoming = OrderParticipationSegment.objects.get(attribution__order=order,
                provider_account=self.accounts[1], released_at__isnull=True)
            started = self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
                {'segment_id':incoming.pk, 'entry_image':flow.screenshot()}, format='multipart')
            self.assertEqual(started.data['code'], 0, started.data)
            self.config.rules = {'companion':11, 'grind':40}; self.config.version = 3; self.config.save()
            self.auth(operator)
            transferred = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'继续协商', 'transfers':[
                {'old_provider_id':self.accounts[1].pk, 'new_provider_id':self.accounts[0].pk,
                 'original_share':{'retained':1000, 'transferred':2000, 'agreement':'双方确认转回'}}]}, format='json')
            self.assertEqual(transferred.data['code'], 0, transferred.data)
            rows = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order).order_by('segment__pk'))
            self.assertEqual([(x.incoming_basis, x.retained_basis, x.commission_rate,
                               x.game_commission_snapshot['version']) for x in rows],
                             [(5000, 2000, 30, 1), (3000, 1000, 50, 2), (2000, None, 40, 3)])
            self.assertEqual(OrderSegmentFinancialCoverage.objects.get(attribution__order=order).status, 'REVIEW')
            self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '正式转单逐段结算链仍在接线（18-盘点转单分段收敛），门禁保持关闭；最终联调启用')
    def test_missing_payment_scope_prevents_game_transfer_without_mutation(self):
        from tempfile import TemporaryDirectory
        from club_accounts.services import get_or_create_account_for_legacy_user
        from orders.tests.factories import make_console_user
        with TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media, REQUIRE_ORDER_EVIDENCE_IMAGES=True):
            with override_settings(PRESTART_SCOPE_SNAPSHOT_ENABLED=False):
                result = self.create(provider_id=self.accounts[0].pk)
            self.assertEqual(result.data['code'], 0, result.data)
            order = Order.objects.get(pk=result.data['data']['id'])
            self.auth(self.accounts[0])
            self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/start/',
                {'entry_image':flow.screenshot()}, format='multipart').data['code'], 0)
            staff = make_console_user(['order:view', 'order:dispatch'])
            operator = get_or_create_account_for_legacy_user(staff)
            staff.admin_membership.account = operator; staff.admin_membership.save(update_fields=['account'])
            self.auth(operator)
            result = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'双方协商', 'transfers':[
                {'old_provider_id':self.accounts[0].pk, 'new_provider_id':self.accounts[1].pk,
                 'original_share':{'retained':2000, 'transferred':3000, 'agreement':'双方确认份额'}}]}, format='json')
            self.assertNotEqual(result.data['code'], 0)
            order.refresh_from_db(); self.assertEqual(order.provider_account_id, self.accounts[0].pk)
            self.assertFalse(OrderSegmentTransferBasis.objects.filter(transfer__previous_segment__attribution__order=order).exists())

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '正式转单逐段结算链仍在接线（18-盘点转单分段收敛），门禁保持关闭；最终联调启用')
    def test_missing_current_game_rule_rejects_transfer_and_keeps_old_snapshot(self):
        from tempfile import TemporaryDirectory
        from club_accounts.services import get_or_create_account_for_legacy_user
        from orders.tests.factories import make_console_user
        with TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media, REQUIRE_ORDER_EVIDENCE_IMAGES=True):
            result = self.create(provider_id=self.accounts[0].pk)
            order = Order.objects.get(pk=result.data['data']['id'])
            self.auth(self.accounts[0])
            self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/start/',
                {'entry_image':flow.screenshot()}, format='multipart').data['code'], 0)
            staff = make_console_user(['order:view', 'order:dispatch'])
            operator = get_or_create_account_for_legacy_user(staff)
            staff.admin_membership.account = operator; staff.admin_membership.save(update_fields=['account'])
            self.config.rules = {'companion':11}; self.config.version = 2; self.config.save()
            self.auth(operator)
            result = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'双方协商', 'transfers':[
                {'old_provider_id':self.accounts[0].pk, 'new_provider_id':self.accounts[1].pk,
                 'original_share':{'retained':2000, 'transferred':3000, 'agreement':'双方确认份额'}}]}, format='json')
            self.assertNotEqual(result.data['code'], 0)
            order.refresh_from_db(); self.assertEqual(order.provider_account_id, self.accounts[0].pk)
            self.assertFalse(OrderSegmentTransferBasis.objects.filter(transfer__previous_segment__attribution__order=order).exists())
            original = OrderSegmentFinancialSnapshot.objects.get(segment__attribution__order=order)
            self.assertEqual((original.commission_rate, original.game_commission_snapshot['version']), (30, 1))

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '正式转单逐段结算链仍在接线（18-盘点转单分段收敛），门禁保持关闭；最终联调启用')
    def test_retained_provider_can_submit_evidence_but_legacy_earnings_cannot_be_approved(self):
        from tempfile import TemporaryDirectory
        from club_accounts.services import get_or_create_account_for_legacy_user
        from orders.tests.factories import make_console_user
        from wallet.models import ProviderReport
        with TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media, REQUIRE_ORDER_EVIDENCE_IMAGES=True):
            result = self.create(provider_id=self.accounts[0].pk)
            order = Order.objects.get(pk=result.data['data']['id'])
            self.auth(self.accounts[0])
            self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/start/',
                {'entry_image':flow.screenshot()}, format='multipart').data['code'], 0)
            staff = make_console_user(['order:view', 'order:dispatch', 'report:view', 'report:audit'])
            operator = get_or_create_account_for_legacy_user(staff)
            staff.admin_membership.account = operator; staff.admin_membership.save(update_fields=['account'])
            self.auth(operator)
            transferred = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'双方协商', 'transfers':[
                {'old_provider_id':self.accounts[0].pk, 'new_provider_id':self.accounts[1].pk,
                 'original_share':{'retained':2000, 'transferred':3000, 'agreement':'双方确认份额'}}]}, format='json')
            self.assertEqual(transferred.data['code'], 0, transferred.data)
            self.auth(self.accounts[0])
            detail = self.client.get(f'/api/orders/orders/{order.pk}/').data['data']
            self.assertTrue(detail['report_entry']['available'])
            self.assertIsNone(detail['expected_income'])
            report = ProviderReport.objects.get(order=order, provider_account=self.accounts[0])
            for kind in ('COMPLETION', 'RESULT'):
                upload = self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                    {'kind':kind, 'image':flow.screenshot()}, format='multipart')
                self.assertEqual(upload.data['code'], 0, upload.data)
            self.auth(self.accounts[1])
            from orders.models import OrderParticipationSegment
            incoming = OrderParticipationSegment.objects.get(attribution__order=order,
                provider_account=self.accounts[1], released_at__isnull=True)
            self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
                {'segment_id':incoming.pk, 'entry_image':flow.screenshot()}, format='multipart').data['code'], 0)
            self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/complete/',
                {'completion_image':flow.screenshot()}, format='multipart').data['code'], 0)
            self.auth(self.accounts[0])
            submitted = self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
            self.assertEqual(submitted.data['code'], 0, submitted.data)
            for field in ('amount', 'commission_rate', 'payout_amount', 'expected_income'):
                self.assertIsNone(submitted.data['data'][field], field)
            self.assertEqual(submitted.data['data']['settlement_status'], 'REVIEW')
            self.auth(operator)
            preview = self.client.get(f'/api/admin/reports/{report.pk}/').data['data']
            self.assertIn('逐段核对', preview['order_approval_block_reason'])
            blocked = self.client.get('/api/admin/reports/', {'order_blocked':'1'}).data['data']
            self.assertIn(report.pk, [row['id'] for row in blocked['list']])
            denied = self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
            self.assertNotEqual(denied.data['code'], 0)
            report.refresh_from_db(); self.assertEqual(report.status, 'PENDING')
            self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
            from django.utils import timezone
            changed = order.providers.filter(provider_account=self.accounts[0]).update(settled_at=timezone.now())
            self.assertEqual(changed, 1)
            preview = self.client.get(f'/api/admin/reports/{report.pk}/').data['data']
            self.assertEqual(preview['order_approval_block_reason'], '')
            blocked = self.client.get('/api/admin/reports/', {'order_blocked':'1'}).data['data']
            self.assertNotIn(report.pk, [row['id'] for row in blocked['list']])


@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class SegmentFinancialStoreTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    @override_settings(SEGMENT_STANDARD_TRANSFER_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
                       REQUIRE_ORDER_EVIDENCE_IMAGES=False)
    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '正式转单逐段结算链仍在接线（18-盘点转单分段收敛），门禁保持关闭；最终联调启用')
    def test_same_identifiers_use_each_store_current_transfer_rule(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from club_accounts.tokens import issue_account_tokens
        from orders.tests.factories import make_console_user, make_provider, make_service, make_user
        from rest_framework.test import APIClient
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        from wallet.services import get_wallet
        results = []
        for store, host, rate in ((resolve_store(), 'sl.esports.club', 35),
                                  (xinan_store(), 'xa.esports.club', 45)):
            with store_context(store):
                boss = make_user(); buyer = get_or_create_account_for_legacy_user(boss)
                wallet = get_wallet(account=buyer, user=boss); wallet.balance = 5000; wallet.save(update_fields=['balance'])
                players = [make_provider(deposit_mode='EARNINGS') for _ in range(2)]
                accounts = [get_or_create_account_for_legacy_user(player) for player in players]
                for player, account in zip(players, accounts):
                    player.escort_profile.account = account; player.escort_profile.save(update_fields=['account'])
                service = make_service(price=1000, commission_override=True, commission_rate=20)
                staff = make_console_user(['order:view', 'order:dispatch']); operator = get_or_create_account_for_legacy_user(staff)
                staff.admin_membership.account = operator; staff.admin_membership.save(update_fields=['account'])
                client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(buyer)[0])
                created = client.post('/api/orders/create/', {'service_id':service.pk, 'provider_id':accounts[0].pk,
                    'client_request_id':'standard-transfer-two-stores'}, format='json')
                self.assertEqual(created.data['code'], 0, created.data)
                order_id = created.data['data']['id']
                client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(accounts[0])[0])
                self.assertEqual(client.post(f'/api/orders/orders/{order_id}/start/', {}, format='json').data['code'], 0)
                service.commission_rate = rate; service.save(update_fields=['commission_rate'])
                client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(operator)[0])
                transferred = client.post(f'/api/admin/orders/{order_id}/transfer/', {'reason':'本店核对', 'transfers':[
                    {'old_provider_id':accounts[0].pk, 'new_provider_id':accounts[1].pk,
                     'original_share':{'retained':400, 'transferred':600, 'agreement':'双方确认'}}]}, format='json')
                self.assertEqual(transferred.data['code'], 0, transferred.data)
                entry = OrderSegmentFinancialSnapshot.objects.get(segment__attribution__order_id=order_id,
                                                                    segment__provider_account=accounts[1])
                results.append((order_id, accounts[1].pk, entry.commission_rate, entry.income_at_entry))
        self.assertEqual(results[0][:2], results[1][:2])
        self.assertEqual([result[2:] for result in results], [(35,390), (45,330)])

    @skipUnless(os.environ.get('XA_SEGMENT_TRANSFER_FORMAL') == '1',
                '正式转单逐段结算链仍在接线（18-盘点转单分段收敛），门禁保持关闭；最终联调启用')
    def test_equal_order_and_account_ids_keep_own_deal_and_rate(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from club_accounts.tokens import issue_account_tokens
        from orders.tests.factories import make_console_user, make_provider, make_service, make_user
        from rest_framework.test import APIClient
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        from wallet.services import get_wallet
        results = []
        for store, host, price, rate in ((resolve_store(), 'sl.esports.club', 1000, 20),
                                          (xinan_store(), 'xa.esports.club', 2000, 30)):
            with store_context(store):
                boss = make_user(); buyer = get_or_create_account_for_legacy_user(boss)
                wallet = get_wallet(account=buyer, user=boss); wallet.balance = 5000; wallet.save(update_fields=['balance'])
                provider = make_provider(deposit_mode='EARNINGS'); player = get_or_create_account_for_legacy_user(provider)
                provider.escort_profile.account = player; provider.escort_profile.save(update_fields=['account'])
                incoming = make_provider(deposit_mode='EARNINGS'); next_player = get_or_create_account_for_legacy_user(incoming)
                incoming.escort_profile.account = next_player; incoming.escort_profile.save(update_fields=['account'])
                service = make_service(price=price, commission_override=True, commission_rate=rate)
                staff = make_console_user(['order:view', 'order:dispatch']); operator = get_or_create_account_for_legacy_user(staff)
                staff.admin_membership.account = operator; staff.admin_membership.save(update_fields=['account'])
                client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(buyer)[0])
                result = client.post('/api/orders/create/', {'service_id':service.pk, 'provider_id':player.pk,
                    'client_request_id':'segment-financial-store'}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
                order_id = result.data['data']['id']
                if store.slug == 'shenglang':
                    client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(player)[0])
                    started = client.post(f'/api/orders/orders/{order_id}/start/', {}, format='json')
                    self.assertEqual(started.data['code'], 0, started.data)
                    client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(operator)[0])
                    transfer = client.post(f'/api/admin/orders/{order_id}/transfer/', {'reason':'本店核对', 'transfers':[
                        {'old_provider_id':player.pk, 'new_provider_id':next_player.pk,
                         'original_share':{'retained':400, 'transferred':600, 'agreement':'双方确认'}}]}, format='json')
                    self.assertEqual(transfer.data['code'], 0, transfer.data)
                client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(operator)[0])
                detail = client.get(f'/api/admin/orders/{order_id}/').data['data']['original_attribution']['financial_capture']
                results.append((order_id, player.pk, detail['deal_amount'], detail['segments'][0]['commission_rate'],
                                detail['status'], len(detail['transfers'])))
        self.assertEqual(results[0][:2], results[1][:2])
        self.assertEqual([row[2:] for row in results], [(1000,20,'REVIEW',1), (2000,30,'ACTIVE',0)])
