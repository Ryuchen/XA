from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from orders import points_transfer_refund_release as releases
from orders.points_negotiation_preview import funding_after_refund


class PointsTransferRefundReleaseTests(SimpleTestCase):
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
