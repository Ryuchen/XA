"""Real transfer, segment reports and platform-funded settlement."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from stores.db import transaction

from coupons import test_coupon_availability_workflow as coupon_workflow
from orders.models import Order, OrderOriginalShareTransfer, OrderSegmentReportSubmission
from orders.tests import test_safebox_segment_capture as capture_fixture
from orders.tests.test_release_flow import screenshot
from wallet.models import Transaction, get_platform_wallet
from wallet.services import get_wallet


@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=True,
                   VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True, VIP_SAFE_BOX_SEGMENT_SETTLEMENT_TESTING=True,
                   ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   SEGMENT_STANDARD_TRANSFER_ENABLED=True, SEGMENT_REPORT_ENABLED=True,
                   PRESTART_SCOPE_SNAPSHOT_ENABLED=True, PRESTART_HANDOFF_ENABLED=True,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class SafeBoxSegmentSettlementTest(TestCase):
    setUp = coupon_workflow.CouponAvailabilityWorkflowTest.setUp
    client_for = staticmethod(coupon_workflow.CouponAvailabilityWorkflowTest.client_for)
    ok = coupon_workflow.CouponAvailabilityWorkflowTest.ok
    prepare = capture_fixture.SafeBoxSegmentCaptureTest.prepare

    def finished(self, *, retained=4000):
        _, players, staff, order_id = self.prepare()
        self.ok(staff.post(f'/api/admin/orders/{order_id}/assign/', {'provider_id':players[0].pk}, format='json'))
        first, second = (self.client_for(player) for player in players)
        self.ok(first.post(f'/api/orders/orders/{order_id}/start/', {'entry_image':screenshot()}, format='multipart'))
        self.ok(staff.post(f'/api/admin/orders/{order_id}/transfer/', {'reason':'双方协商转单', 'transfers':[
            {'old_provider_id':players[0].pk, 'new_provider_id':players[1].pk, 'split_value':0,
             'original_share':{'retained':retained, 'transferred':10000-retained, 'agreement':'双方确认原价份额'}}]}, format='json'))
        transfer = OrderOriginalShareTransfer.objects.get(previous_segment__attribution__order_id=order_id)
        old, current = transfer.previous_segment, transfer.next_segment
        self.ok(first.post(f'/api/orders/orders/{order_id}/participation-evidence/',
            {'segment_id':old.pk, 'completion_image':screenshot()}, format='multipart'))
        self.ok(second.post(f'/api/orders/orders/{order_id}/participation-start/',
            {'segment_id':current.pk, 'entry_image':screenshot()}, format='multipart'))
        self.ok(second.post(f'/api/orders/orders/{order_id}/complete/',
            {'completion_image':screenshot()}, format='multipart'))
        return Order.objects.get(pk=order_id), players, staff, (first, second), (old, current)

    def report(self, order, staff, client, segment):
        path = f'/api/orders/orders/{order.pk}/segment-report/'
        self.ok(client.post(path, {'segment_id':segment.pk, 'action':'open', 'client_request_id':f'open-{segment.pk}'}, format='json'))
        self.ok(client.post(path, {'segment_id':segment.pk, 'action':'upload', 'client_request_id':f'upload-{segment.pk}',
                                   'image':screenshot()}, format='multipart'))
        self.ok(client.post(path, {'segment_id':segment.pk, 'action':'submit', 'version':0,
                                   'client_request_id':f'submit-{segment.pk}'}, format='json'))
        self.ok(staff.post(f'/api/admin/orders/{order.pk}/segments/{segment.pk}/report-review/',
                           {'action':'approve', 'version':1}, format='json'))

    def settle(self, order, now):
        from orders.services import settle_order
        with patch('django.utils.timezone.now', return_value=now), transaction.atomic():
            return settle_order(Order.objects.select_for_update().get(pk=order.pk))

    def test_whole_funding_then_independent_report_clocks_and_exactly_once(self):
        from orders.models import OrderSegmentPersonSettlement
        order, players, staff, clients, segments = self.finished()
        self.report(order, staff, clients[0], segments[0]); self.report(order, staff, clients[1], segments[1])
        first = OrderSegmentReportSubmission.objects.get(report__segment=segments[0])
        second = OrderSegmentReportSubmission.objects.get(report__segment=segments[1])
        later = first.submitted_at + timedelta(hours=10)
        OrderSegmentReportSubmission.objects.filter(pk=second.pk).update(submitted_at=later, reviewed_at=later)
        boundary = max(order.completed_at, first.submitted_at) + timedelta(hours=72, minutes=1)
        platform = get_platform_wallet(); platform.balance=7999; platform.save(update_fields=['balance'])
        self.assertEqual(self.settle(order, boundary), [])
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').exists())
        platform.refresh_from_db(); platform.balance=8000; platform.save(update_fields=['balance'])
        self.assertEqual(self.settle(order, boundary), [players[0].legacy_mapping.legacy_user_id])
        self.assertEqual(self.settle(order, boundary), [])
        self.assertEqual((get_wallet(account=players[0]).balance, get_wallet(account=players[1]).balance), (3200, 0))
        with patch('django.utils.timezone.now', return_value=boundary):
            own = self.ok(clients[0].get(f'/api/orders/orders/{order.pk}/'))
            other = self.ok(clients[1].get(f'/api/orders/orders/{order.pk}/'))
            self.assertEqual((own['expected_income'], own['settlement_status']), (3200, 'SETTLED'))
            self.assertEqual((other['expected_income'], other['settlement_status']), (None, 'REVIEW'))
        self.assertEqual(self.settle(order, later+timedelta(hours=72, minutes=1)), [players[1].legacy_mapping.legacy_user_id])
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.assertEqual(OrderSegmentPersonSettlement.objects.filter(manifest__order=order).count(), 2)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').count(), 2)
        self.assertEqual((get_wallet(account=players[0]).balance, get_wallet(account=players[1]).balance), (3200, 4800))
        from engagement.confirmation import process_order
        with patch('django.utils.timezone.now', return_value=later+timedelta(hours=73)):
            self.assertTrue(process_order(order.pk))
        order.refresh_from_db(); self.assertIsNotNone(order.consumption_confirmed_at)

    def test_tampered_paid_receipt_blocks_other_person(self):
        from orders.models import OrderSegmentPersonSettlement
        from orders.settlement import SettlementError
        order, players, staff, clients, segments = self.finished()
        self.report(order, staff, clients[0], segments[0]); self.report(order, staff, clients[1], segments[1])
        later = timezone.now()+timedelta(hours=10)
        OrderSegmentReportSubmission.objects.filter(report__segment=segments[1]).update(submitted_at=later, reviewed_at=later)
        now = timezone.now()+timedelta(hours=73)
        platform = get_platform_wallet(); platform.balance=8000; platform.save(update_fields=['balance'])
        self.assertEqual(self.settle(order, now), [players[0].legacy_mapping.legacy_user_id])
        record = OrderSegmentPersonSettlement.objects.get(manifest__order=order)
        record.amount += 1; record.save(update_fields=['amount'])
        with self.assertRaises(SettlementError):
            self.settle(order, later+timedelta(hours=73))
        from orders.subsidies import settle_platform_subsidy_share
        share = order.platform_subsidy.shares.get(assignment__provider_account=players[0])
        with patch('django.utils.timezone.now', return_value=later+timedelta(hours=73)):
            with self.assertRaises(SettlementError):
                settle_platform_subsidy_share(share.pk)
        self.assertEqual(get_wallet(account=players[1]).balance, 0)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').count(), 1)
        with patch('django.utils.timezone.now', return_value=later+timedelta(hours=73)):
            display = self.ok(clients[0].get(f'/api/orders/orders/{order.pk}/'))
        self.assertEqual((display['expected_income'], display['settlement_status']), (None, 'REVIEW'))

    def test_disabling_flags_cannot_reopen_legacy_refund_after_partial_payout(self):
        from orders.refunds import RefundError
        from orders.segment_settlement import reject_legacy_refund
        order, players, staff, clients, segments = self.finished()
        self.report(order, staff, clients[0], segments[0])
        platform = get_platform_wallet(); platform.balance=8000; platform.save(update_fields=['balance'])
        self.assertEqual(self.settle(order, timezone.now()+timedelta(hours=73)), [players[0].legacy_mapping.legacy_user_id])
        with override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=False, VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=False,
                               VIP_SAFE_BOX_SEGMENT_SETTLEMENT_TESTING=False, SEGMENT_SETTLEMENT_ENABLED=False,
                               SEGMENT_SETTLEMENT_ENFORCE_ENABLED=False, ORIGINAL_ATTRIBUTION_ENABLED=False,
                               SEGMENT_ECONOMICS_CAPTURE_ENABLED=False, SEGMENT_ECONOMICS_ENFORCE_ENABLED=False):
            with self.assertRaises(RefundError):
                reject_legacy_refund(order)
            self.assertEqual(self.settle(order, timezone.now()+timedelta(hours=90)), [])
            order.refresh_from_db(); self.assertIsNone(order.settled_at)
            self.assertEqual(get_wallet(account=players[1]).balance, 0)

    def test_disabling_subsidy_switch_does_not_bypass_original_order_refund_guard(self):
        from orders.services import refund_to_customer
        from orders.settlement import SettlementError
        _, players, staff, order_id = self.prepare()
        self.ok(staff.post(f'/api/admin/orders/{order_id}/assign/', {'provider_id':players[0].pk}, format='json'))
        self.ok(self.client_for(players[0]).post(f'/api/orders/orders/{order_id}/start/',
                                                {'entry_image':screenshot()}, format='multipart'))
        order = Order.objects.get(pk=order_id)
        with override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=False):
            with self.assertRaises(SettlementError):
                refund_to_customer(order)
        self.assertFalse(order.refund_records.exists())

    def test_income_uses_order_deposit_snapshot_and_records_both_sides(self):
        from orders.models import OrderSegmentPersonSettlement
        order, players, staff, clients, segments = self.finished()
        profile = players[0].escort_profile
        profile.deposit_mode = 'EARNINGS'; profile.deposit_paid = 0
        profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        self.report(order, staff, clients[0], segments[0]); self.report(order, staff, clients[1], segments[1])
        platform = get_platform_wallet(); platform.balance=8000; platform.save(update_fields=['balance'])
        self.assertEqual(len(self.settle(order, timezone.now()+timedelta(hours=73))), 2)
        record = OrderSegmentPersonSettlement.objects.get(manifest__order=order, account=players[0])
        profile.refresh_from_db()
        self.assertEqual((record.income_transaction.amount, record.deposit_debit.amount,
                          record.deposit_credit.amount, profile.deposit_paid, get_wallet(account=players[0]).balance),
                         (3200, -1920, 1920, 1920, 1280))

    @override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True)
    def test_refund_gap_is_repaid_before_ordinary_order_deposit(self):
        from orders.models import OrderSegmentPersonSettlement
        from orders.tests.factories import make_completed_order
        from wallet.models import RefundDepositIncomeApplication
        from wallet.test_refund_deposit_deficit import RefundDepositDeficitTest
        order, players, staff, clients, segments = self.finished()
        self.profile = players[0].escort_profile
        self.profile.deposit_mode='EARNINGS'; self.profile.deposit_paid=1000
        self.profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        self.wallet = get_wallet(account=players[0])
        platform = get_platform_wallet(); platform.balance=10000; platform.save(update_fields=['balance'])
        self.order = make_completed_order(order.customer, self.profile.user,
            amount=10000, provider_account=players[0], payment_status='PAID')
        deficit = RefundDepositDeficitTest.deficit(self, 200)
        self.report(order, staff, clients[0], segments[0]); self.report(order, staff, clients[1], segments[1])
        self.assertEqual(len(self.settle(order, timezone.now()+timedelta(hours=73))), 2)
        record = OrderSegmentPersonSettlement.objects.get(manifest__order=order, account=players[0])
        application = RefundDepositIncomeApplication.objects.get(source_transaction=record.income_transaction)
        deficit.refresh_from_db(); self.profile.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((application.applied_amount, deficit.remaining, record.deposit_debit.amount,
                          record.deposit_credit.amount, self.wallet.balance, self.profile.deposit_paid),
                         (200, 0, -1800, 1800, 1200, 2800))

    def test_open_case_blocks_only_affected_person_and_later_finalizes(self):
        from orders.models import AfterSale
        order, players, staff, clients, segments = self.finished()
        self.report(order, staff, clients[0], segments[0]); self.report(order, staff, clients[1], segments[1])
        case = AfterSale.objects.create(order=order, account=order.customer_account, reason='本人售后',
                                        affected_provider_account_ids=[players[0].pk])
        platform = get_platform_wallet(); platform.balance=8000; platform.save(update_fields=['balance'])
        now = timezone.now()+timedelta(hours=73)
        self.assertEqual(self.settle(order, now), [players[1].legacy_mapping.legacy_user_id])
        order.refresh_from_db(); self.assertIsNone(order.settled_at)
        case.status='RESOLVED'; case.save(update_fields=['status'])
        self.assertEqual(self.settle(order, now), [players[0].legacy_mapping.legacy_user_id])
        order.refresh_from_db(); self.assertIsNotNone(order.settled_at)

    def test_failure_after_credit_rolls_back_all_ledgers(self):
        from orders.models import OrderSegmentSettlementManifest
        order, players, staff, clients, segments = self.finished()
        self.report(order, staff, clients[0], segments[0]); self.report(order, staff, clients[1], segments[1])
        platform = get_platform_wallet(); platform.balance=8000; platform.save(update_fields=['balance'])
        with patch('orders.segment_settlement._report_versions', side_effect=RuntimeError('报单凭证故障')):
            with self.assertRaisesMessage(RuntimeError, '报单凭证故障'):
                self.settle(order, timezone.now()+timedelta(hours=73))
        platform.refresh_from_db(); order.refresh_from_db()
        self.assertEqual((platform.balance, get_wallet(account=players[0]).balance, order.settled_at), (8000, 0, None))
        self.assertFalse(OrderSegmentSettlementManifest.objects.filter(order=order).exists())
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').exists())

    def test_nonretained_transferred_provider_needs_no_fabricated_payment(self):
        order, players, staff, clients, segments = self.finished(retained=0)
        self.report(order, staff, clients[1], segments[1])
        platform = get_platform_wallet(); platform.balance=8000; platform.save(update_fields=['balance'])
        self.assertEqual(self.settle(order, timezone.now()+timedelta(hours=73)), [players[1].legacy_mapping.legacy_user_id])
        order.refresh_from_db(); self.assertIsNotNone(order.settled_at)
        self.assertEqual((get_wallet(account=players[0]).balance, get_wallet(account=players[1]).balance), (0, 8000))
        profile = players[0].escort_profile; profile.refresh_from_db()
        self.assertEqual(profile.completed_order_count, 1)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').count(), 1)

    def test_scheduled_retry_and_consumption_confirmation_are_idempotent(self):
        from engagement.tasks import confirm_due_consumption, retry_completion_subsidies
        order, players, staff, clients, segments = self.finished()
        self.report(order, staff, clients[0], segments[0]); self.report(order, staff, clients[1], segments[1])
        platform = get_platform_wallet(); platform.balance=8000; platform.save(update_fields=['balance'])
        with patch('django.utils.timezone.now', return_value=timezone.now()+timedelta(hours=73)):
            self.assertEqual(retry_completion_subsidies.run(), 1)
            self.assertEqual(retry_completion_subsidies.run(), 0)
            self.assertEqual(confirm_due_consumption.run(), 1)
            self.assertEqual(confirm_due_consumption.run(), 0)
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at); self.assertIsNotNone(order.consumption_confirmed_at)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').count(), 2)

    def test_returned_provider_waits_for_both_reports_and_is_paid_once(self):
        from orders.models import OrderParticipationSegment, OrderSegmentPersonSettlement
        _, players, staff, order_id = self.prepare()
        first, second = (self.client_for(player) for player in players)
        self.ok(staff.post(f'/api/admin/orders/{order_id}/assign/', {'provider_id':players[0].pk}, format='json'))
        self.ok(first.post(f'/api/orders/orders/{order_id}/start/', {'entry_image':screenshot()}, format='multipart'))
        self.ok(staff.post(f'/api/admin/orders/{order_id}/transfer/', {'reason':'双方协商转单', 'transfers':[
            {'old_provider_id':players[0].pk, 'new_provider_id':players[1].pk, 'split_value':0,
             'original_share':{'retained':4000, 'transferred':6000, 'agreement':'双方确认原价份额'}}]}, format='json'))
        old = OrderParticipationSegment.objects.get(attribution__order_id=order_id, provider_account=players[0])
        middle = OrderParticipationSegment.objects.get(attribution__order_id=order_id, provider_account=players[1])
        self.ok(first.post(f'/api/orders/orders/{order_id}/participation-evidence/',
            {'segment_id':old.pk, 'completion_image':screenshot()}, format='multipart'))
        self.ok(second.post(f'/api/orders/orders/{order_id}/participation-start/',
            {'segment_id':middle.pk, 'entry_image':screenshot()}, format='multipart'))
        self.ok(staff.post(f'/api/admin/orders/{order_id}/transfer/', {'reason':'双方协商转回', 'transfers':[
            {'old_provider_id':players[1].pk, 'new_provider_id':players[0].pk, 'split_value':0,
             'original_share':{'retained':2000, 'transferred':4000, 'agreement':'双方确认再次分配'}}]}, format='json'))
        returned = OrderParticipationSegment.objects.get(attribution__order_id=order_id,
            provider_account=players[0], released_at__isnull=True)
        self.ok(second.post(f'/api/orders/orders/{order_id}/participation-evidence/',
            {'segment_id':middle.pk, 'completion_image':screenshot()}, format='multipart'))
        self.ok(first.post(f'/api/orders/orders/{order_id}/participation-start/',
            {'segment_id':returned.pk, 'entry_image':screenshot()}, format='multipart'))
        self.ok(first.post(f'/api/orders/orders/{order_id}/complete/',
            {'completion_image':screenshot()}, format='multipart'))
        order = Order.objects.get(pk=order_id)
        self.report(order, staff, first, old); self.report(order, staff, second, middle)
        self.report(order, staff, first, returned)
        last = timezone.now()+timedelta(hours=10)
        OrderSegmentReportSubmission.objects.filter(report__segment=returned).update(submitted_at=last, reviewed_at=last)
        platform = get_platform_wallet(); platform.balance=8000; platform.save(update_fields=['balance'])
        self.assertEqual(self.settle(order, timezone.now()+timedelta(hours=73)), [players[1].legacy_mapping.legacy_user_id])
        self.assertEqual((get_wallet(account=players[0]).balance, get_wallet(account=players[1]).balance), (0, 1600))
        self.assertEqual(self.settle(order, last+timedelta(hours=73)), [players[0].legacy_mapping.legacy_user_id])
        record = OrderSegmentPersonSettlement.objects.get(manifest__order=order, account=players[0])
        self.assertEqual((record.amount, record.segment_ids), (6400, [old.pk, returned.pk]))
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').count(), 2)


@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=True,
                   VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True, VIP_SAFE_BOX_SEGMENT_SETTLEMENT_TESTING=True,
                   ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   SEGMENT_STANDARD_TRANSFER_ENABLED=True, SEGMENT_REPORT_ENABLED=True,
                   PRESTART_SCOPE_SNAPSHOT_ENABLED=True, PRESTART_HANDOFF_ENABLED=True,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
@skipUnlessDBFeature('has_select_for_update')
class SafeBoxSegmentSettlementRaceTest(TransactionTestCase):
    setUp = coupon_workflow.CouponAvailabilityWorkflowTest.setUp
    client_for = staticmethod(coupon_workflow.CouponAvailabilityWorkflowTest.client_for)
    ok = coupon_workflow.CouponAvailabilityWorkflowTest.ok
    prepare = capture_fixture.SafeBoxSegmentCaptureTest.prepare
    finished = SafeBoxSegmentSettlementTest.finished
    report = SafeBoxSegmentSettlementTest.report

    def test_concurrent_settlement_posts_each_share_once(self):
        from orders.models import OrderSegmentPersonSettlement
        from orders.services import settle_order
        order, players, staff, clients, segments = self.finished()
        self.report(order, staff, clients[0], segments[0]); self.report(order, staff, clients[1], segments[1])
        platform = get_platform_wallet(); platform.balance=8000; platform.save(update_fields=['balance'])
        gate = Barrier(2)
        def run(_):
            close_old_connections()
            try:
                gate.wait(timeout=10)
                with transaction.atomic():
                    locked = Order.objects.select_for_update().get(pk=order.pk)
                    return settle_order(locked)
            finally:
                close_old_connections()
        with patch('django.utils.timezone.now', return_value=timezone.now()+timedelta(hours=73)):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(run, (0, 1)))
        self.assertEqual(sorted(len(result) for result in results), [0, 2])
        self.assertEqual(OrderSegmentPersonSettlement.objects.filter(manifest__order=order).count(), 2)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').count(), 2)
        self.assertEqual((get_wallet(account=players[0]).balance, get_wallet(account=players[1]).balance), (3200, 4800))


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=True,
                   VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True, VIP_SAFE_BOX_SEGMENT_SETTLEMENT_TESTING=True,
                   ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   SEGMENT_STANDARD_TRANSFER_ENABLED=True, SEGMENT_REPORT_ENABLED=True,
                   PRESTART_SCOPE_SNAPSHOT_ENABLED=True, PRESTART_HANDOFF_ENABLED=True,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class SafeBoxSegmentSettlementStoresTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    client_for = staticmethod(capture_fixture.SafeBoxSegmentCaptureStoresTest.client_for)
    ok = coupon_workflow.CouponAvailabilityWorkflowTest.ok
    prepare = capture_fixture.SafeBoxSegmentCaptureTest.prepare
    finished = SafeBoxSegmentSettlementTest.finished
    report = SafeBoxSegmentSettlementTest.report
    settle = SafeBoxSegmentSettlementTest.settle

    def test_settlement_never_touches_other_store_with_same_ids(self):
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        entries = []
        for store in (resolve_store(), xinan_store()):
            with store_context(store):
                coupon_workflow.CouponAvailabilityWorkflowTest.setUp(self)
                order, players, staff, clients, segments = self.finished()
                self.report(order, staff, clients[0], segments[0]); self.report(order, staff, clients[1], segments[1])
                platform = get_platform_wallet(); platform.balance=8000; platform.save(update_fields=['balance'])
                entries.append((store, order.pk, [player.pk for player in players]))
        first, second = entries
        self.assertEqual(first[1:], second[1:])
        with store_context(first[0]):
            order = Order.objects.get(pk=first[1])
            self.assertEqual(len(self.settle(order, timezone.now()+timedelta(hours=73))), 2)
            self.assertEqual(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').count(), 2)
        with store_context(second[0]):
            order = Order.objects.get(pk=second[1])
            self.assertIsNone(order.settled_at)
            self.assertFalse(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').exists())
            self.assertEqual([get_wallet(account_id=pk).balance for pk in second[2]], [0, 0])
