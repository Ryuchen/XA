"""Atomic internal termination of an accepted safety-box transfer agreement."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from coupons import test_coupon_availability_workflow as coupon_workflow
from orders.models import AfterSale, Order, OrderPlatformSubsidyShare, SubsidyAgreementExecution
from orders.tests import test_safebox_segment_capture as capture_fixture
from orders.tests import test_safebox_transfer_negotiation_preview as preview_fixture
from orders.tests import test_safebox_transfer_agreement_evidence as evidence_fixture
from orders.tests import test_safebox_transfer_agreement_plan as plan_fixture
from wallet.models import Transaction, get_platform_wallet


@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_NEGOTIATION_ENABLED=True,
    VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=True, VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True,
    VIP_SAFE_BOX_SEGMENT_SETTLEMENT_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PREVIEW_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EVIDENCE_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PLAN_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EXECUTION_TESTING=True, NEGOTIATION_PROPOSAL_ENABLED=True,
    ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    SEGMENT_STANDARD_TRANSFER_ENABLED=True, SEGMENT_REPORT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    PRESTART_HANDOFF_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class SafeBoxTransferAgreementExecutionTest(TestCase):
    setUp = coupon_workflow.CouponAvailabilityWorkflowTest.setUp
    client_for = staticmethod(coupon_workflow.CouponAvailabilityWorkflowTest.client_for)
    ok = coupon_workflow.CouponAvailabilityWorkflowTest.ok
    prepare = capture_fixture.SafeBoxSegmentCaptureTest.prepare
    returned = preview_fixture.SafeBoxTransferNegotiationPreviewTest.returned
    draft = evidence_fixture.SafeBoxTransferAgreementEvidenceTest.draft
    accepted = plan_fixture.SafeBoxTransferAgreementPlanTest.accepted

    def execute(self, proposal, request_id='transfer-execution'):
        from django.contrib.auth import get_user_model
        from orders.subsidy_execution import execute
        operator = proposal.created_by
        legacy = get_user_model().objects.get(pk=operator.legacy_mapping.legacy_user_id)
        return execute(proposal.case_id, proposal.pk, operator=operator, legacy_operator=legacy, request_id=request_id)

    def test_accepted_termination_keeps_history_and_others_without_paying(self):
        from orders.subsidy_execution import verify_execution
        from orders.negotiation_aftersale_scope import closed_provider_accounts
        from stores.db import transaction
        order, players, current, proposal = self.accepted()
        platform = get_platform_wallet(); platform.balance=0; platform.save(update_fields=['balance'])
        receipt = self.execute(proposal)
        self.assertEqual(self.execute(proposal).pk, receipt.pk)
        order.refresh_from_db(); current.refresh_from_db()
        case = AfterSale.objects.get(pk=proposal.case_id)
        self.assertEqual((order.status, order.completed_at, case.status), ('COMPLETED', proposal.accepted_at, 'RESOLVED'))
        self.assertIsNone(current.released_at)
        self.assertEqual(list(OrderPlatformSubsidyShare.objects.filter(subsidy__order=order).order_by('assignment__provider_account_id')
                              .values_list('amount', flat=True)), [4400, 1600])
        with transaction.atomic():
            self.assertEqual(verify_execution(receipt), {players[0].pk})
            self.assertEqual(closed_provider_accounts(order), {players[0].pk})
        from orders.services import settle_order
        from orders.settlement import SettlementError
        with transaction.atomic(), self.assertRaises(SettlementError):
            settle_order(Order.objects.select_for_update().get(pk=order.pk))
        self.assertFalse(Transaction.objects.filter(order=order, tx_type__in=['INCOME','SUBSIDY_EXPENSE']).exists())

    def test_requires_consent_and_unchanged_segment_evidence(self):
        from orders.subsidy_transfer_agreement_evidence import bind_draft
        order, _, _, proposal = self.draft()
        terms = bind_draft(proposal.pk)
        with self.assertRaises(ValidationError):
            self.execute(proposal)
        when=timezone.now(); proposal.decisions.update(accepted=True, decided_at=when)
        proposal.status='ACCEPTED'; proposal.accepted_at=when; proposal.save(update_fields=['status','accepted_at'])
        type(terms).objects.filter(pk=terms.pk).update(prior_income=0)
        with self.assertRaises(ValidationError):
            self.execute(proposal)
        order.refresh_from_db(); self.assertEqual(order.status, 'IN_SERVICE')
        self.assertFalse(SubsidyAgreementExecution.objects.filter(order=order).exists())

    def test_audit_failure_rolls_back_share_case_and_order(self):
        order, _, _, proposal = self.accepted()
        with patch('console.models.AdminAuditLog.objects.create', side_effect=RuntimeError('audit failed')):
            with self.assertRaisesMessage(RuntimeError, 'audit failed'):
                self.execute(proposal)
        order.refresh_from_db(); case=AfterSale.objects.get(pk=proposal.case_id)
        self.assertEqual((order.status, case.status), ('IN_SERVICE', 'OPEN'))
        self.assertEqual(list(OrderPlatformSubsidyShare.objects.filter(subsidy__order=order).order_by('assignment__provider_account_id')
                              .values_list('amount', flat=True)), [6400, 1600])
        self.assertFalse(SubsidyAgreementExecution.objects.filter(order=order).exists())

    def test_forged_unaffected_income_cannot_pass_reconciliation(self):
        from orders.subsidy_execution import _digest, verify_execution
        from stores.db import transaction
        order, players, _, proposal = self.accepted()
        receipt = self.execute(proposal)
        shares = receipt.shares
        item = next(row for row in shares if row['account_id'] == players[1].pk)
        item['original'] += 10; item['retained'] += 10; item['original_locked'] += 10; item['locked_after'] += 10
        OrderPlatformSubsidyShare.objects.filter(subsidy__order=order, assignment__provider_account=players[1]).update(
            amount=item['retained'], retained_amount=item['locked_after'])
        receipt.shares_hash = _digest(shares); receipt.save(update_fields=['shares', 'shares_hash'])
        with transaction.atomic(), self.assertRaises(ValidationError):
            verify_execution(receipt)


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_NEGOTIATION_ENABLED=True,
    VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=True, VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True,
    VIP_SAFE_BOX_SEGMENT_SETTLEMENT_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PREVIEW_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EVIDENCE_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PLAN_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EXECUTION_TESTING=True, NEGOTIATION_PROPOSAL_ENABLED=True,
    ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, SEGMENT_STANDARD_TRANSFER_ENABLED=True,
    SEGMENT_REPORT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True, PRESTART_HANDOFF_ENABLED=True,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True, LEGACY_FORCE_AUTH_COMPAT=False,
    ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class SafeBoxTransferAgreementExecutionStoresTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    client_for = staticmethod(capture_fixture.SafeBoxSegmentCaptureStoresTest.client_for)
    ok = coupon_workflow.CouponAvailabilityWorkflowTest.ok
    prepare = capture_fixture.SafeBoxSegmentCaptureTest.prepare
    returned = preview_fixture.SafeBoxTransferNegotiationPreviewTest.returned
    draft = evidence_fixture.SafeBoxTransferAgreementEvidenceTest.draft
    accepted = plan_fixture.SafeBoxTransferAgreementPlanTest.accepted
    execute = SafeBoxTransferAgreementExecutionTest.execute

    def test_same_proposal_id_only_terminates_its_store(self):
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        entries=[]
        for store in (resolve_store(), xinan_store()):
            with store_context(store):
                coupon_workflow.CouponAvailabilityWorkflowTest.setUp(self)
                order, _, _, proposal=self.accepted()
                entries.append((store, order.pk, proposal.pk))
        self.assertEqual(entries[0][1:], entries[1][1:])
        from orders.models import NegotiationProposal
        with store_context(entries[0][0]):
            self.execute(NegotiationProposal.objects.get(pk=entries[0][2]))
            self.assertEqual(Order.objects.get(pk=entries[0][1]).status, 'COMPLETED')
        with store_context(entries[1][0]):
            self.assertEqual(Order.objects.get(pk=entries[1][1]).status, 'IN_SERVICE')
            self.assertFalse(SubsidyAgreementExecution.objects.filter(order_id=entries[1][1]).exists())


@skipUnlessDBFeature('has_select_for_update')
@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_NEGOTIATION_ENABLED=True,
    VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=True, VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True,
    VIP_SAFE_BOX_SEGMENT_SETTLEMENT_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PREVIEW_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EVIDENCE_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PLAN_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EXECUTION_TESTING=True, NEGOTIATION_PROPOSAL_ENABLED=True,
    ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, SEGMENT_STANDARD_TRANSFER_ENABLED=True,
    SEGMENT_REPORT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True, PRESTART_HANDOFF_ENABLED=True,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True, LEGACY_FORCE_AUTH_COMPAT=False,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class SafeBoxTransferAgreementExecutionRaceTest(TransactionTestCase):
    setUp = coupon_workflow.CouponAvailabilityWorkflowTest.setUp
    client_for = staticmethod(coupon_workflow.CouponAvailabilityWorkflowTest.client_for)
    ok = coupon_workflow.CouponAvailabilityWorkflowTest.ok
    prepare = capture_fixture.SafeBoxSegmentCaptureTest.prepare
    returned = preview_fixture.SafeBoxTransferNegotiationPreviewTest.returned
    draft = evidence_fixture.SafeBoxTransferAgreementEvidenceTest.draft
    accepted = plan_fixture.SafeBoxTransferAgreementPlanTest.accepted
    execute = SafeBoxTransferAgreementExecutionTest.execute

    def test_simultaneous_retry_creates_one_receipt(self):
        from orders.models import NegotiationProposal
        order, _, _, proposal=self.accepted()
        barrier=Barrier(2)
        def run(_):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return self.execute(NegotiationProposal.objects.get(pk=proposal.pk)).pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(run, (0,1)))
        self.assertEqual(results[0],results[1])
        self.assertEqual(SubsidyAgreementExecution.objects.filter(order=order).count(),1)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').exists())
