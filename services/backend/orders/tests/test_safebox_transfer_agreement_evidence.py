"""Draft evidence is bound to real transfer facts, without accepting consent."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from rest_framework.exceptions import ValidationError

from coupons import test_coupon_availability_workflow as coupon_workflow
from orders.models import (AfterSale, NegotiationDecision, NegotiationProposal, OrderOriginalShareTransfer,
                           SubsidyTransferAgreementTerms)
from orders.tests import test_safebox_segment_capture as capture_fixture
from orders.tests import test_safebox_transfer_negotiation_preview as preview_fixture
from wallet.models import Transaction


@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_NEGOTIATION_ENABLED=True,
    VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=True, VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True,
    VIP_SAFE_BOX_SEGMENT_SETTLEMENT_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PREVIEW_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EVIDENCE_TESTING=True, NEGOTIATION_PROPOSAL_ENABLED=True,
    ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    SEGMENT_STANDARD_TRANSFER_ENABLED=True, SEGMENT_REPORT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    PRESTART_HANDOFF_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class SafeBoxTransferAgreementEvidenceTest(TestCase):
    setUp = coupon_workflow.CouponAvailabilityWorkflowTest.setUp
    client_for = staticmethod(coupon_workflow.CouponAvailabilityWorkflowTest.client_for)
    ok = coupon_workflow.CouponAvailabilityWorkflowTest.ok
    prepare = capture_fixture.SafeBoxSegmentCaptureTest.prepare
    returned = preview_fixture.SafeBoxTransferNegotiationPreviewTest.returned

    def draft(self, *, retained=1200):
        from orders.negotiation_proposals import _digest
        order, players, _, current, case_id = self.returned()
        case = AfterSale.objects.get(pk=case_id)
        operator = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).first().operator
        scope = [players[0].pk]
        terms = [{'account_id':players[0].pk, 'original_income_units':3200, 'retained_units':retained,
                  'burden_units':max(3200-retained, 0), 'additional_units':max(retained-3200, 0)}]
        # This is an internal, unpublished fixture; the public API still refuses these proposals.
        proposal = NegotiationProposal.objects.create(case=case, case_opened_at=case.created_at,
            case_request_id=case.client_request_id, version=1, request_id='evidence-internal', created_by=operator,
            affected_accounts=scope, refund_units=0, provider_terms=terms, explanation='仅核定当前段',
            terms_hash=_digest(case, 1, scope, 0, terms, '仅核定当前段'))
        NegotiationDecision.objects.create(proposal=proposal, account=order.customer_account, role='BOSS')
        NegotiationDecision.objects.create(proposal=proposal, account=players[0], role='PROVIDER')
        return order, players, current, proposal

    def test_binds_current_segment_and_prior_income_once_without_money_or_consent(self):
        from orders.subsidy_transfer_agreement_evidence import bind_draft, verify_draft
        order, players, current, proposal = self.draft()
        terms = bind_draft(proposal.pk)
        self.assertEqual((terms.segment_id, terms.account_id, terms.prior_income, terms.original_income,
                          terms.retained_income), (current.pk, players[0].pk, 3200, 3200, 1200))
        self.assertEqual(bind_draft(proposal.pk).pk, terms.pk)
        self.assertEqual(verify_draft(proposal.pk).pk, terms.pk)
        self.assertEqual(len(terms.evidence['segments']), 3)
        self.assertEqual(len(terms.evidence['transfers']), 2)
        self.assertFalse(proposal.decisions.exclude(accepted__isnull=True).exists())
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').exists())

    def test_changed_transfer_agreement_invalidates_same_amount_evidence(self):
        from orders.subsidy_transfer_agreement_evidence import bind_draft, verify_draft
        order, _, _, proposal = self.draft()
        bind_draft(proposal.pk)
        transfer = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).first()
        transfer.agreement = '事后变更'; transfer.save(update_fields=['agreement'])
        with self.assertRaises(ValidationError):
            verify_draft(proposal.pk)
        with self.assertRaises(ValidationError):
            bind_draft(proposal.pk)

    def test_cannot_retain_more_than_current_segment_or_rewrite_saved_evidence(self):
        from orders.subsidy_transfer_agreement_evidence import bind_draft, verify_draft
        _, _, _, proposal = self.draft(retained=3201)
        with self.assertRaises(ValidationError):
            bind_draft(proposal.pk)
        self.assertFalse(SubsidyTransferAgreementTerms.objects.filter(proposal=proposal).exists())

    def test_saved_evidence_tamper_and_missing_table_fail_closed(self):
        from orders.subsidy_transfer_agreement_evidence import bind_draft, verify_draft
        _, _, _, proposal = self.draft()
        terms = bind_draft(proposal.pk)
        SubsidyTransferAgreementTerms.objects.filter(pk=terms.pk).update(prior_income=0)
        with self.assertRaises(ValidationError):
            verify_draft(proposal.pk)
        with patch('orders.subsidy_transfer_agreement_evidence._tables_ready', return_value=False):
            with self.assertRaises(ValidationError):
                bind_draft(proposal.pk)

    def test_existing_snapshot_retries_after_first_consent_but_cannot_be_created_then(self):
        from django.utils import timezone
        from orders.subsidy_transfer_agreement_evidence import bind_draft, verify_draft
        _, _, _, proposal = self.draft()
        terms = bind_draft(proposal.pk)
        proposal.decisions.filter(role='BOSS').update(accepted=True, decided_at=timezone.now())
        self.assertEqual(bind_draft(proposal.pk).pk, terms.pk)
        self.assertEqual(verify_draft(proposal.pk).pk, terms.pk)
        terms.delete()
        with self.assertRaises(ValidationError):
            bind_draft(proposal.pk)

    def test_complete_consent_still_verifies_original_evidence(self):
        from django.utils import timezone
        from orders.subsidy_transfer_agreement_evidence import bind_draft, verify_draft
        _, _, _, proposal = self.draft()
        terms = bind_draft(proposal.pk)
        when = timezone.now()
        proposal.decisions.update(accepted=True, decided_at=when)
        proposal.status='ACCEPTED'; proposal.accepted_at=when
        proposal.save(update_fields=['status', 'accepted_at'])
        self.assertEqual(verify_draft(proposal.pk).pk, terms.pk)


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_NEGOTIATION_ENABLED=True,
    VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=True, VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True,
    VIP_SAFE_BOX_SEGMENT_SETTLEMENT_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PREVIEW_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EVIDENCE_TESTING=True, NEGOTIATION_PROPOSAL_ENABLED=True,
    ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    SEGMENT_STANDARD_TRANSFER_ENABLED=True, SEGMENT_REPORT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    PRESTART_HANDOFF_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class SafeBoxTransferAgreementEvidenceStoresTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    client_for = staticmethod(capture_fixture.SafeBoxSegmentCaptureStoresTest.client_for)
    ok = coupon_workflow.CouponAvailabilityWorkflowTest.ok
    prepare = capture_fixture.SafeBoxSegmentCaptureTest.prepare
    returned = preview_fixture.SafeBoxTransferNegotiationPreviewTest.returned
    draft = SafeBoxTransferAgreementEvidenceTest.draft

    def test_same_proposal_id_keeps_snapshot_and_changes_in_its_store(self):
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        from orders.subsidy_transfer_agreement_evidence import bind_draft, verify_draft
        entries = []
        for store in (resolve_store(), xinan_store()):
            with store_context(store):
                coupon_workflow.CouponAvailabilityWorkflowTest.setUp(self)
                order, _, _, proposal = self.draft()
                saved = bind_draft(proposal.pk)
                entries.append((store, order.pk, proposal.pk, saved.pk))
        first, second = entries
        self.assertEqual(first[1:], second[1:])
        with store_context(second[0]):
            SubsidyTransferAgreementTerms.objects.filter(pk=second[3]).update(prior_income=0)
            with self.assertRaises(ValidationError):
                verify_draft(second[2])
        with store_context(first[0]):
            self.assertEqual(verify_draft(first[2]).prior_income, 3200)
            self.assertFalse(Transaction.objects.filter(order_id=first[1], tx_type='SUBSIDY_EXPENSE').exists())


@skipUnlessDBFeature('has_select_for_update')
@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_NEGOTIATION_ENABLED=True,
    VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=True, VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True,
    VIP_SAFE_BOX_SEGMENT_SETTLEMENT_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PREVIEW_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EVIDENCE_TESTING=True, NEGOTIATION_PROPOSAL_ENABLED=True,
    ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    SEGMENT_STANDARD_TRANSFER_ENABLED=True, SEGMENT_REPORT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    PRESTART_HANDOFF_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class SafeBoxTransferAgreementEvidenceRaceTest(TransactionTestCase):
    setUp = coupon_workflow.CouponAvailabilityWorkflowTest.setUp
    client_for = staticmethod(coupon_workflow.CouponAvailabilityWorkflowTest.client_for)
    ok = coupon_workflow.CouponAvailabilityWorkflowTest.ok
    prepare = capture_fixture.SafeBoxSegmentCaptureTest.prepare
    returned = preview_fixture.SafeBoxTransferNegotiationPreviewTest.returned
    draft = SafeBoxTransferAgreementEvidenceTest.draft

    def test_concurrent_identical_bind_creates_one_snapshot(self):
        from orders.subsidy_transfer_agreement_evidence import bind_draft
        _, _, _, proposal = self.draft()
        barrier = Barrier(2)
        def run(_):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return bind_draft(proposal.pk).pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, (0, 1)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(SubsidyTransferAgreementTerms.objects.filter(proposal=proposal).count(), 1)
