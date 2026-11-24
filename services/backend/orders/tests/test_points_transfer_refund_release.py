from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings
from rest_framework.exceptions import ValidationError

from orders import points_transfer_refund_release as releases
from orders.points_negotiation_preview import funding_after_refund


class PointsTransferRefundReleaseTests(SimpleTestCase):
    def test_refunded_points_report_detail_uses_read_only_scope(self):
        from contextlib import nullcontext
        from orders import segment_reports
        from orders.models import Order, OrderOriginalShareTransfer

        order = SimpleNamespace(pk=7, payment_status=Order.PaymentStatus.PARTIALLY_REFUNDED)
        locked = SimpleNamespace(pk=order.pk)
        with (patch.object(segment_reports, 'enabled', return_value=True),
              patch.object(segment_reports, 'supports', return_value=True) as supports,
              patch.object(segment_reports.transaction, 'atomic', return_value=nullcontext()),
              patch('orders.points_business_access.formal_service_read_candidate', return_value=True),
              patch.object(OrderOriginalShareTransfer.objects, 'filter') as transfers,
              patch('orders.points_transfer_refund_access.after_transfer',
                    return_value=nullcontext(locked)) as scoped):
            transfers.return_value.exists.return_value = True
            self.assertTrue(segment_reports.supports_detail(order))
            scoped.assert_called_once_with(order.pk, phase='report', read_only=True)
            supports.assert_called_once_with(locked)

            scoped.side_effect = ValidationError('退款凭证待核对')
            supports.reset_mock()
            self.assertFalse(segment_reports.supports_detail(order))
            supports.assert_not_called()

    def test_report_support_reuses_verified_refund_range(self):
        from orders import segment_reports
        from orders.models import Order

        order = Mock(status=Order.Status.COMPLETED, payment_status=Order.PaymentStatus.PARTIALLY_REFUNDED)
        with (patch.object(segment_reports, 'enabled', return_value=True),
              patch('orders.ordinary_transfer_capability.enabled', return_value=False),
              patch('orders.points_transfer_refund_access.permits', return_value=True),
              patch.object(segment_reports.transaction, 'atomic') as atomic):
            for refunds, supported in ((0, False), (1, True), (2, True), (3, False)):
                with self.subTest(refunds=refunds):
                    order.refund_records.count.return_value = refunds
                    self.assertEqual(segment_reports.supports(order), supported)
            atomic.assert_not_called()

    def test_continuation_can_require_release_without_existing_allocations(self):
        from orders import points_transfer_refund_continuation as continuation
        from orders.models import Order

        order = SimpleNamespace(status=Order.Status.IN_SERVICE, payment_status=Order.PaymentStatus.PARTIALLY_REFUNDED)
        posting = SimpleNamespace(participants={})
        transfers = Mock()
        transfers.filter.return_value.exists.return_value = True
        postings = Mock()
        postings.select_for_update.return_value.filter.return_value.order_by.return_value = [posting]
        with (patch('orders.points_transfer_refund_access.permits', side_effect=lambda _order, *, phase: phase == 'refund'),
              patch('orders.points_transfer_refund_access.require_refund_or_isolated'),
              patch.object(continuation.transaction, 'get_connection', return_value=SimpleNamespace(in_atomic_block=True)),
              patch.object(continuation.OrderOriginalShareTransfer, 'objects', transfers),
              patch.object(continuation.PointsNegotiationPosting, 'objects', postings),
              patch('orders.financial_lock.lock_order_money'),
              patch('orders.points_negotiation_termination.verify_termination_locked') as verify):
            continuation.verify_locked(order, include_release=True)
            posting.participants = []
            with self.assertRaisesMessage(ValidationError, '转单退款补资释放凭证类型异常'):
                continuation.verify_locked(order, include_release=True)

        verify.assert_called_once_with(posting, include_release=True)

    @override_settings(SERVICE_POINTS_TRANSFER_REFUND_RECEIPT_USAGE_TESTING=True)
    def test_records_both_receipt_events_before_rechecking_original_allocations(self):
        order = SimpleNamespace(pk=116)
        posting = Mock(pk=1, terms_hash='terms', points_refund_id=1)
        posting.proposal.case.order = order
        allocations = [SimpleNamespace(pk=1, amount=210, receipt_id=119),
                       SimpleNamespace(pk=2, amount=15, receipt_id=120)]
        rows = [SimpleNamespace(allocation=SimpleNamespace(receipt_id=item.receipt_id,
                                receipt=SimpleNamespace(usage_version=1))) for item in allocations]
        posting.funding_releases.select_related.return_value.order_by.return_value = rows
        actual = funding_after_refund(principal_units=280, gift_units=110, compensation_units=90,
            returned_prepaid_units=120, returned_gift_units=0, returned_compensation_units=0,
            provider_income_units=505, pending_deduction_units=150, provider_external_units=0,
            inviter_rate=0, allocated_units=225)
        self.assertEqual(actual.release_units, 30)
        allocation_model, release_model, evidence_model = Mock(), Mock(), Mock()
        allocation_model.objects.select_for_update.return_value.filter.return_value.order_by.return_value = allocations
        release_model.objects.filter.return_value.exists.return_value = False
        evidence_model.objects.filter.return_value.first.return_value = None
        events = []

        def verify_completed_release(evidence, **kwargs):
            self.assertIs(evidence, evidence_model.objects.create.return_value)
            self.assertEqual(events, [119, 120])
            return actual

        with (patch.multiple(releases, _guard=Mock(), OrderPointsFundingAllocation=allocation_model,
                            OrderPointsFundingRelease=release_model, OrderPointsFundingReleaseEvidence=evidence_model,
                            verify_transfer_revisions_after_refund_locked=Mock(return_value={}),
                            _compute=Mock(return_value=(SimpleNamespace(provider_income_units=505), actual)),
                            _add_addon_history=Mock(return_value=(actual, None)),
                            _usage_scope_locked=Mock()),
              patch.object(releases, 'verify_release_locked', side_effect=verify_completed_release),
              patch('orders.points_negotiation_execution.verify_posting_locked', return_value=posting),
              patch('orders.points_funding_release._prior_addon_releases', return_value=[]),
              patch('users.receipt_usage_sources.record_transfer_refund_release_locked',
                    side_effect=lambda row: events.append(row.allocation.receipt_id)),
              patch('users.receipt_usage_sources.verify_allocations_locked')):
            result = releases.record_release_locked(posting, {'conditional_funding_if_recovered': asdict(actual)})

        self.assertEqual(result, actual)
