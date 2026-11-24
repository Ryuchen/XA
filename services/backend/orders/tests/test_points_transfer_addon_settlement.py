from contextlib import nullcontext
from datetime import timedelta
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import Mock, patch
from uuid import uuid4

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import ConsumptionConfirmation
from orders.models import (GameCategory, Order, OrderAddonConsumption, OrderParticipationSegment, OrderProvider,
                           OrderSegmentAddonSettlement, ServiceAddonConfiguration, ServiceAddonPriceOption)
from orders.tests import test_points_checkout_integration as fixture
from orders.tests.factories import make_console_user, make_provider, make_service
from orders.tests.test_release_flow import screenshot
from stores.context import store_context
from stores.db import transaction
from wallet.models import Transaction


_formal_mysql = settings.DATABASES.get('store_b', {}).get('ENGINE') == 'django.db.backends.mysql'
_requires_formal_aftersale = skipUnless(_formal_mysql, 'requires MySQL for the verified add-on aftersale transaction')


class PointsTransferAddonSettlementDispatchTests(SimpleTestCase):
    def test_first_transfer_refund_settlement_uses_verified_net_allocations(self):
        from orders.points_funding_evidence import VerifiedOrderFunding, VerifiedOrderFundingWithAllocations
        from orders.points_negotiation_preview import funding_after_refund
        from orders.segment_settlement import _points_transfer_terms_verified
        from orders.segment_settlement_plan import PersonAmount, SegmentSettlementPlan
        from orders.settlement import SettlementError

        order = SimpleNamespace(pk=12, refund_records=Mock())
        order.refund_records.count.return_value = 1
        funding = funding_after_refund(principal_units=100, gift_units=0, compensation_units=0,
            returned_prepaid_units=10, returned_gift_units=0, returned_compensation_units=0,
            provider_income_units=120, pending_deduction_units=0, provider_external_units=0,
            inviter_rate=0, allocated_units=30)
        plan = SegmentSettlementPlan(150, (), (PersonAmount(7, (8,), 120, None, ()),), 0, 0, 0)
        with (patch('orders.points_transfer_refunded_plan.build_refunded_plan', return_value=(plan, funding)),
              patch('orders.models.PointsNegotiationPosting.objects.get',
                    return_value=SimpleNamespace(pk=11, created_at=timezone.now(), funding_release_evidence=None)),
              patch('orders.models.PointsPostRefundTransferRevision.objects.filter') as versions,
              patch('orders.points_transfer_addon_events.later_runs', return_value=[]),
              patch('orders.points_funding_evidence.verify_order_funding') as verify,
              patch('orders.points_funding_evidence.verified_order_allocations_locked') as reload_allocations,
              patch('orders.points_transfer_refund_evidence.verify_transfer_revisions_after_refund_locked') as reload_transfers,
              patch('orders.addon_funding_release.addon_release_totals') as reload_addons):
            versions.return_value.order_by.return_value.first.return_value = None
            reload_allocations.side_effect = AssertionError('已核验的逐笔补资无需再读原分配')
            verify.return_value = VerifiedOrderFundingWithAllocations(30, 30, 0, True, ((5, 9, 0), (6, 10, 30)))
            self.assertEqual(_points_transfer_terms_verified(order)[1]['allocations'],
                             [{'id': 5, 'receipt_id': 9, 'amount': 0}, {'id': 6, 'receipt_id': 10, 'amount': 30}])
            verify.assert_called_once_with(order.pk, allow_finalized=True, include_net_allocations=True)

            verify.return_value = VerifiedOrderFundingWithAllocations(30, 29, 1, False, ((6, 10, 29),))
            self.assertEqual(_points_transfer_terms_verified(order), (plan, None))

            verify.return_value = VerifiedOrderFunding(30, 30, 0, True)
            with self.assertRaisesMessage(SettlementError, '转单退款后的逐笔补资凭证缺失'):
                _points_transfer_terms_verified(order)

            verify.return_value = VerifiedOrderFundingWithAllocations(30, 30, 0, True, ((6, 10, 29),))
            with self.assertRaisesMessage(SettlementError, '转单退款后的补资与收益不守恒'):
                _points_transfer_terms_verified(order)
            reload_allocations.assert_not_called()
            reload_transfers.assert_not_called()
            reload_addons.assert_not_called()

    def test_full_refund_allows_empty_addon_earnings_in_verified_transfer_manifest(self):
        from engagement.confirmation import _fully_refunded_points_transfer_addons

        order = SimpleNamespace(refund_records=Mock())
        order.refund_records.exists.return_value = False
        totals = dict.fromkeys(('paid_units', 'principal_units', 'gift_units', 'compensation_units',
                               'provider_units', 'inviter_units'), 0)
        funding = {'addons': [], 'addon_refund_ids': [5], 'addon_totals': totals}
        with patch('orders.points_transfer_refund_access.permits', return_value=True) as permits:
            self.assertTrue(_fully_refunded_points_transfer_addons(order, funding))
            permits.assert_called_once_with(order, phase='settlement')
            self.assertFalse(_fully_refunded_points_transfer_addons(order, {**funding, 'addon_refund_ids': []}))
            self.assertFalse(_fully_refunded_points_transfer_addons(order, {**funding, 'addons': None}))
            for value in (None, False, 1):
                with self.subTest(value=value):
                    changed = {**totals, 'paid_units': value}
                    self.assertFalse(_fully_refunded_points_transfer_addons(order, {**funding, 'addon_totals': changed}))
            order.refund_records.exists.return_value = True
            self.assertFalse(_fully_refunded_points_transfer_addons(order, funding))
            order.refund_records.exists.return_value = False
            permits.return_value = False
            self.assertFalse(_fully_refunded_points_transfer_addons(order, funding))

    def test_transfer_addon_refund_readback_uses_the_income_from_its_verified_snapshot(self):
        from orders.addon_refund_execution import _replay_transfer_addon_refund

        order = SimpleNamespace(pk=12, refund_records=Mock())
        order.refund_records.exists.return_value = False
        original = {'order_id': order.pk, 'payment_id': 7, 'proposal_id': 8, 'amount': 100}
        run = SimpleNamespace(points_addon_hash='digest')
        refund = SimpleNamespace(pk=5, operator_id=2, amount=100, funding_release_run=run, payment=object())
        audit = SimpleNamespace(operator_account_id=refund.operator_id, request_body=original)
        operator = object()
        with (patch('orders.addon_refund_execution._require_refund_operator') as require_operator,
              patch('orders.points_transfer_refund_access.after_transfer', return_value=nullcontext(order)) as read,
              patch('orders.addon_refund_execution.OrderAddonRefund.objects.select_related') as refunds,
              patch('orders.addon_refund_execution._has_transfer_addon_refund_receipt', return_value=True) as has_receipt,
              patch('console.models.AdminAuditLog.objects.filter', return_value=[audit]),
              patch('orders.addon_funding_release.verify_run') as verify_run,
              patch('orders.addon_refund_execution._first_handover_after_refund', return_value=None) as later_handover,
              patch('orders.addon_handover.verify_transferred_handover_funding') as verify_handover,
              patch('orders.points_voluntary_paid_evidence.verify_standard_refund_current_read') as verify_paid):
            refunds.return_value.filter.return_value.first.return_value = refund
            run.points_addon_snapshot = {'payment_id': 7, 'addon_income': 0}
            self.assertEqual(_replay_transfer_addon_refund(12, 7, 8, operator), (refund, 0))
            require_operator.assert_called_once_with(operator)
            read.assert_called_once_with(12, phase='report', pending_payment_id=7, read_only=True)
            verify_run.assert_called_once_with(run, _skip_usage=True)
            verify_paid.assert_called_once_with(run)
            verify_handover.assert_not_called()
            has_receipt.assert_called_once_with(order, 7, 8)

            has_receipt.return_value = False
            with self.assertRaisesMessage(ValidationError, '原加购退款状态已变化'):
                _replay_transfer_addon_refund(12, 7, 8, operator)
            verify_run.assert_called_once()
            has_receipt.return_value = True

            handover = object()
            later_handover.return_value = handover
            run.points_addon_snapshot = {'payment_id': 7, 'addon_income': 65}
            self.assertEqual(_replay_transfer_addon_refund(12, 7, 8, operator), (refund, 65))
            verify_handover.assert_called_once_with(order, refund.payment, handover)
            verify_handover.side_effect = ValidationError('加购接替资金凭证缺失，请先核对')
            with self.assertRaisesMessage(ValidationError, '加购接替资金凭证缺失'):
                _replay_transfer_addon_refund(12, 7, 8, operator)
            later_handover.return_value = None

            run.points_addon_snapshot = {'payment_id': 7, 'funding': {'addon_history': [
                {'payment_id': 7, 'provider_units': 18}]}}
            self.assertEqual(_replay_transfer_addon_refund(12, 7, 8, operator), (refund, 18))
            run.points_addon_snapshot = {'payment_id': 9, 'addon_income': 0}
            with self.assertRaisesMessage(ValidationError, '原加购退款收益凭证待核对'):
                _replay_transfer_addon_refund(12, 7, 8, operator)

    def test_standard_refund_evidence_preserves_the_original_paid_person(self):
        from orders.models import OrderAddonFundingReleaseRun
        from orders.points_voluntary_paid_evidence import recover_standard_refunds_locked

        state = SimpleNamespace(db='tenant')
        order = SimpleNamespace(pk=12, settled_at=None, refund_records=Mock(), _state=state)
        order.refund_records.exists.return_value = False
        manifest = SimpleNamespace(pk=3, order_id=order.pk, finalized_at=None, _state=state)
        prior = {'manifest_id': manifest.pk, 'people': [{'account_id': 7}]}
        proposal = SimpleNamespace(payment_id=4, case=SimpleNamespace(order_id=order.pk), participants=Mock())
        refund = SimpleNamespace(pk=5, payment_id=4, created_at=timezone.now(), proposal=proposal)
        run = SimpleNamespace(refund=refund, refund_id=refund.pk, created_at=refund.created_at + timedelta(seconds=1),
            points_addon_snapshot={'funding': {}}, points_addon_hash='digest', refunded_main_snapshot=None,
            points_followup_snapshot=None)
        with (patch.object(transaction, 'get_connection', return_value=SimpleNamespace(in_atomic_block=True, alias=state.db)),
              patch('orders.points_transfer_refund_access.permits', return_value=True) as permits,
              patch('orders.points_transfer_refund_evidence._prior_settlement', return_value=prior),
              patch('orders.points_transfer_refund_evidence._verify_prior_settlement') as original,
              patch.object(OrderAddonFundingReleaseRun.objects, 'filter') as runs,
              patch('orders.models.OrderSegmentPersonSettlement.objects.filter') as paid_people,
              patch('orders.addon_funding_release.verify_run') as check_run,
              patch('orders.points_voluntary_paid_evidence.verify_historical_paid') as history,
              patch('orders.points_voluntary_paid_evidence._save_refund_evidence') as save):
            runs.return_value.select_related.return_value.order_by.return_value = [run]
            paid_people.return_value.exists.return_value = False
            proposal.participants.values_list.return_value = [7]
            with self.assertRaisesMessage(ValidationError, '已付人员与原加购退款范围不一致'):
                recover_standard_refunds_locked(order, manifest, [refund.pk])
            check_run.assert_not_called()
            save.assert_not_called()

            proposal.participants.values_list.return_value = [8]
            history.side_effect = ValidationError('已付结算时间晚于退款')
            with self.assertRaisesMessage(ValidationError, '已付结算时间晚于退款'):
                recover_standard_refunds_locked(order, manifest, [refund.pk])
            save.assert_not_called()

            history.side_effect = None
            self.assertIs(recover_standard_refunds_locked(order, manifest, [refund.pk]), prior)
            permits.assert_called_with(order, phase='settlement')
            original.assert_called_with(order, prior)
            check_run.assert_called_with(run)
            history.assert_called_with(order, prior, refund.created_at, manifest.pk)
            save.assert_called_once_with(refund, run, manifest, prior)

    def test_committed_standard_refund_requires_paid_witness_once_in_manifest_revision(self):
        from orders.points_voluntary_paid_evidence import verify_standard_refund_current_read

        refund = SimpleNamespace(pk=5, order_id=12, created_at=timezone.now())
        run = SimpleNamespace(refund=refund, refund_id=refund.pk, points_addon_snapshot={'funding': {}},
            points_addon_hash='digest', refunded_main_snapshot=None, points_followup_snapshot=None)
        with (patch('orders.points_voluntary_paid_evidence.verify_current_read', return_value=None) as evidence,
              patch('orders.models.OrderSegmentPersonSettlement.objects.filter') as paid,
              patch('orders.models.OrderSegmentAddonSettlementRevision.objects.filter') as changes,
              patch('orders.models.OrderSegmentSettlementManifest.objects.filter') as manifest):
            paid.return_value.exists.return_value = True
            changes.return_value.values_list.return_value = [[6]]
            manifest.return_value.values_list.return_value.first.return_value = [6]
            self.assertIsNone(verify_standard_refund_current_read(run))

            manifest.return_value.values_list.return_value.first.return_value = [refund.pk]
            with self.assertRaisesMessage(ValidationError, '已付加购退款缺少清单版本'):
                verify_standard_refund_current_read(run)

            changes.return_value.values_list.return_value = [[refund.pk]]
            with self.assertRaisesMessage(ValidationError, '已付加购退款缺少人员结算凭证'):
                verify_standard_refund_current_read(run)

            evidence.return_value = {'people': [{'account_id': 7}]}
            self.assertIsNone(verify_standard_refund_current_read(run))
            paid.assert_called_with(manifest__order_id=refund.order_id, settled_at__lte=refund.created_at)

    def test_segment_addon_terms_use_verified_net_allocations(self):
        from orders.addon_funding import AddonFundingPart, CombinedAddonFunding
        from orders.points_funding_evidence import VerifiedOrderFundingWithAllocations
        from orders.points_funding_plan import plan_service_funding
        from orders.points_transfer_funding import PointsTransferFunding
        from orders.segment_settlement import _points_transfer_terms_verified
        from orders.segment_settlement_plan import PersonAmount, SegmentSettlementPlan
        from orders.settlement import ProviderShare, SettlementError

        order = SimpleNamespace(pk=12, refund_records=SimpleNamespace(exists=lambda: False))
        funding = plan_service_funding(deal_units=100, point_discount_units=10, inviter_rate=0,
            shares=[ProviderShare(100, 'PERCENT', 20, 0, 80)])
        segments = SegmentSettlementPlan(100, (), (PersonAmount(7, (8,), 80, None, ()),), 0, 10, 0)
        combined = CombinedAddonFunding(main=funding, addon_payment_ids=(9,),
            addon_parts=(AddonFundingPart(9, 7, 8, 40),), addon_paid_units=50, addon_principal_units=30,
            addon_gift_units=20, addon_compensation_units=0, addon_provider_income_units=40,
            addon_inviter_income_units=0, addon_provider_recovery_units=5,
            main_deductions=(), store_income_units=20, additional_store_funding_units=15)
        with (patch('orders.points_transfer_funding.plan_points_transfer',
                    return_value=PointsTransferFunding(segments, funding)),
              patch('orders.addon_settlement.combined_terms', return_value=combined),
              patch('orders.points_transfer_refund_access.permits', return_value=True),
              patch('orders.models.OrderAddonSettlement.objects.filter') as existing_settlements,
              patch('orders.models.OrderAddonRefund.objects.filter') as refunds,
              patch('orders.models.OrderAddonRefundEarningDeduction.objects.filter') as deductions,
              patch('orders.points_funding_evidence.verify_order_funding') as verify_funding,
              patch('orders.points_funding_evidence.verified_order_allocations_locked') as reload_allocations,
              patch('orders.points_transfer_revision.verify_revisions_locked') as reload_transfers,
              patch('orders.addon_funding_release.addon_release_totals') as reload_addons):
            existing_settlements.return_value.exists.return_value = False
            refunds.return_value.distinct.return_value.select_related.return_value = []
            refunds.return_value.order_by.return_value.values_list.return_value = []
            deductions.return_value.exclude.return_value.select_related.return_value.order_by.return_value.distinct.return_value = []

            verify_funding.return_value = VerifiedOrderFundingWithAllocations(15, 15, 0, True,
                ((11, 2, 0), (13, 4, 15)))
            plan, terms = _points_transfer_terms_verified(order)
            self.assertEqual(plan.store_income, 20)
            self.assertEqual(terms['allocations'], [{'id': 11, 'receipt_id': 2, 'amount': 0},
                                                    {'id': 13, 'receipt_id': 4, 'amount': 15}])
            verify_funding.assert_called_once_with(12, allow_finalized=True, include_net_allocations=True)

            verify_funding.return_value = VerifiedOrderFundingWithAllocations(15, 14, 1, False, ((13, 4, 14),))
            self.assertEqual(_points_transfer_terms_verified(order), (segments, None))
            verify_funding.return_value = VerifiedOrderFundingWithAllocations(15, 15, 0, True, ((13, 4, 14),))
            with self.assertRaisesMessage(SettlementError, '积分转单及加购补资与收益不守恒'):
                _points_transfer_terms_verified(order)
            reload_allocations.assert_not_called()
            reload_transfers.assert_not_called()
            reload_addons.assert_not_called()

    def test_history_reuse_is_limited_to_locked_order_receipt_and_access_scope(self):
        from orders import points_addon_receipt_usage as usage
        from orders.points_business_access import _scope

        state = SimpleNamespace(db='tenant')
        order = SimpleNamespace(pk=12, _state=state)
        payment = SimpleNamespace(pk=2, _state=state, intent=SimpleNamespace(order_id=order.pk))
        other = SimpleNamespace(pk=3, _state=state, intent=SimpleNamespace(order_id=13))
        connection = SimpleNamespace(in_atomic_block=True, alias='tenant', atomic_blocks=[object()])
        with (patch.object(transaction, 'get_connection', return_value=connection),
              patch.object(usage, '_payment_facts', side_effect=lambda *args, **kwargs: {'verified': [2]}) as rebuild):
            with usage.reuse_history_locked(order, {6, 7}):
                first = usage.payment_facts(payment, read_receipt_id=6)
                first['verified'].append(3)
                self.assertEqual(usage.payment_facts(payment, read_receipt_id=6), {'verified': [2]})
                self.assertEqual(rebuild.call_count, 1)
                usage.payment_facts(payment, read_receipt_id=6, _event_history=False)
                usage.payment_facts(payment, read_receipt_id=7)
                for _ in range(2):
                    usage.payment_facts(other, read_receipt_id=6)
                    usage.payment_facts(payment, read_receipt_id=8)
                    usage.payment_facts(payment)
                connection.atomic_blocks.append(object())
                usage.payment_facts(payment, read_receipt_id=6)
                connection.atomic_blocks.pop()
                token = _scope.set(('tenant', 'another-context', 12, 8, 3, 'report', 'addon'))
                try:
                    usage.payment_facts(payment, read_receipt_id=6)
                finally:
                    _scope.reset(token)
                self.assertEqual(rebuild.call_count, 11)

            rebuild.side_effect = ValidationError('原付款凭证异常')
            with self.assertRaises(ValidationError):
                usage.payment_facts(payment, read_receipt_id=6)

    def test_formal_transfer_scope_dispatches_paid_addon_to_segment_settlement(self):
        from orders.points_business_access import _bind
        from orders.services import _settle_order

        order = SimpleNamespace(pk=12, status='COMPLETED', Status=Order.Status,
            settlement_mode='REPORT_APPROVAL', SettlementMode=Order.SettlementMode,
            commission_allocation_pending=False, commission_rate=20, provider_income=80, shop_income=20,
            is_gift_snapshot=False, service_clock_version=1, service_aftersale_hours=1,
            service_freeze_hours=2, service_deposit_percent=0, funding_terms=None,
            aftersales=SimpleNamespace(filter=lambda **_: SimpleNamespace(exists=lambda: False)),
            refund_records=SimpleNamespace(exists=lambda: False))
        store = SimpleNamespace(slug='settlement-store', database_alias='default')
        with (patch('orders.points_business_access.configured', return_value=True),
              patch('orders.points_business_access.current_store', return_value=store),
              patch.object(transaction, 'get_connection', return_value=SimpleNamespace(alias='default')),
              patch('orders.addon_settlement.enabled', return_value=True),
              patch('orders.addon_settlement.has_paid_addons', return_value=True),
              patch('orders.points_service_payment.needs_integrated_processing', return_value=True),
              patch('orders.segment_addon_settlement.enabled', return_value=False),
              patch('orders.segment_settlement.settle_due_segment_people', return_value=[9]) as segment,
              patch('orders.points_settlement_test.settle_points_order', return_value=[20]) as ordinary):
            with _bind(phase='settlement', kind='transfer', order_id=order.pk):
                self.assertEqual(_settle_order(order), [9])
            segment.assert_called_once()
            with _bind(phase='settlement', kind='transfer', order_id=99):
                self.assertEqual(_settle_order(order), [20])
            ordinary.assert_called_once()

    @override_settings(SERVICE_POINTS_MYSQL_ENABLED=True)
    def test_decision_locks_order_before_choosing_original_acceptance_scope(self):
        from orders.addon_handover import _points_handover_scope
        from orders.models import OrderAddonHandover, OrderOriginalShareTransfer

        order = SimpleNamespace(pk=12, refund_records=SimpleNamespace(exists=lambda: False))
        account = SimpleNamespace(pk=7)
        request = SimpleNamespace(account=account)

        @_points_handover_scope
        def confirm(view, request, order_id, payment_id, handover_id):
            return getattr(request, '_points_transferred_handover_replay', False)

        with (patch.object(Order.objects, 'filter') as orders,
              patch.object(Order.objects, 'select_for_update') as lock,
              patch.object(OrderOriginalShareTransfer.objects, 'filter') as transfers,
              patch.object(OrderAddonHandover.objects, 'filter') as prior,
              patch('orders.points_service_payment.needs_integrated_processing', return_value=True),
              patch.object(transaction, 'get_connection', return_value=SimpleNamespace(vendor='mysql')),
              patch('orders.points_transfer_refund_access.after_transfer', return_value=nullcontext()) as read,
              patch('orders.points_transfer_refund_access.transfer_capture', return_value=nullcontext()) as capture):
            orders.return_value.first.return_value = order
            locked = lock.return_value.filter.return_value
            locked.first.return_value = order
            transfers.return_value.exists.return_value = True

            def find_receipt(**kwargs):
                self.assertEqual(locked.first.call_count, prior.call_count)
                return prior.return_value

            prior.side_effect = find_receipt
            prior.return_value.exists.return_value = True
            self.assertTrue(confirm(None, request, order.pk, 3, 6))
            lock.return_value.filter.assert_called_once_with(pk=order.pk)
            prior.assert_called_once_with(pk=6, payment_id=3, payment__intent__order=order,
                kind='STANDARD', status='ACCEPTED', decisions__account=account, decisions__accepted=True)
            read.assert_called_once_with(order.pk, phase='report', pending_payment_id=3, read_only=True)
            capture.assert_not_called()

            prior.return_value.exists.return_value = False
            self.assertFalse(confirm(None, SimpleNamespace(account=account), order.pk, 3, 8))
            capture.assert_called_once_with(order.pk, addon_handover=True, pending_payment_id=3)

    def test_funded_transfer_replay_requires_the_original_run_even_when_release_is_zero(self):
        from orders.addon_handover import verify_transferred_handover_funding
        from orders.models import OrderAddonFundingReleaseRun, OrderPointsFundingAllocation

        order, payment = Order(pk=12), SimpleNamespace(pk=3)
        proposal = SimpleNamespace(pk=6, accepted_at=timezone.now())
        with (patch.object(OrderPointsFundingAllocation.objects, 'filter') as allocations,
              patch.object(OrderAddonFundingReleaseRun.objects, 'filter') as runs,
              patch('orders.addon_funding_release.verify_run') as verify,
              patch('orders.addon_handover.verify_handover') as verify_unfunded):
            allocations.return_value.exists.return_value = True
            runs.return_value.first.return_value = None
            with self.assertRaisesMessage(ValidationError, '加购接替资金凭证缺失'):
                verify_transferred_handover_funding(order, payment, proposal)
            allocations.assert_called_once_with(redemption__order=order, created_at__lte=proposal.accepted_at)
            zero_release = SimpleNamespace(released_amount=0, points_handover_snapshot={'version': 1},
                                           points_handover_hash='digest')
            runs.return_value.first.return_value = zero_release
            verify_transferred_handover_funding(order, payment, proposal)
            verify.assert_called_once_with(zero_release)
            allocations.return_value.exists.return_value = False
            runs.return_value.first.return_value = None
            verify_transferred_handover_funding(order, payment, proposal)
            verify_unfunded.assert_called_once_with(payment, proposal)

    def test_second_handover_requires_the_original_refund_run_and_same_segment_deductions(self):
        from django.core.exceptions import ValidationError as DjangoValidationError
        from orders.models import OrderAddonFundingReleaseRun
        from orders.points_addon_handover_refund_facts import verify_before_second_handover

        refunded_at = timezone.now()
        before = refunded_at + timedelta(seconds=1)
        refund = SimpleNamespace(pk=11, payment_id=3, order_id=12, created_at=refunded_at,
            amount=50, wallet_deduction=0, deposit_deduction=0)
        first = SimpleNamespace(pk=6, payment_id=3, payment=SimpleNamespace(intent=SimpleNamespace(order_id=12)),
            previous_id=None, kind='STANDARD', accepted_at=refunded_at - timedelta(seconds=1), transferred_basis=60,
            target_segment_id=9, target_segment=SimpleNamespace(provider_account_id=20))
        participant = [1, 20, 60, 50, [{'segment_id': 9}], [48, 2, 0, 0], None, [],
                       [[15, 3, 9, 48]], [[16, 9, 2]]]
        run = SimpleNamespace(pk=14, released_amount=0, points_addon_hash='original-digest',
            points_addon_snapshot={'handover_refund': {'handover_id': 6, 'participants': [participant]}})
        with (patch.object(OrderAddonFundingReleaseRun.objects, 'filter') as runs,
              patch('orders.addon_funding_release.verify_run') as verify):
            runs.return_value.first.return_value = None
            with self.assertRaisesMessage(DjangoValidationError, '缺少原退款补资凭证'):
                verify_before_second_handover(refund, first, before=before, receipt_id=5)
            runs.assert_called_once_with(refund=refund, created_at__gt=refunded_at, created_at__lt=before)
            runs.return_value.first.return_value = run
            self.assertEqual(verify_before_second_handover(refund, first, before=before, receipt_id=5),
                             [11, 14, 'original-digest'])
            verify.assert_called_once_with(run, _skip_usage=True, witness_receipt_id=5)
            participant[9][0][1] = 10
            with self.assertRaisesMessage(DjangoValidationError, '未归属同一履约段'):
                verify_before_second_handover(refund, first, before=before, receipt_id=5)
            participant[8], participant[9] = [], []
            participant[4][0]['segment_id'] = 10
            with self.assertRaisesMessage(DjangoValidationError, '未归属同一履约段'):
                verify_before_second_handover(refund, first, before=before, receipt_id=5)

    def test_handover_rechecks_historical_wallet_exhaustion_and_deposit_cycle(self):
        from django.core.exceptions import ValidationError as DjangoValidationError
        from orders.points_addon_handover_refund_facts import _verify_prior_handover_refund_recovery

        stamp = timezone.now()
        wallet = SimpleNamespace(account_id=20, user_id=21)
        debit = SimpleNamespace(wallet=wallet, order_id=None, balance_after=0, created_at=stamp)
        deficit = SimpleNamespace(account_business_cycle=3, created_at=stamp + timedelta(seconds=2),
            source_store_transaction=SimpleNamespace(created_at=stamp + timedelta(seconds=1)))
        refund = SimpleNamespace(created_at=stamp, wallet_deduction=2, deposit_deduction=28, provider_transaction=debit,
            order=SimpleNamespace(providers=SimpleNamespace(filter=lambda **_: SimpleNamespace(
                values_list=lambda *args, **kwargs: SimpleNamespace(first=lambda: 21)))),
            deposit_deficits=SimpleNamespace(select_related=lambda *args: [deficit]))
        first = SimpleNamespace(accepted_at=stamp - timedelta(seconds=1),
            target_segment=SimpleNamespace(provider_account_id=20, provider_business_cycle=3))
        run = SimpleNamespace(created_at=stamp + timedelta(seconds=3))
        with patch('orders.ordinary_refund_receipt_evidence._verify_direct_deposit') as verify:
            _verify_prior_handover_refund_recovery(refund, first, run)
            verify.assert_called_once_with(deficit, refund=refund, account_id=20, user_id=21, amount=28, addon=True)
            deficit.account_business_cycle = 4
            with self.assertRaisesMessage(DjangoValidationError, '押金周期或时序异常'):
                _verify_prior_handover_refund_recovery(refund, first, run)
            deficit.account_business_cycle = 3
            debit.balance_after = 1
            with self.assertRaisesMessage(DjangoValidationError, '钱包尚有可用余额'):
                _verify_prior_handover_refund_recovery(refund, first, run)
            refund.wallet_deduction, refund.provider_transaction = 0, None
            with self.assertRaisesMessage(DjangoValidationError, '缺少原退款零余额钱包凭证'):
                _verify_prior_handover_refund_recovery(refund, first, run)
            refund.zero_wallet_evidence = SimpleNamespace(account_id=20, wallet=wallet, business_cycle=3,
                balance=0, is_active=True, created_at=stamp)
            _verify_prior_handover_refund_recovery(refund, first, run)
            refund.zero_wallet_evidence.created_at = deficit.created_at
            with self.assertRaisesMessage(DjangoValidationError, '缺少原退款零余额钱包凭证'):
                _verify_prior_handover_refund_recovery(refund, first, run)
            refund.wallet_deduction, refund.deposit_deduction, refund.provider_transaction = 2, 0, debit
            debit.balance_after = -1
            with self.assertRaisesMessage(DjangoValidationError, '原退款钱包流水异常'):
                _verify_prior_handover_refund_recovery(refund, first, run)

    def test_cross_payment_debit_must_follow_the_same_first_service_transfer(self):
        from orders.models import OrderAddonHandover, OrderAddonPayment, OrderOriginalShareTransfer
        from orders.points_addon_handover_refund_facts import _same_seat_cross_handovers

        at = timezone.now()
        order = Mock()
        order.refund_records.filter.return_value.exists.return_value = False
        order.prestart_handoffs.filter.return_value.exists.return_value = False
        refund = SimpleNamespace(payment_id=3, order=order, created_at=at + timedelta(seconds=3))
        first = SimpleNamespace(pk=10, payment_id=3, kind='STANDARD', previous_id=None,
            created_at=at + timedelta(seconds=1), accepted_at=at + timedelta(seconds=2),
            source_segment_id=8, target_segment_id=9)
        donor = SimpleNamespace(**{**vars(first), 'pk': 11, 'payment_id': 4})
        original = SimpleNamespace(previous_segment_id=8, next_segment_id=9, created_at=at)
        with (patch.object(OrderOriginalShareTransfer.objects, 'filter') as transfers,
              patch.object(OrderAddonPayment.objects, 'filter') as payments,
              patch.object(OrderAddonHandover.objects, 'filter') as handovers,
              patch('orders.points_transfer_refund_access.same_seat_addon_payments', return_value=True)):
            transfers.return_value.select_related.return_value.order_by.return_value = [original]
            payments.return_value.select_related.return_value = [SimpleNamespace(pk=3), SimpleNamespace(pk=4)]
            self.assertEqual(_same_seat_cross_handovers(refund, first, donor), (first, donor))
            donor.target_segment_id = 10
            self.assertFalse(_same_seat_cross_handovers(refund, first, donor))
            donor.target_segment_id = 9
            donor.accepted_at = refund.created_at
            self.assertFalse(_same_seat_cross_handovers(refund, first, donor))
            donor.accepted_at = first.accepted_at
            order.prestart_handoffs.filter.return_value.exists.return_value = True
            self.assertFalse(_same_seat_cross_handovers(refund, first, donor))
            order.prestart_handoffs.filter.return_value.exists.return_value = False
            transfers.return_value.select_related.return_value.order_by.return_value = [original, Mock()]
            self.assertFalse(_same_seat_cross_handovers(refund, first, donor))
            transfers.return_value.select_related.return_value.order_by.return_value = [original]
            payments.return_value.select_related.return_value.append(SimpleNamespace(pk=5))
            third = SimpleNamespace(**{**vars(first), 'pk': 12, 'payment_id': 5})
            refund.main_pending_deduction = refund.wallet_deduction = refund.deposit_deduction = 0
            handovers.return_value.select_related.return_value = [third, first, donor]
            self.assertEqual(_same_seat_cross_handovers(refund, first, donor), (first, donor, third))
            self.assertEqual(_same_seat_cross_handovers(refund, first, third), (first, donor, third))
            third.accepted_at = refund.created_at
            self.assertFalse(_same_seat_cross_handovers(refund, first, donor))
            third.accepted_at = first.accepted_at
            refund.wallet_deduction = 1
            self.assertEqual(_same_seat_cross_handovers(refund, first, donor), (first, donor, third))
            refund.wallet_deduction = 0
            payments.return_value.select_related.return_value.append(SimpleNamespace(pk=6))
            fourth = SimpleNamespace(**{**vars(first), 'pk': 13, 'payment_id': 6})
            rows = handovers.return_value.select_related.return_value
            self.assertFalse(_same_seat_cross_handovers(refund, first, donor))
            rows.insert(0, fourth)
            self.assertEqual(_same_seat_cross_handovers(refund, first, fourth), (first, donor, third, fourth))
            rows.append(SimpleNamespace(**{**vars(fourth), 'pk': 14}))
            self.assertFalse(_same_seat_cross_handovers(refund, first, fourth))

    def test_same_seat_cross_debit_uses_each_earlier_payment_before_the_next(self):
        from django.core.exceptions import ValidationError as DjangoValidationError
        from orders.points_addon_handover_refund_facts import _verify_same_seat_debits

        refund = SimpleNamespace(payment_id=3)
        person = SimpleNamespace(account_id=11, burden=50)
        handovers = [SimpleNamespace(payment_id=pk, target_segment_id=9) for pk in (3, 4, 5)]
        state = {(9, 11): {'income': 48}}
        valid = [SimpleNamespace(payment_id=pk, segment_id=9, amount=amount)
                 for pk, amount in ((3, 48), (4, 1), (5, 1))]
        skipped = [valid[0], SimpleNamespace(payment_id=5, segment_id=9, amount=2)]
        with patch('orders.points_addon_handover_refund_facts._same_seat_cross_income') as income:
            income.side_effect = [1, 48]
            _verify_same_seat_debits(refund, handovers, person, state, valid, receipt_id=7)
            income.side_effect = [1, 48]
            with self.assertRaisesMessage(DjangoValidationError, '未按原付款顺序扣减'):
                _verify_same_seat_debits(refund, handovers, person, state, skipped, receipt_id=7)
            income.side_effect = [0, 48]
            _verify_same_seat_debits(refund, handovers, person, state, skipped, receipt_id=7)
            valid[-1].segment_id = 10
            with self.assertRaisesMessage(DjangoValidationError, '未按原付款顺序扣减'):
                _verify_same_seat_debits(refund, handovers, person, state, valid, receipt_id=7)
            handovers.append(SimpleNamespace(payment_id=6, target_segment_id=9))
            state[(9, 11)]['income'] = 47
            valid = [SimpleNamespace(payment_id=pk, segment_id=9, amount=amount)
                     for pk, amount in ((3, 47), (4, 1), (5, 1), (6, 1))]
            income.side_effect = [1, 1, 48]
            _verify_same_seat_debits(refund, handovers, person, state, valid, receipt_id=7)
            skipped = [*valid[:2], SimpleNamespace(payment_id=6, segment_id=9, amount=2)]
            income.side_effect = [1, 1, 48]
            with self.assertRaisesMessage(DjangoValidationError, '未按原付款顺序扣减'):
                _verify_same_seat_debits(refund, handovers, person, state, skipped, receipt_id=7)

    def test_same_seat_cross_refund_exhausts_addon_and_main_earnings_before_cash(self):
        from django.core.exceptions import ValidationError as DjangoValidationError
        from orders.points_addon_handover_refund_facts import _verify_same_seat_debits, _verify_same_seat_main_debit

        refund = SimpleNamespace(payment_id=3)
        person = SimpleNamespace(account_id=11, burden=50, addon_pending_deduction=9, main_pending_deduction=1)
        handovers = [SimpleNamespace(payment_id=pk, target_segment_id=9) for pk in (3, 4, 5)]
        state = {(9, 11): {'income': 3}}
        deductions = [SimpleNamespace(payment_id=pk, segment_id=9, amount=3) for pk in (3, 4, 5)]
        with patch('orders.points_addon_handover_refund_facts._same_seat_cross_income', return_value=3):
            _verify_same_seat_debits(refund, handovers, person, state, deductions,
                                     receipt_id=7, following_deductions=41)
            with self.assertRaisesMessage(DjangoValidationError, '未按原付款顺序扣减'):
                _verify_same_seat_debits(refund, handovers, person, state, deductions[:-1],
                                         receipt_id=7, following_deductions=44)
        _verify_same_seat_main_debit(person, 1, 0)
        person.main_pending_deduction = 0
        with self.assertRaisesMessage(DjangoValidationError, '未按顺序使用原履约段主单待结收益'):
            _verify_same_seat_main_debit(person, 1, 0)
        _verify_same_seat_main_debit(person, 1, 1)

    def test_second_handover_checks_each_refund_before_the_next_event(self):
        from django.core.exceptions import ValidationError as DjangoValidationError
        from orders.points_addon_handover_refund_facts import verify_refunds_before_second_handover

        created = timezone.now()
        first = SimpleNamespace(transferred_basis=60)
        refunds = [SimpleNamespace(pk=11, amount=20, created_at=created),
                   SimpleNamespace(pk=12, amount=30, created_at=created + timedelta(seconds=1))]
        boundary = created + timedelta(seconds=2)
        facts = [[11, 21, 'first-digest'], [12, 22, 'second-digest']]
        with patch('orders.points_addon_handover_refund_facts.verify_before_second_handover',
                   side_effect=facts) as verify:
            self.assertEqual(verify_refunds_before_second_handover(refunds, first, before=boundary, receipt_id=5),
                             facts)
            self.assertEqual([call.kwargs['before'] for call in verify.call_args_list],
                             [refunds[1].created_at, boundary])
            self.assertEqual([call.args[0] for call in verify.call_args_list], refunds)
            refunds[1].amount = 40
            with self.assertRaisesMessage(DjangoValidationError, '未保留可接替份额'):
                verify_refunds_before_second_handover(refunds, first, before=boundary, receipt_id=5)

    def test_same_seat_return_uses_only_an_unaffected_teammate_payment(self):
        from orders.models import OrderOriginalShareTransfer
        from orders.points_transfer_refund_access import unaffected_addon_for_return

        at = timezone.now()
        boundary = at + timedelta(seconds=1)
        order = SimpleNamespace(pk=12, required_provider_count=2, room_required_snapshot=False, customer_account_id=18)
        original = SimpleNamespace(attribution_id=8, slot=1, provider_account_id=20)
        transfer = SimpleNamespace(previous_segment=original, next_segment=SimpleNamespace(provider_account_id=22), created_at=at)
        moved = SimpleNamespace(pk=3)
        segment = SimpleNamespace(attribution_id=8, slot=2, provider_account_id=21, provider_business_cycle=1,
            started_at=at - timedelta(seconds=2), released_at=None)
        teammate = SimpleNamespace(pk=4, intent=SimpleNamespace(order_id=12, segment=segment, provider_id=21, buyer_id=18),
            buyer_transaction=SimpleNamespace(created_at=at - timedelta(seconds=1)), handovers=Mock(), refund_records=Mock(),
            refund_earning_deductions=Mock())
        with patch.object(OrderOriginalShareTransfer.objects, 'filter') as transfers:
            rows = [transfers.return_value, teammate.handovers.all.return_value, teammate.refund_records.all.return_value,
                    teammate.refund_earning_deductions.all.return_value]
            for query in rows:
                query.exists.return_value = False
                query.filter.return_value.exists.return_value = False
            payments = [moved, teammate]
            self.assertTrue(unaffected_addon_for_return(order, moved, payments, transfer, boundary=boundary))
            rows[-1].filter.assert_called_once_with(refund__created_at__lte=boundary)
            rows[-1].filter.return_value.exists.return_value = True
            self.assertFalse(unaffected_addon_for_return(order, moved, payments, transfer, boundary=boundary))
            rows[-1].filter.return_value.exists.return_value = False
            rows[-1].exists.return_value = True
            self.assertFalse(unaffected_addon_for_return(order, moved, payments, transfer))
            self.assertTrue(unaffected_addon_for_return(order, moved, payments, transfer, boundary=boundary))
            segment.slot = original.slot
            self.assertFalse(unaffected_addon_for_return(order, moved, payments, transfer, boundary=boundary))

    def test_original_replays_can_continue_between_two_paid_addon_returns(self):
        from orders.models import OrderAddonHandover, OrderAddonPayment
        from orders import points_transfer_refund_access as scope

        order = Mock()
        order.addon_intents.exclude.return_value.values_list.return_value = []
        transfers = [Mock(), Mock()]
        chains = [[SimpleNamespace(payment_id=3), SimpleNamespace(payment_id=3)], [SimpleNamespace(payment_id=4)]]
        with (patch.object(OrderAddonPayment.objects, 'filter') as payments,
              patch.object(OrderAddonHandover.objects, 'filter') as handovers,
              patch.object(scope, '_followup_transfer_handover_scope', return_value=False),
              patch.object(scope, '_postrefund_transfer_addon_scope', return_value=False),
              patch.object(scope, 'post_refund_handover_history', return_value=False),
              patch.object(scope, '_origin_addon_history', return_value=False),
              patch.object(scope, '_multi_addon_return_chains', return_value=chains)):
            payments.return_value.select_related.return_value = [SimpleNamespace(intent_id=1), SimpleNamespace(intent_id=2)]
            handovers.return_value.exists.return_value = False
            handovers.return_value.values_list.return_value = []
            self.assertFalse(scope._supported_addon(order, transfers, allow_successive=True))
            self.assertTrue(scope._supported_addon(order, transfers, allow_successive=True, allocation_replay=True))
            self.assertTrue(scope._supported_addon(order, transfers, allow_handover=True, pending_payment_id=4))
            handovers.return_value.values_list.return_value = [4]
            self.assertFalse(scope._supported_addon(order, transfers, allow_handover=True, pending_payment_id=3))
            self.assertTrue(scope._supported_addon(order, transfers, allow_handover=True, pending_payment_id=3,
                                                  allow_successive=True))
            handovers.return_value.values_list.return_value = [3, 4]
            self.assertFalse(scope._supported_addon(order, transfers, allow_handover=True, pending_payment_id=3,
                                                   allow_successive=True))
            payments.return_value.select_related.return_value.append(SimpleNamespace(intent_id=3))
            chains[1].append(SimpleNamespace(payment_id=4))
            chains.append([SimpleNamespace(payment_id=5)])
            handovers.return_value.values_list.return_value = [5]
            self.assertFalse(scope._supported_addon(order, transfers, allow_successive=True))
            self.assertTrue(scope._supported_addon(order, transfers, allow_handover=True, pending_payment_id=5))
            handovers.return_value.values_list.return_value = []
            self.assertTrue(scope._supported_addon(order, transfers, allocation_replay=True))
            chains[-1].append(SimpleNamespace(payment_id=5))
            self.assertTrue(scope._supported_addon(order, transfers, allow_successive=True))

    def test_same_seat_payments_require_all_first_handover_chains(self):
        from orders import points_transfer_refund_access as scope

        at = timezone.now()
        order = SimpleNamespace(pk=12, required_provider_count=2, room_required_snapshot=False, customer_account_id=18)
        origin = SimpleNamespace(pk=8, provider_account_id=20, provider_business_cycle=1,
                                 started_at=at - timedelta(seconds=3))
        transfer = SimpleNamespace(previous_segment=origin, created_at=at)
        intent = SimpleNamespace(order_id=12, buyer_id=18, segment_id=8, provider_id=20)
        for count in (3, 4, 5):
            with self.subTest(count=count):
                payments = [SimpleNamespace(pk=pk, intent=intent,
                    buyer_transaction=SimpleNamespace(created_at=at - timedelta(seconds=1))) for pk in range(3, 3 + count)]
                chains = [[SimpleNamespace(payment_id=payment.pk)] for payment in payments]
                with patch.object(scope, '_successive_addon_handovers', side_effect=chains):
                    self.assertEqual(scope._multi_addon_return_chains(order, payments, [transfer]), chains)
                with patch.object(scope, '_successive_addon_handovers', side_effect=[*chains[:-1], []]):
                    self.assertEqual(scope._multi_addon_return_chains(order, payments, [transfer]), [])
                payments[-1].buyer_transaction.created_at = at
                with patch.object(scope, '_successive_addon_handovers', side_effect=chains):
                    self.assertEqual(scope._multi_addon_return_chains(order, payments, [transfer]), [])

    def test_pending_second_handover_scope_requires_the_recorded_allocation_action(self):
        from orders.models import OrderAddonHandover, OrderPointsFundingAllocation
        from orders.points_funding_write import pending_handover_for_allocation_replay

        order = SimpleNamespace(pk=11)
        key = 'points:original-action'
        with (patch.object(OrderPointsFundingAllocation.objects, 'filter') as allocations,
              patch.object(OrderAddonHandover.objects, 'filter') as handovers):
            self.assertIsNone(pending_handover_for_allocation_replay(order, 5, None))
            allocations.assert_not_called()
            allocations.return_value.exists.return_value = False
            self.assertIsNone(pending_handover_for_allocation_replay(order, 5, key))
            allocations.assert_called_once_with(request_key=key, redemption__order=order, receipt_id=5,
                                                platform_action__request_key=key)
            handovers.assert_not_called()
            allocations.return_value.exists.return_value = True
            handovers.return_value.values_list.return_value = [7]
            self.assertEqual(pending_handover_for_allocation_replay(order, 5, key), 7)
            handovers.return_value.values_list.return_value = [7, 8]
            self.assertIsNone(pending_handover_for_allocation_replay(order, 5, key))


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated store databases')
@override_settings(ALLOWED_HOSTS=['*'], PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
    SERVICE_POINTS_MYSQL_ENABLED=_formal_mysql, RECEIPT_USAGE_LEDGER_ENABLED=_formal_mysql,
    POINTS_REDEMPTION_PREVIEW_ENABLED=True, DISCOUNT_OPTIONS_PREVIEW_ENABLED=True,
    DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True,
    SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=True,
    SERVICE_POINTS_CHECKOUT_TESTING=not _formal_mysql, SERVICE_POINTS_ASSIGN_TESTING=not _formal_mysql,
    SERVICE_POINTS_REPORT_TESTING=not _formal_mysql, SERVICE_POINTS_FUNDING_TESTING=not _formal_mysql,
    SERVICE_POINTS_SETTLEMENT_TESTING=not _formal_mysql, SERVICE_POINTS_CONSUMPTION_TESTING=not _formal_mysql,
    SERVICE_POINTS_TRANSFER_CAPTURE_TESTING=not _formal_mysql,
    SERVICE_POINTS_TRANSFER_FUNDING_REVISION_TESTING=not _formal_mysql,
    SERVICE_POINTS_TRANSFER_SETTLEMENT_TESTING=not _formal_mysql,
    SERVICE_POINTS_TRANSFER_ADDON_SETTLEMENT_TESTING=not _formal_mysql,
    ADDON_PAYMENT_INTERNAL_TESTING=not _formal_mysql, ADDON_ORDER_FUNDING_INTERNAL_TESTING=not _formal_mysql,
    ADDON_SETTLEMENT_INTERNAL_TESTING=not _formal_mysql, ADDON_SEGMENT_SETTLEMENT_INTERNAL_TESTING=not _formal_mysql,
    ADDON_HANDOVER_INTERNAL_TESTING=not _formal_mysql, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=not _formal_mysql,
    ADDON_SEGMENT_REFUND_REVISION_INTERNAL_TESTING=not _formal_mysql,
    ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, SEGMENT_STANDARD_TRANSFER_ENABLED=True,
    SEGMENT_REPORT_ENABLED=True, SEGMENT_SETTLEMENT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True, LEGACY_FORCE_AUTH_COMPAT=False,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class PointsTransferAddonSettlementTest(TestCase):
    databases = {'default', 'store_b'} & settings.DATABASES.keys()
    if _formal_mysql and settings.ISOLATED_CONTROL_PLANE:
        databases.add('store_a')
    payload = fixture.PointsFundingPlatformRecoveryTests.payload
    quoted_payload = fixture.PointsFundingPlatformRecoveryTests.quoted_payload
    verify = fixture.PointsFundingPlatformRecoveryTests.verify
    fund = fixture.PointsFundingPlatformRecoveryTests.fund

    def setUp(self):
        if _formal_mysql and settings.ISOLATED_CONTROL_PLANE:
            from stores.models import Store
            Store.objects.get_or_create(slug='shenglang', defaults={
                'name': '声浪电竞', 'database_alias': 'store_a', 'status': Store.Status.ACTIVE,
                'initialized_at': timezone.now()})
        fixture.PointsFundingPlatformRecoveryTests.setUp(self)

    def create_assigned_points_order(self):
        if not _formal_mysql:
            return fixture.PointsFundingPlatformRecoveryTests.create_assigned_points_order(self)
        from console.points_redemption_config import save_points_terms
        from support.models import SupportContactCard
        from wallet.services import get_wallet

        provider = make_provider(deposit_mode='EARNINGS')
        account = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = account
        provider.escort_profile.save(update_fields=['account'])
        get_wallet(account=account, user=provider)
        support = make_console_user(['support:edit'])
        staff = get_or_create_account_for_legacy_user(support)
        support.admin_membership.account = staff
        support.admin_membership.save(update_fields=['account'])
        card = SupportContactCard.objects.create(name='隔离售后客服', staff_account=staff,
            wecom_corp_id='wwtest', wecom_service_url='https://work.weixin.qq.com/kfid/kf-test')
        save_points_terms(group_point_hundredths=10000, group_coin_units=2000,
                          max_ratio_basis_points=5000, version=1, audit=lambda *_: None)
        payload = self.payload() | {'provider_ids': [account.pk], 'support_contact_id': card.pk,
                                   'dispatch_route': 'PUBLIC', 'dispatch_mode': 'BOSS_SELECT'}
        quote = self.client.post('/api/orders/quote/', payload, format='json')
        self.assertEqual(quote.status_code, 200, quote.data)
        points = quote.data['data']['points_preview']
        self.assertTrue(points['payment_available'], points)
        self.assertFalse(points['checkout_test_only'], points)
        payload.pop('preview_points_hundredths')
        result = self.client.post('/api/orders/create/', payload | {
            'points_checkout_test_token': points['checkout_test_token'], 'expected_amount': 6000}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        order = Order.objects.get(pk=result.data['data']['id'])
        self.assertEqual((order.status, order.amount), (Order.Status.GRABBED, 4000))
        return order, None

    def transfer_funding_status(self, order):
        return self.client.get(f'/api/admin/platform/points-funding/{self.second.slug}/orders/{order.pk}',
                               HTTP_AUTHORIZATION='Bearer ' + self.token)

    def assert_transfer_funding(self, order, required, allocated):
        if _formal_mysql:
            response = self.transfer_funding_status(order)
            self.assertEqual(response.status_code, 200, response.data)
            proof = response.data['data']
            values = (proof['required_units'], proof['allocated_units'], proof['remaining_units'])
        else:
            from orders.points_funding_evidence import verify_order_funding
            with transaction.atomic():
                proof = verify_order_funding(order.pk)
            values = (proof.required, proof.allocated, proof.remaining)
        self.assertEqual(values, (required, allocated, required - allocated))

    def open_addon_aftersale(self, admin, order, payment_id, account, *, reason, request_id):
        url = '/api/admin/aftersales/open-negotiation/'
        payload = {'order_id': order.pk, 'addon_payment_id': payment_id,
                   'affected_provider_account_ids': [account.pk], 'reason': reason,
                   'client_request_id': request_id}
        response = admin.post(url, payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        case = order.aftersales.get(pk=response.data['data']['id'])
        self.assertEqual((case.addon_payment_id, case.affected_provider_account_ids), (payment_id, [account.pk]))
        return case.pk

    def test_preexisting_addon_and_funding_are_rebalanced_at_transfer_and_handover(self):
        self._preexisting_addon_and_funding()

    def test_corrupt_open_manifest_blocks_remaining_earnings_without_repaying_first_person(self):
        self._preexisting_addon_and_funding(check_manifest_integrity=True)

    def test_second_transfer_after_addon_handover_preserves_funding_and_settlement(self):
        self._preexisting_addon_and_funding(second_transfer=True)

    @_requires_formal_aftersale
    def test_addon_refund_then_second_transfer_recalculates_funding(self):
        self._preexisting_addon_and_funding(second_transfer=True, refund_before_transfer=True)

    @_requires_formal_aftersale
    def test_main_burden_refund_then_second_transfer_preserves_person_deduction(self):
        self._preexisting_addon_and_funding(second_transfer=True, refund_before_transfer=True, main_burden=True)

    @_requires_formal_aftersale
    def test_addon_refund_before_handover_then_settle_remaining_work(self):
        self._preexisting_addon_and_funding(refund_before_handover=True)

    def test_addon_license_unknown_is_reviewable_without_blocking_settlement(self):
        self._preexisting_addon_and_funding(license_outage=True)

    @_requires_formal_aftersale
    def test_addon_license_review_uses_previously_refunded_principal(self):
        self._preexisting_addon_and_funding(license_outage=True, refund_before_handover=True)

    @skipUnless(not _formal_mysql, 'checks the SQLite aftersale boundary')
    def test_addon_aftersale_is_rejected_without_the_mysql_transaction(self):
        self._preexisting_addon_and_funding(refund_before_handover=True, check_aftersale_gate=True)

    def test_addon_license_review_only_unknown_vip(self):
        self._preexisting_addon_and_funding(license_outage=True, unknown_kind='vip')

    def test_addon_license_review_only_unknown_points(self):
        self._preexisting_addon_and_funding(license_outage=True, unknown_kind='points')

    def test_addon_license_review_denies_benefits(self):
        self._preexisting_addon_and_funding(license_outage=True, review_deny=True)

    def test_addon_license_review_retries_deferred_vip(self):
        self._preexisting_addon_and_funding(license_outage=True, defer_vip=True)

    def test_addon_license_review_recovers_platform_audit(self):
        self._preexisting_addon_and_funding(license_outage=True, audit_failure=True)

    def test_addon_license_review_reverses_later_principal_refund(self):
        self._preexisting_addon_and_funding(license_outage=True, verify_reverse=True)

    def test_addon_license_review_does_not_restore_closed_cycle(self):
        self._preexisting_addon_and_funding(license_outage=True, closed_cycle=True)

    def test_points_main_earnings_cover_addon_refund_using_deal_snapshot(self):
        from engagement.models import PointFundingLot
        from engagement.confirmation import process_order
        from orders.models import OrderAddonRefund, OrderPointsSettlement, OrderSegmentFinancialSnapshot
        from wallet.models import ProviderReport
        from wallet.services import get_wallet
        with TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media), store_context(self.second):
            order = Order.objects.get(pk=self.order.pk)
            assignment = OrderProvider.objects.get(order=order, released_at__isnull=True)
            provider = assignment.provider_account
            player = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(provider)[0])
            boss = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
            self.assertEqual(player.post(f'/api/orders/orders/{order.pk}/start/',
                {'entry_image': screenshot()}, format='multipart').data['code'], 0)
            snapshot = OrderSegmentFinancialSnapshot.objects.get(segment__attribution__order=order)
            self.assertEqual((assignment.provider_income, snapshot.income_at_entry), (3200, 4800))
            game = GameCategory.objects.create(name='积分主单承担加购退款')
            self.service.game_category = game
            self.service.save(update_fields=['game_category'])
            addon = make_service(name='加购项目', game_category=game, is_addon=True)
            relation = ServiceAddonConfiguration.objects.create(service=self.service, addon=addon,
                enabled=True, conditions='', condition_mode='NONE', version=1)
            ServiceAddonPriceOption.objects.create(configuration=relation, unit='HOUR', price=20000)
            wallet = get_wallet(account=self.account)
            wallet.balance += 16000
            wallet.save(update_fields=['balance'])
            PointFundingLot.objects.create(account=self.account, source_key='points-addon-main-test',
                                           kind='PREPAID', remaining=16000)
            url = f'/api/orders/orders/{order.pk}/addons/intents/'
            intent = boss.post(url, {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1,
                'configuration_version': 1, 'provider_account_id': provider.pk, 'request_id': 'main-burden-addon',
                'expected_amount': 20000, 'gift_balance_amount': 0, 'compensation_balance_amount': 0}, format='json')
            self.assertEqual(intent.status_code, 200, intent.data)
            paid = boss.post(url + f"{intent.data['data']['id']}/pay/", {}, format='json')
            self.assertEqual(paid.status_code, 200, paid.data)
            payment_id = paid.data['data']['payment_id']
            self.assertEqual(player.post(f'/api/orders/orders/{order.pk}/complete/',
                {'completion_image': screenshot()}, format='multipart').data['code'], 0)
            case = boss.post(f'/api/orders/orders/{order.pk}/aftersale/', {'reason': '加购全部未履行',
                'affected_provider_account_ids': [provider.pk], 'client_request_id': 'main-burden-case'}, format='json')
            self.assertEqual(case.status_code, 200, case.data)
            case_id = case.data['data']['id']
            staff_user = make_console_user(['order:view', 'order:refund', 'order:settle', 'report:view', 'report:audit'])
            staff = get_or_create_account_for_legacy_user(staff_user)
            staff_user.admin_membership.account = staff
            staff_user.admin_membership.save(update_fields=['account'])
            admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
            refund_url = f'/api/admin/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/'
            proposal = admin.post(refund_url, {'request_id': 'main-burden-refund', 'amount_raw': 20000,
                'case_id': case_id, 'explanation': '双方同意加购全退'}, format='json')
            self.assertEqual(proposal.status_code, 200, proposal.data)
            proposal_id = proposal.data['data']['id']
            user_url = f'/api/orders/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/{proposal_id}/'
            for client in (boss, player):
                self.assertEqual(client.post(user_url, {'accepted': True}, format='json').status_code, 200)
            response = admin.post(refund_url + f'{proposal_id}/execute/', {}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            refund = OrderAddonRefund.objects.get(proposal_id=proposal_id)
            self.assertEqual((refund.addon_pending_deduction, refund.main_pending_deduction,
                              refund.wallet_deduction, refund.deposit_deduction), (16000, 4000, 0, 0))
            self.assertEqual(admin.post(f'/api/admin/aftersales/{case_id}/resolve/',
                {'resolution': '加购退款完成'}, format='json').status_code, 200)
            report = ProviderReport.objects.get(order=order)
            self.assertEqual(player.post(f'/api/wallet/reports/{report.pk}/images/',
                {'kind': 'RESULT', 'image': screenshot()}, format='multipart').data['code'], 0)
            self.assertEqual(player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json').data['code'], 0)
            self.assertEqual(admin.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json').data['code'], 0)
            report.refresh_from_db()
            with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
                self.assertTrue(process_order(order.pk))
                self.assertFalse(process_order(order.pk))
            settlement = OrderPointsSettlement.objects.get(order=order)
            self.assertEqual((settlement.paid_units, settlement.provider_units, settlement.funding_units,
                              settlement.store_units), (4000, 800, 0, 3200))
            self.assertEqual(settlement.persons.get().amount, 800)
            income = Transaction.objects.get(order=order, wallet__account=provider,
                                             tx_type='INCOME', remark=f'订单收入：{order.order_no}')
            self.assertEqual(income.amount, 800)
            self.assertEqual(get_wallet(account=provider).balance, 320)
            self.assertEqual(player.get('/api/wallet/reports/').data['data'][0]['settlement_status'], 'SETTLED')
            self.assertEqual(ConsumptionConfirmation.objects.get(order=order).amount, 4000)

    def _preexisting_addon_and_funding(self, *, second_transfer=False, refund_before_transfer=False,
                                       refund_before_handover=False, license_outage=False, main_burden=False,
                                       unknown_kind='both', review_deny=False, defer_vip=False, audit_failure=False,
                                       verify_reverse=False, closed_cycle=False, check_manifest_integrity=False,
                                       check_aftersale_gate=False):
        with TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            with store_context(self.second):
                order = Order.objects.get(pk=self.order.pk)
                old = OrderProvider.objects.get(order=order, released_at__isnull=True).provider_account
                old_client = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(old)[0])
                self.assertEqual(old_client.post(f'/api/orders/orders/{order.pk}/start/',
                    {'entry_image': screenshot()}, format='multipart').data['code'], 0)
                game = GameCategory.objects.create(name='积分转单前加购')
                self.service.game_category = game
                self.service.save(update_fields=['game_category'])
                addon = make_service(name='积分转单前加购', is_addon=True, game_category=game)
                config = ServiceAddonConfiguration.objects.create(service=self.service, addon=addon,
                    enabled=True, condition_mode='NONE', conditions='', version=1)
                ServiceAddonPriceOption.objects.create(configuration=config, unit='HOUR', price=1000)
                boss = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
                path = f'/api/orders/orders/{order.pk}/addons/intents/'
                intent = boss.post(path, {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1,
                    'configuration_version': 1, 'provider_account_id': old.pk, 'request_id': 'before-transfer-addon',
                    'expected_amount': 1000, 'gift_balance_amount': 0, 'compensation_balance_amount': 0}, format='json')
                self.assertEqual(intent.status_code, 200, intent.data)
                paid = boss.post(path + f"{intent.data['data']['id']}/pay/", {}, format='json')
                self.assertEqual(paid.status_code, 200, paid.data)
            self.assertEqual(self.verify().status_code, 200)
            allocation = self.fund({'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 300})
            self.assertEqual(allocation.status_code, 200, allocation.data)
            with store_context(self.second):
                replacement = make_provider(deposit_mode='EARNINGS')
                new = get_or_create_account_for_legacy_user(replacement)
                replacement.escort_profile.account = new
                replacement.escort_profile.save(update_fields=['account'])
                if _formal_mysql:
                    from wallet.services import get_wallet
                    get_wallet(account=new, user=replacement)
                permissions = ['order:view', 'order:dispatch', 'order:refund', 'order:settle', 'support:edit', 'report:audit']
                if closed_cycle:
                    permissions += ['wallet:view', 'wallet:adjust', 'user:view', 'user:retire']
                staff_user = make_console_user(permissions)
                staff = get_or_create_account_for_legacy_user(staff_user)
                staff_user.admin_membership.account = staff
                staff_user.admin_membership.save(update_fields=['account'])
                admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
                self.service.commission_override = True
                self.service.commission_rate = 30
                self.service.save(update_fields=['commission_override', 'commission_rate'])
                result = admin.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason': '双方同意', 'transfers': [{
                    'old_provider_id': old.pk, 'new_provider_id': new.pk, 'split_type': 'PERCENT', 'split_value': 0,
                    'penalty_amount': 0, 'original_share': {'retained': 4000, 'transferred': 6000,
                                                          'agreement': '双方同意'}}]}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
                old_segment = OrderParticipationSegment.objects.get(attribution__order=order, provider_account=old)
                self.assertEqual(old_client.post(f'/api/orders/orders/{order.pk}/participation-evidence/',
                    {'segment_id': old_segment.pk, 'completion_image': screenshot()}, format='multipart').data['code'], 0)
                from orders.models import OrderPointsFundingAllocation, OrderPointsTransferFundingRevision
                revision = OrderPointsTransferFundingRevision.objects.get(order=order)
                self.assertEqual((revision.before_funding['additional_store_funding_units'],
                                  revision.after_funding['additional_store_funding_units']), (600, 240))
                self.assertEqual(list(revision.releases.values_list('amount', flat=True)), [60])
                segment = OrderParticipationSegment.objects.get(attribution__order=order, provider_account=new)
                player = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(new)[0])
                self.assertEqual(player.post(f'/api/orders/orders/{order.pk}/participation-start/',
                    {'segment_id': segment.pk, 'entry_image': screenshot()}, format='multipart').data['code'], 0)
                payment_id = paid.data['data']['payment_id']
                if refund_before_handover:
                    open_url = '/api/admin/aftersales/open-negotiation/'
                    case_input = {'order_id': order.pk,
                        'addon_payment_id': payment_id,
                        'affected_provider_account_ids': [old.pk], 'reason': '原陪玩加购部分退款',
                        'client_request_id': 'addon-refund-before-handover'}
                    if check_aftersale_gate:
                        case = admin.post(open_url, case_input, format='json')
                        self.assertEqual(case.status_code, 400, case.data)
                        self.assertEqual(case.data['msg'], '当前加购售后范围不可用')
                        self.assertFalse(order.aftersales.exists())
                        self.assertFalse(order.transactions.filter(tx_type='REFUND').exists())
                        return
                    denied = admin.post(open_url, case_input | {'addon_payment_id': payment_id + 1}, format='json')
                    self.assertEqual(denied.status_code, 400, denied.data)
                    self.assertFalse(order.aftersales.exists())
                    case = admin.post(open_url, case_input, format='json')
                    self.assertEqual(case.status_code, 200, case.data)
                    case_id = case.data['data']['id']
                    self.assertEqual(order.aftersales.get(pk=case_id).addon_payment_id, payment_id)
                    refund_url = f'/api/admin/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/'
                    proposed = admin.post(refund_url, {'request_id': 'refund-before-addon-handover',
                        'amount_raw': 100, 'case_id': case_id, 'explanation': '甲负责部分未完成',
                        'affected_provider_account_ids': [old.pk]}, format='json')
                    self.assertEqual(proposed.status_code, 200, proposed.data)
                    proposal_id = proposed.data['data']['id']
                    user_url = f'/api/orders/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/{proposal_id}/'
                    for client in (boss, old_client):
                        decision = client.post(user_url, {'accepted': True}, format='json')
                        self.assertEqual(decision.status_code, 200, decision.data)
                    refund = admin.post(refund_url + f'{proposal_id}/execute/', {}, format='json')
                    self.assertEqual(refund.status_code, 200, refund.data)
                    resolved = admin.post(f'/api/admin/aftersales/{case_id}/resolve/',
                        {'resolution': '加购退款完成'}, format='json')
                    self.assertEqual(resolved.status_code, 200, resolved.data)
                    funding = self.transfer_funding_status(order)
                    self.assertEqual(funding.status_code, 409, funding.data)
                    self.assertEqual(funding.data['errorCode'], 'POINTS_FUNDING_REVIEW')
                staff_url = f'/api/admin/orders/{order.pk}/addons/payments/{payment_id}/handovers/'
                handover = admin.post(staff_url, {'request_id': 'pre-funded-addon-handover',
                    'target_provider_account_id': new.pk, 'retained_basis_raw': 400,
                    'completed_work': '甲已完成部分', 'remaining_work': '乙接替剩余部分'}, format='json')
                self.assertEqual(handover.status_code, 200, handover.data)
                handover_id = handover.data['data']['id']
                decision_url = f'/api/orders/orders/{order.pk}/addons/payments/{payment_id}/handovers/{handover_id}/decision/'
                for client in (boss, old_client, player):
                    decision = client.post(decision_url, {'accepted': True}, format='json')
                    self.assertEqual(decision.status_code, 200, decision.data)
                from orders.models import OrderAddonFundingReleaseRun
                release = OrderAddonFundingReleaseRun.objects.get(handover_id=handover_id)
                required_initial = 201 if refund_before_handover else 180
                self.assertEqual((release.allocated_before, release.required_amount, release.released_amount),
                                 (240, required_initial, 240 - required_initial))
                self.assertEqual(OrderPointsFundingAllocation.objects.get(redemption__order=order).amount, 300)
                self.assert_transfer_funding(order, required_initial, required_initial)
                self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
                if refund_before_transfer:
                    case_id = self.open_addon_aftersale(admin, order, payment_id, new,
                        reason='加购部分退款', request_id='addon-refund-before-return')
                    refund_url = f'/api/admin/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/'
                    proposed = admin.post(refund_url, {'request_id': 'addon-refund-before-return',
                        'amount_raw': 500 if main_burden else 100, 'case_id': case_id, 'explanation': '乙负责部分未完成',
                        'affected_provider_account_ids': [new.pk]}, format='json')
                    self.assertEqual(proposed.status_code, 200, proposed.data)
                    proposal_id = proposed.data['data']['id']
                    user_url = f'/api/orders/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/{proposal_id}/'
                    for client in (boss, player):
                        decision = client.post(user_url, {'accepted': True}, format='json')
                        self.assertEqual(decision.status_code, 200, decision.data)
                    refund = admin.post(refund_url + f'{proposal_id}/execute/', {}, format='json')
                    self.assertEqual(refund.status_code, 200, refund.data)
                    if main_burden:
                        from orders.models import OrderAddonRefund
                        record = OrderAddonRefund.objects.get(proposal_id=proposal_id)
                        self.assertEqual((record.addon_pending_deduction, record.main_pending_deduction), (420, 80))
                    resolved = admin.post(f'/api/admin/aftersales/{case_id}/resolve/',
                        {'resolution': '加购退款完成'}, format='json')
                    self.assertEqual(resolved.status_code, 200, resolved.data)
                participants = [(old_client, old_segment), (player, segment)]
                expected_payouts = ({old_segment.pk: 311, segment.pk: 350} if refund_before_handover else
                                    {old_segment.pk: 320, segment.pk: 420})
                completing_client = player
                if second_transfer:
                    result = admin.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason': '再次协商转单',
                        'transfers': [{'old_provider_id': new.pk, 'new_provider_id': old.pk,
                            'split_type': 'PERCENT', 'split_value': 0, 'penalty_amount': 0,
                            'original_share': {'retained': 2000, 'transferred': 4000, 'agreement': '双方同意'}}]}, format='json')
                    self.assertEqual(result.data['code'], 0, result.data)
                    revision = OrderPointsTransferFundingRevision.objects.filter(order=order).order_by('-pk').first()
                    self.assertEqual((revision.before_funding['additional_store_funding_units'],
                                      revision.after_funding['additional_store_funding_units']), (180, 180))
                    self.assertEqual(revision.allocations[0][-1], 120)
                    returned = OrderParticipationSegment.objects.get(attribution__order=order,
                        provider_account=old, released_at__isnull=True)
                    self.assertEqual(player.post(f'/api/orders/orders/{order.pk}/participation-evidence/',
                        {'segment_id': segment.pk, 'completion_image': screenshot()}, format='multipart').data['code'], 0)
                    self.assertEqual(old_client.post(f'/api/orders/orders/{order.pk}/participation-start/',
                        {'segment_id': returned.pk, 'entry_image': screenshot()}, format='multipart').data['code'], 0)
                    handover = admin.post(staff_url, {'request_id': 'pre-funded-addon-return',
                        'target_provider_account_id': old.pk, 'retained_basis_raw': 0 if main_burden else 200,
                        'completed_work': '乙已完成部分', 'remaining_work': '甲接替剩余部分'}, format='json')
                    self.assertEqual(handover.status_code, 200, handover.data)
                    handover_id = handover.data['data']['id']
                    decision_url = f'/api/orders/orders/{order.pk}/addons/payments/{payment_id}/handovers/{handover_id}/decision/'
                    for client in (boss, player, old_client):
                        decision = client.post(decision_url, {'accepted': True}, format='json')
                        self.assertEqual(decision.status_code, 200, decision.data)
                    release = OrderAddonFundingReleaseRun.objects.get(handover_id=handover_id)
                    required = 250 if main_burden else 198 if refund_before_transfer else 180
                    self.assertEqual((release.allocated_before, release.required_amount, release.released_amount), (180, required, 0))
                    self.assert_transfer_funding(order, required, 180)
                    participants.append((old_client, returned))
                    expected_payouts = ({old_segment.pk: 320, returned.pk: 70} if main_burden else
                                        {old_segment.pk: 320, segment.pk: 128, returned.pk: 210}
                                        if refund_before_transfer else
                                        {old_segment.pk: 320, segment.pk: 140, returned.pk: 280})
                    completing_client = old_client
                completion_url = f'/api/orders/orders/{order.pk}/complete/'
                if refund_before_handover:
                    rejected = old_client.post(completion_url,
                        {'completion_image': screenshot()}, format='multipart')
                    self.assertEqual(rejected.data['code'], 400, rejected.data)
                    order.refresh_from_db()
                    self.assertEqual(order.status, Order.Status.IN_SERVICE)
                completion_request = {'client_request_id': 'points-addon-transfer-completion'}
                completed = completing_client.post(completion_url,
                    {**completion_request, 'completion_image': screenshot()}, format='multipart')
                self.assertEqual(completed.data['code'], 0, completed.data)
                if refund_before_handover:
                    replay = completing_client.post(completion_url,
                        {**completion_request, 'completion_image': screenshot()}, format='multipart')
                    self.assertEqual(replay.data['code'], 0, replay.data)
                    self.assertTrue(replay.data['data']['request_replayed'])
                    self.assertEqual(order.status_logs.filter(action='COMPLETE').count(), 1)
                from engagement.confirmation import process_order
                for index, (client, participant) in enumerate(participants):
                    for action, extra in [('open', {}), ('upload', {'image': screenshot()}), ('submit', {'version': 0})]:
                        response = client.post(f'/api/orders/orders/{order.pk}/segment-report/',
                            {'segment_id': participant.pk, 'action': action, 'client_request_id': str(uuid4()), **extra},
                            format='multipart' if action == 'upload' else 'json')
                        self.assertEqual(response.data['code'], 0, response.data)
                    response = admin.post(f'/api/admin/orders/{order.pk}/segments/{participant.pk}/report-review/',
                        {'action': 'approve', 'version': 1}, format='json')
                    self.assertEqual(response.data['code'], 0, response.data)
                    if check_manifest_integrity and index == 0:
                        from orders.models import OrderSegmentSettlementManifest
                        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=100)):
                            self.assertFalse(process_order(order.pk))
                        manifest = OrderSegmentSettlementManifest.objects.get(order=order)
                        self.assertIsNone(manifest.finalized_at)
                        income = Transaction.objects.filter(order=order, tx_type='INCOME').order_by('pk')
                        posted = list(income.values_list('pk', 'wallet__account_id', 'amount'))
                        self.assertEqual([row[1:] for row in posted], [(old.pk, 1920), (old.pk, 320)])
                future = timezone.now() + timedelta(hours=100)
                if check_manifest_integrity:
                    from orders.settlement import SettlementError
                    with transaction.atomic(), patch('django.utils.timezone.now', return_value=future):
                        OrderSegmentSettlementManifest.objects.filter(pk=manifest.pk).update(plan_hash='0' * 64)
                        with self.assertRaisesMessage(SettlementError, '退款前逐段结算方案待核对'):
                            process_order(order.pk)
                        self.assertEqual(list(income.values_list('pk', 'wallet__account_id', 'amount')), posted)
                        transaction.set_rollback(True)
                if refund_before_transfer:
                    with patch('django.utils.timezone.now', return_value=future):
                        self.assertFalse(process_order(order.pk))
                    self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
                    extra = self.fund({'request_id': str(uuid4()), 'order_id': order.pk,
                                       'amount': 70 if main_burden else 18})
                    self.assertEqual(extra.status_code, 200, extra.data)
                if license_outage:
                    from django.db import DatabaseError
                    def selective(feature=None):
                        unknown = (unknown_kind == 'vip' and feature == 'VIP_GROWTH') or (
                            unknown_kind == 'points' and feature is None)
                        return ('UNKNOWN', {'status': 'CONTROL_UNAVAILABLE'}) if unknown else ('ALLOW', {'status': 'ACTIVE'})
                    outage = (patch('stores.license_status.status_for_store', side_effect=DatabaseError('control offline'))
                              if unknown_kind == 'both' else
                              patch('stores.license_benefits.check_new_benefits', side_effect=selective))
                    with override_settings(LICENSE_BENEFIT_ENFORCEMENT_ENABLED=True, LICENSE_SNAPSHOT_CACHE_ENABLED=False), \
                            outage, patch('django.utils.timezone.now', return_value=future):
                        self.assertEqual(process_order(order.pk), unknown_kind == 'points')
                        self.assertFalse(process_order(order.pk))
                else:
                    with patch('django.utils.timezone.now', return_value=future):
                        self.assertTrue(process_order(order.pk))
                        self.assertFalse(process_order(order.pk))
                if check_manifest_integrity:
                    self.assertEqual(list(income.filter(wallet__account_id=old.pk).values_list(
                        'pk', 'wallet__account_id', 'amount')), posted)
                payouts = dict(OrderSegmentAddonSettlement.objects.filter(payment_id=payment_id)
                               .values_list('segment_id', 'amount'))
                self.assertEqual(payouts, expected_payouts)
                order.refresh_from_db()
                self.assertEqual((order.provider_income, order.settlement_store_funding,
                                  order.settlement_addon_contribution),
                                 (4360, 250, 110) if main_burden else (4440, 198, 242) if refund_before_transfer else
                                 (4440, 201, 239) if refund_before_handover else (4440, 180, 260))
                if license_outage and unknown_kind != 'points':
                    from engagement.models import LicenseConsumptionPointsDecision, LicenseRefundVipDecision, PointLedger
                    self.assertIsNotNone(order.settled_at)
                    self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                    vip = LicenseRefundVipDecision.objects.get(order=order)
                    self.assertEqual((vip.outcome, vip.paid_units, vip.refunded_units), ('UNKNOWN', 4000, 0))
                    self.assertEqual(order.funding_benefits_resolved_at, vip.decided_at)
                    points = LicenseConsumptionPointsDecision.objects.get(order=order)
                    self.assertEqual(points.outcome, 'UNKNOWN')
                    self.assertEqual(points.license_evidence['vip_decision_id'], vip.pk)
                    self.assertFalse(PointLedger.objects.filter(event_key=points.event_key).exists())
                else:
                    self.assertEqual(ConsumptionConfirmation.objects.get(order=order).amount, 4000)
                consumption = OrderAddonConsumption.objects.get(payment_id=payment_id)
                self.assertEqual((consumption.paid_units, consumption.refunded_units),
                                 (1000, 500 if main_burden else 100 if refund_before_transfer or refund_before_handover else 0))
                if license_outage:
                    self.assertEqual(consumption.awarded_points, 0)
                    self.assertEqual(consumption.license_decisions['points']['outcome'],
                                     'ALLOW' if unknown_kind == 'vip' else 'UNKNOWN')
                    self.assertTrue(consumption.license_decisions['points']['applicable'])
            with store_context(self.first):
                self.assertFalse(OrderSegmentAddonSettlement.objects.exists())
            if license_outage:
                from stores.models import PlatformAccount, PlatformRoleGrant
                from stores.platform_auth import issue_tokens
                supervisor = PlatformAccount.objects.create(username='addon-license-supervisor', phone='13800001999',
                                                            is_phone_verified=True)
                PlatformRoleGrant.objects.create(account=supervisor, role='SUPER_ADMIN', scope_slug='*')
                supervisor_token, _ = issue_tokens(supervisor, 'SMS', supervisor.phone)
                url = f'/api/admin/platform/store-management/{self.second.pk}/license-points-review'
                response = self.client.get(url, {'kind': 'ADDON'}, HTTP_AUTHORIZATION='Bearer ' + supervisor_token)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data['data']['total'], 1)
                row = response.data['data']['list'][0]
                self.assertEqual((row['order_id'], row['payment_id']), (order.pk, payment_id))
                self.assertEqual(row['decisions']['points']['outcome'], 'ALLOW' if unknown_kind == 'vip' else 'UNKNOWN')
                self.assertEqual(row['decisions']['points']['reason'], 'ACTIVE' if unknown_kind == 'vip' else 'CONTROL_UNAVAILABLE')
                self.assertEqual(row['decisions']['points']['points_at_decision_hundredths'],
                                 consumption.license_decisions['points']['points_at_decision_hundredths'])
                other = self.client.get(f'/api/admin/platform/store-management/{self.first.pk}/license-points-review',
                    {'kind': 'ADDON'}, HTTP_AUTHORIZATION='Bearer ' + supervisor_token)
                self.assertEqual(other.data['data']['total'], 0)
                denied = self.client.get(url, {'kind': 'ADDON'}, HTTP_AUTHORIZATION='Bearer ' + self.token)
                self.assertEqual(denied.status_code, 403)
                from engagement.models import LicenseAddonBenefitsReview, MemberProfile
                from engagement.services import completed_consumption
                with store_context(self.second):
                    account = consumption.account
                    if closed_cycle:
                        from engagement.models import RetirementAudit
                        from wallet.services import get_wallet
                        wallet = get_wallet(account_id=account.pk)
                        balance = wallet.balance
                        self.assertGreater(balance, 0)
                        adjusted = admin.post(f'/api/admin/wallets/{wallet.pk}/adjust/', {
                            'amount': -balance, 'remark': '归还隔离测试预存余款',
                            'client_request_id': str(uuid4()),
                        }, format='json')
                        self.assertEqual(adjusted.data['code'], 0, adjusted.data)
                        adjustment = Transaction.objects.get(wallet=wallet, tx_type='ADJUST', operator_account=staff)
                        self.assertEqual((adjustment.amount, adjustment.balance_before, adjustment.balance_after),
                                         (-balance, balance, 0))
                        path = f'/api/admin/users/{account.pk}'
                        preview = admin.get(f'{path}/retire-preview/')
                        self.assertEqual(preview.status_code, 200, preview.data)
                        self.assertTrue(preview.data['data']['can_retire'], preview.data)
                        retired = admin.post(f'{path}/retire/', {'confirm': True, 'reason': '隔离测试退店',
                            'confirmation_token': preview.data['data']['confirmation_token']}, format='json')
                        self.assertEqual(retired.data['code'], 0, retired.data)
                        account.refresh_from_db()
                        self.assertEqual(account.business_cycle, consumption.business_cycle + 1)
                        self.assertTrue(RetirementAudit.objects.filter(account=account, operator=staff,
                            business_cycle=consumption.business_cycle).exists())
                    before_points = MemberProfile.objects.get(account=account).points_balance
                    before_vip = completed_consumption(account)
                resolve_url = f'{url}/addons/{consumption.pk}/resolve'
                chosen = 'DENY' if review_deny else 'ALLOW'
                body = {'request_id': str(uuid4()), 'vip_outcome': None if unknown_kind == 'points' else chosen,
                        'points_outcome': None if unknown_kind == 'vip' else chosen,
                        'reason': '已核对加购实销时授权和 VIP 功能有效'}
                if audit_failure:
                    from django.db import DatabaseError
                    from stores.models import PlatformAuditEvent
                    original = PlatformAuditEvent.objects.create
                    def fail_audit(**kwargs):
                        if kwargs.get('action') == 'license.addon_benefits_reviewed':
                            raise DatabaseError('模拟平台审计失败')
                        return original(**kwargs)
                    with patch('stores.models.PlatformAuditEvent.objects.create', side_effect=fail_audit):
                        uncertain = self.client.post(resolve_url, body, format='json',
                            HTTP_AUTHORIZATION='Bearer ' + supervisor_token)
                    self.assertEqual(uncertain.status_code, 503, uncertain.data)
                    pending = self.client.get(url, {'kind': 'ADDON_REVIEWED'},
                        HTTP_AUTHORIZATION='Bearer ' + supervisor_token).data['data']['list'][0]
                    self.assertEqual((pending['status'], pending['request_id']), ('AUDIT_PENDING', body['request_id']))
                    replacement = PlatformAccount.objects.create(username='addon-license-recovery', phone='13800001998',
                                                                 is_phone_verified=True)
                    PlatformRoleGrant.objects.create(account=replacement, role='SUPER_ADMIN', scope_slug='*')
                    replacement_token, _ = issue_tokens(replacement, 'SMS', replacement.phone)
                    recovered = self.client.post(resolve_url, body, format='json',
                        HTTP_AUTHORIZATION='Bearer ' + replacement_token)
                    self.assertEqual(recovered.status_code, 200, recovered.data)
                    audit = PlatformAuditEvent.objects.get(action='license.addon_benefits_reviewed')
                    self.assertEqual(audit.account_id, replacement.pk)
                    self.assertEqual(audit.detail['original_actor_id'], str(supervisor.pk))
                if defer_vip:
                    from django.db.models import QuerySet
                    from wallet.services import get_wallet

                    with patch('stores.license_benefits.check_new_benefits', return_value=('DENY', {'status': 'EXPIRED'})):
                        response = self.client.post(resolve_url, body, format='json', HTTP_AUTHORIZATION='Bearer ' + supervisor_token)
                        self.assertTrue(response.data['data']['vip_recalculation_pending'])
                        select_for_update = QuerySet.select_for_update

                        def check_lock_order(rows, *args, **kwargs):
                            if rows.model is LicenseAddonBenefitsReview:
                                lock_wallet.assert_called_once_with(account_id=account.pk, for_update=True, create=False)
                            return select_for_update(rows, *args, **kwargs)

                        with (patch('wallet.services.get_wallet', wraps=get_wallet) as lock_wallet,
                              patch.object(QuerySet, 'select_for_update', new=check_lock_order)):
                            replay = self.client.post(resolve_url, body, format='json', HTTP_AUTHORIZATION='Bearer ' + supervisor_token)
                        self.assertEqual(replay.status_code, 200, replay.data)
                        self.assertEqual(replay.data, response.data)
                        with patch('wallet.services.get_wallet', wraps=get_wallet) as lock_wallet:
                            changed = self.client.post(resolve_url, {**body, 'reason': body['reason'].replace('VIP', 'vip')},
                                format='json', HTTP_AUTHORIZATION='Bearer ' + supervisor_token)
                        self.assertEqual(changed.status_code, 409, changed.data)
                        lock_wallet.assert_not_called()
                    with store_context(self.first), override_settings(LICENSE_BENEFIT_ENFORCEMENT_ENABLED=False):
                        from engagement.tasks import retry_addon_vip_recalculations
                        self.assertEqual(retry_addon_vip_recalculations.run(), 0)
                    with store_context(self.second), override_settings(LICENSE_BENEFIT_ENFORCEMENT_ENABLED=False):
                        from engagement.tasks import retry_addon_vip_recalculations
                        self.assertEqual(retry_addon_vip_recalculations.run(), 1)
                    with (override_settings(LICENSE_BENEFIT_ENFORCEMENT_ENABLED=False),
                          patch('wallet.services.get_wallet', wraps=get_wallet) as lock_wallet):
                        response = self.client.post(resolve_url, body, format='json', HTTP_AUTHORIZATION='Bearer ' + supervisor_token)
                    lock_wallet.assert_not_called()
                    self.assertFalse(response.data['data']['vip_recalculation_pending'])
                else:
                    response = self.client.post(resolve_url, body, format='json', HTTP_AUTHORIZATION='Bearer ' + supervisor_token)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data['data']['awarded_points_hundredths'],
                                 0 if review_deny or closed_cycle else consumption.license_decisions['points']['points_at_decision_hundredths'])
                if closed_cycle:
                    self.assertEqual(response.data['data']['skip_reason'], 'CYCLE_CLOSED')
                self.assertEqual(self.client.post(resolve_url, body, format='json',
                    HTTP_AUTHORIZATION='Bearer ' + supervisor_token).data, response.data)
                self.assertEqual(self.client.get(url, {'kind': 'ADDON'},
                    HTTP_AUTHORIZATION='Bearer ' + supervisor_token).data['data']['total'], 0)
                with store_context(self.second):
                    consumption.refresh_from_db()
                    review = LicenseAddonBenefitsReview.objects.get(consumption=consumption)
                    self.assertEqual(review.vip_applied, unknown_kind != 'points' and not review_deny and not closed_cycle)
                    self.assertEqual(consumption.license_decisions['vip']['outcome'],
                                     'ALLOW' if unknown_kind == 'points' else 'UNKNOWN')
                    self.assertEqual(MemberProfile.objects.get(account=account).points_balance,
                                     before_points + review.awarded_points)
                    self.assertEqual(completed_consumption(account), before_vip + (
                        0 if unknown_kind == 'points' or review_deny or closed_cycle else consumption.paid_units - consumption.refunded_units))
                    if verify_reverse:
                        from orders.models import AfterSale, OrderAddonRefund, OrderAddonRefundProposal
                        from orders.addon_consumption import reverse_addon_consumption
                        from wallet.services import get_wallet
                        from engagement.models import PointLedger
                        with transaction.atomic():
                            payment = consumption.payment
                            allocation = payment.allocations.select_for_update().filter(kind='PREPAID').first()
                            amount = min(100, allocation.amount - allocation.refunded_amount)
                            self.assertGreater(amount, 0)
                            case, _ = AfterSale.objects.get_or_create(order=order,
                                defaults={'account': account, 'reason': '隔离退款积分测试'})
                            proposal = OrderAddonRefundProposal.objects.create(payment=payment, case=case,
                                case_opened_at=case.created_at, created_by=account, request_id='review-refund',
                                amount=amount, source_plan=[], explanation='隔离退款积分测试', status='ACCEPTED')
                            allocation.refunded_amount += amount
                            allocation.save(update_fields=['refunded_amount'])
                            lot = allocation.lot
                            lot.remaining += amount
                            lot.save(update_fields=['remaining'])
                            wallet = get_wallet(account=account, for_update=True)
                            old_balance = wallet.balance
                            wallet.balance += amount
                            wallet.save(update_fields=['balance'])
                            buyer_tx = Transaction.objects.create(wallet=wallet, order=order, tx_type='REFUND',
                                amount=amount, balance_before=old_balance, balance_after=wallet.balance)
                            refund = OrderAddonRefund.objects.create(order=order, payment=payment, proposal=proposal,
                                operator=account, amount=amount, addon_pending_deduction=amount, buyer_transaction=buyer_tx)
                            reverse_addon_consumption(refund)
                            reverse_addon_consumption(refund)
                            terms = payment.benefit_terms
                            remaining = (consumption.principal_units - allocation.refunded_amount) * terms['points_rate'] * terms['birthday_multiplier'] // 100
                            expected = review.awarded_points - remaining
                            self.assertEqual(PointLedger.objects.get(kind='ADDON_LICENSE_REFUND').amount, -expected)
                            self.assertEqual(MemberProfile.objects.get(account=account).points_balance,
                                             before_points + review.awarded_points - expected)
                            transaction.set_rollback(True)
                with store_context(self.first):
                    self.assertFalse(LicenseAddonBenefitsReview.objects.exists())

    @_requires_formal_aftersale
    def test_transfer_addon_shared_funding_and_independent_settlement(self):
        self._transfer_addon_refund_and_settlement(full_refund=False)

    @_requires_formal_aftersale
    def test_transfer_addon_refund_deducts_unsettled_main_without_repaying_teammate(self):
        self._transfer_addon_refund_and_settlement(full_refund=True)

    def _transfer_addon_refund_and_settlement(self, *, full_refund):
        from engagement.confirmation import process_order
        from wallet.services import get_wallet
        with TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            with store_context(self.second):
                order = Order.objects.get(pk=self.order.pk)
                boss = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
                old = OrderProvider.objects.get(order=order, released_at__isnull=True).provider_account
                old_client = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(old)[0])
                self.assertEqual(old_client.post(f'/api/orders/orders/{order.pk}/start/',
                    {'entry_image': screenshot()}, format='multipart').data['code'], 0)
                replacement = make_provider(deposit_mode='EARNINGS')
                new = get_or_create_account_for_legacy_user(replacement)
                replacement.escort_profile.account = new
                replacement.escort_profile.save(update_fields=['account'])
                get_wallet(account=new, user=replacement)
                player = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(new)[0])
                staff_user = make_console_user(['order:view', 'order:dispatch', 'order:refund', 'order:settle', 'report:audit', 'support:edit'])
                staff = get_or_create_account_for_legacy_user(staff_user)
                staff_user.admin_membership.account = staff
                staff_user.admin_membership.save(update_fields=['account'])
                admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
                self.service.commission_override = True
                self.service.commission_rate = 30
                self.service.save(update_fields=['commission_override', 'commission_rate'])
                moved = admin.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason': '双方同意', 'transfers': [{
                    'old_provider_id': old.pk, 'new_provider_id': new.pk, 'split_type': 'PERCENT', 'split_value': 0,
                    'penalty_amount': 0, 'original_share': {'retained': 4000, 'transferred': 6000, 'agreement': '双方同意'}}]}, format='json')
                self.assertEqual(moved.data['code'], 0, moved.data)
                old_segment = OrderParticipationSegment.objects.get(attribution__order=order, provider_account=old)
                new_segment = OrderParticipationSegment.objects.get(attribution__order=order, provider_account=new)
                self.assertEqual(old_client.post(f'/api/orders/orders/{order.pk}/participation-evidence/',
                    {'segment_id': old_segment.pk, 'completion_image': screenshot()}, format='multipart').data['code'], 0)
                self.assertEqual(player.post(f'/api/orders/orders/{order.pk}/participation-start/',
                    {'segment_id': new_segment.pk, 'entry_image': screenshot()}, format='multipart').data['code'], 0)
                game = GameCategory.objects.create(name='积分转单加购')
                self.service.game_category = game
                self.service.save(update_fields=['game_category'])
                addon = make_service(name='积分转单加购', is_addon=True, game_category=game)
                config = ServiceAddonConfiguration.objects.create(service=self.service, addon=addon,
                    enabled=True, condition_mode='NONE', conditions='', version=1)
                ServiceAddonPriceOption.objects.create(configuration=config, unit='HOUR', price=1000)
                path = f'/api/orders/orders/{order.pk}/addons/intents/'
                result = boss.post(path, {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1,
                    'configuration_version': 1, 'provider_account_id': new.pk, 'request_id': 'points-transfer-addon',
                    'expected_amount': 1000, 'gift_balance_amount': 0, 'compensation_balance_amount': 0}, format='json')
                self.assertEqual(result.status_code, 200, result.data)
                paid = boss.post(path + f"{result.data['data']['id']}/pay/", {}, format='json')
                self.assertEqual(paid.status_code, 200, paid.data)
                payment_id = paid.data['data']['payment_id']
                self.assertEqual(player.post(f'/api/orders/orders/{order.pk}/complete/',
                    {'completion_image': screenshot()}, format='multipart').data['code'], 0)
                case_id = self.open_addon_aftersale(admin, order, payment_id, new,
                    reason='加购部分未履行', request_id='points-addon-aftersale')
                def report(client, segment):
                    for action, extra in [('open', {}), ('upload', {'image': screenshot()}), ('submit', {'version': 0})]:
                        result = client.post(f'/api/orders/orders/{order.pk}/segment-report/',
                            {'segment_id': segment.pk, 'action': action, 'client_request_id': str(uuid4()), **extra},
                            format='multipart' if action == 'upload' else 'json')
                        self.assertEqual(result.data['code'], 0, result.data)
                    result = admin.post(f'/api/admin/orders/{order.pk}/segments/{segment.pk}/report-review/',
                        {'action': 'approve', 'version': 1}, format='json')
                    self.assertEqual(result.data['code'], 0, result.data)
                report(old_client, old_segment)
                details = admin.get(f'/api/admin/orders/{order.pk}/').data['data']
                self.assertIsNone(details['settlement_store_funding'])
                self.assertIsNone(details['settlement_addon_contribution'])
                future = timezone.now() + timedelta(hours=100)
                self.assert_transfer_funding(order, 140, 0)
                with patch('django.utils.timezone.now', return_value=future):
                    self.assertFalse(process_order(order.pk))
                self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
            self.assertEqual(self.verify().status_code, 200)
            funded = self.fund({'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 140})
            self.assertEqual(funded.status_code, 200, funded.data)
            with store_context(self.second):
                with patch('django.utils.timezone.now', return_value=future):
                    self.assertFalse(process_order(order.pk))
                old_income = Transaction.objects.filter(order=order, tx_type='INCOME', wallet__account=old)
                first_income = old_income.values().get()
                self.assertEqual(first_income['amount'], 1920)
                staff_url = f'/api/admin/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/'
                # Keep the aftersale after the first settlement in simulated time.
                with patch('django.utils.timezone.now', return_value=future + timedelta(minutes=1)):
                    proposal = admin.post(staff_url, {'request_id': 'points-addon-refund',
                        'amount_raw': 1000 if full_refund else 500, 'explanation': '双方同意加购退款',
                        'case_id': case_id}, format='json')
                self.assertEqual(proposal.status_code, 200, proposal.data)
                proposal_id = proposal.data['data']['id']
                user_url = f'/api/orders/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/{proposal_id}/'
                with patch('django.utils.timezone.now', return_value=future + timedelta(minutes=2)):
                    for client in (boss, player):
                        decision = client.post(user_url, {'accepted': True}, format='json')
                        self.assertEqual(decision.status_code, 200, decision.data)
                with patch('django.utils.timezone.now', return_value=future + timedelta(minutes=3)):
                    refund = admin.post(staff_url + f'{proposal_id}/execute/', {}, format='json')
                self.assertEqual(refund.status_code, 200, refund.data)
                if full_refund:
                    from orders.models import OrderAddonRefund
                    receipt = OrderAddonRefund.objects.get(proposal_id=proposal_id)
                    self.assertEqual((receipt.addon_pending_deduction, receipt.main_pending_deduction), (700, 300))
                with patch('django.utils.timezone.now', return_value=future + timedelta(minutes=4)):
                    resolved = admin.post(f'/api/admin/aftersales/{case_id}/resolve/',
                        {'resolution': '加购退款完成'}, format='json')
                    self.assertEqual(resolved.status_code, 200, resolved.data)
                    report(player, new_segment)
                with patch('django.utils.timezone.now', return_value=future + timedelta(hours=73)):
                    self.assertTrue(process_order(order.pk))
                    self.assertFalse(process_order(order.pk))
                if full_refund:
                    self.assertFalse(OrderSegmentAddonSettlement.objects.filter(payment_id=payment_id).exists())
                else:
                    self.assertEqual(OrderSegmentAddonSettlement.objects.get(payment_id=payment_id).amount, 200)
                self.assertEqual(list(old_income.values()), [first_income])
                from orders.models import OrderSegmentAddonSettlementRevision
                self.assertEqual(OrderSegmentAddonSettlementRevision.objects.filter(manifest__order=order).count(), 1)
                order.refresh_from_db()
                self.assertEqual((order.provider_income, order.settlement_store_funding,
                                  order.settlement_addon_contribution),
                                 (4140, 140, 0) if full_refund else (4440, 140, 300))
                details = admin.get(f'/api/admin/orders/{order.pk}/').data['data']
                self.assertEqual((details['settlement_store_funding'], details['settlement_addon_contribution']),
                                 (140, 0) if full_refund else (140, 300))
                self.assertEqual(ConsumptionConfirmation.objects.get(order=order).amount, 4000)
                consumption = OrderAddonConsumption.objects.get(payment_id=payment_id)
                self.assertEqual((consumption.paid_units, consumption.refunded_units),
                                 (1000, 1000) if full_refund else (1000, 500))
            with store_context(self.first):
                self.assertFalse(OrderSegmentAddonSettlement.objects.exists())
