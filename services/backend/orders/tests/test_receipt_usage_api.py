"""Shared-receipt allocation trial through isolated platform APIs."""
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.db import DatabaseError
from django.test import TestCase, override_settings

from orders.models import OrderPointsFundingAllocation
from orders.tests import test_points_recharge_closure as base
from orders.tests import test_points_redemption_preview as quote_fixture
from stores import test_platform_pass_repayment as platform_tests
from stores.context import store_context
from stores.db import transaction
from users.models import PassReceiptUsageEvent, PassRefundRepaymentAllocation
from users.receipt_usage_ledger import initialize_empty_locked
from users.receipt_usage_sources import verify_allocations_locked


@override_settings(**base.PointsRealRechargeClosureTest._overridden_settings)
@override_settings(RECEIPT_USAGE_LEDGER_INTERNAL_TESTING=True, RECEIPT_USAGE_ALLOCATIONS_INTERNAL_TESTING=True)
@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class ReceiptUsageApiTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    setUp = base.PointsRealRechargeClosureTest.setUp
    payload = quote_fixture.PointsServiceQuoteTest.payload
    fund = base.PointsRealRechargeClosureTest.fund
    verify = base.PointsRealRechargeClosureTest.verify

    def test_shared_receipt_pending_recovery_and_event_atomicity(self):
        first_refund, _ = platform_tests.PlatformPassRepaymentTests.setup_refunds(self)
        self.assertEqual(self.verify().status_code, 200)
        with store_context(self.second), transaction.atomic():
            initialize_empty_locked(self.receipt_id)
        pass_data = {'request_id': str(uuid4()), 'refund_id': first_refund, 'amount': 200}
        original = platform_tests.PlatformPassRepaymentTests.action
        from stores.models import PlatformAuditEvent
        create_audit = PlatformAuditEvent.objects.create

        def fail_pass_result(**kwargs):
            if kwargs.get('action') == 'pass_repayment.allocate_completed':
                raise DatabaseError('隔离测试平台结果提交失败')
            return create_audit(**kwargs)

        with patch('stores.platform_pass_repayment.PlatformAuditEvent.objects.create', side_effect=fail_pass_result):
            failed = original(self, self.claim, 'allocate', pass_data)
        self.assertEqual(failed.status_code, 503, failed.data)
        with store_context(self.second), transaction.atomic():
            state = verify_allocations_locked(self.receipt_id)
            self.assertEqual((state.pass_units, state.order_units, state.version), (200, 0, 1))
            self.assertEqual(PassRefundRepaymentAllocation.objects.filter(receipt_id=self.receipt_id).count(), 1)
        blocked_data = {'request_id': str(uuid4()), 'order_id': self.order.pk, 'amount': 300}
        self.assertNotEqual(self.fund(blocked_data).status_code, 200)
        recovered = original(self, self.claim, 'allocate', pass_data)
        self.assertEqual(recovered.status_code, 200, recovered.data)
        self.assertEqual(original(self, self.claim, 'allocate', pass_data).data, recovered.data)

        order_data = {'request_id': str(uuid4()), 'order_id': self.order.pk, 'amount': 300}
        with patch('users.receipt_usage_ledger.PassReceiptUsageEvent.objects.create',
                   side_effect=RuntimeError('隔离测试到账事件写入失败')):
            with self.assertRaisesMessage(RuntimeError, '隔离测试到账事件写入失败'):
                self.fund(order_data)
        with store_context(self.second), transaction.atomic():
            state = verify_allocations_locked(self.receipt_id)
            self.assertEqual((state.pass_units, state.order_units, state.version), (200, 0, 1))
            self.assertFalse(OrderPointsFundingAllocation.objects.filter(redemption__order_id=self.order.pk).exists())
        completed = self.fund(order_data)
        self.assertEqual(completed.status_code, 200, completed.data)
        self.assertEqual(self.fund(order_data).data, completed.data)
        with store_context(self.second), transaction.atomic():
            state = verify_allocations_locked(self.receipt_id)
            self.assertEqual((state.pass_units, state.order_units, state.released_units, state.version), (200, 300, 0, 2))
            self.assertEqual(PassReceiptUsageEvent.objects.filter(state_id=self.receipt_id).count(), 2)
            self.assertEqual(OrderPointsFundingAllocation.objects.filter(redemption__order_id=self.order.pk).count(), 1)
            # A correctly recorded trial allocation still cannot enter the
            # unconnected refund/settlement funding proof.
            from orders.points_funding_evidence import verified_order_allocations_locked
            from orders.points_funding_plan import PointsFundingPlanError
            with self.assertRaisesMessage(PointsFundingPlanError, '到账用途账本尚未接入订单核对'):
                verified_order_allocations_locked(self.order)
        overflow = original(self, self.claim, 'allocate',
            {'request_id': str(uuid4()), 'refund_id': first_refund, 'amount': 1})
        self.assertNotEqual(overflow.status_code, 200)
