"""Reconcile an accepted current-segment subsidy without posting money."""
from unittest import skipUnless

from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from coupons import test_coupon_availability_workflow as coupon_workflow
from orders.models import SubsidyTransferAgreementTerms
from orders.tests import test_safebox_segment_capture as capture_fixture
from orders.tests import test_safebox_transfer_negotiation_preview as preview_fixture
from orders.tests import test_safebox_transfer_agreement_evidence as evidence_fixture
from wallet.models import Transaction, get_platform_wallet


@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_NEGOTIATION_ENABLED=True,
    VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=True, VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True,
    VIP_SAFE_BOX_SEGMENT_SETTLEMENT_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PREVIEW_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EVIDENCE_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PLAN_TESTING=True,
    NEGOTIATION_PROPOSAL_ENABLED=True, ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    SEGMENT_STANDARD_TRANSFER_ENABLED=True, SEGMENT_REPORT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    PRESTART_HANDOFF_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class SafeBoxTransferAgreementPlanTest(TestCase):
    setUp = coupon_workflow.CouponAvailabilityWorkflowTest.setUp
    client_for = staticmethod(coupon_workflow.CouponAvailabilityWorkflowTest.client_for)
    ok = coupon_workflow.CouponAvailabilityWorkflowTest.ok
    prepare = capture_fixture.SafeBoxSegmentCaptureTest.prepare
    returned = preview_fixture.SafeBoxTransferNegotiationPreviewTest.returned
    draft = evidence_fixture.SafeBoxTransferAgreementEvidenceTest.draft

    def accepted(self, *, retained=1200):
        from orders.subsidy_transfer_agreement_evidence import bind_draft
        order, players, current, proposal = self.draft(retained=retained)
        bind_draft(proposal.pk)
        when = timezone.now()
        proposal.decisions.update(accepted=True, decided_at=when)
        proposal.status='ACCEPTED'; proposal.accepted_at=when
        proposal.save(update_fields=['status', 'accepted_at'])
        return order, players, current, proposal

    def test_prior_and_other_provider_preserved_and_no_money_posted(self):
        from orders.subsidy_transfer_agreement_plan import calculate
        order, players, current, proposal = self.accepted()
        platform = get_platform_wallet(); platform.balance=5999; platform.save(update_fields=['balance'])
        plan = calculate(proposal.pk)
        self.assertEqual((plan['negotiated_reduction_units'], plan['required_subsidy_units'],
                          plan['funding_shortfall_units'], plan['fully_funded']), (2000, 6000, 1, False))
        self.assertEqual([(item['account_id'], item['original_income_units'], item['retained_income_units'])
                          for item in plan['people']], [(players[0].pk, 6400, 4400), (players[1].pk, 1600, 1600)])
        self.assertEqual([(part['income'], part['retained_income']) for part in plan['segments']],
                         [(3200, 3200), (1600, 1600), (3200, 1200)])
        self.assertEqual(plan['segment_id'], current.pk)
        platform.refresh_from_db(); self.assertEqual(platform.balance, 5999)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type__in=['SUBSIDY_EXPENSE', 'INCOME']).exists())
        platform.balance=6000; platform.save(update_fields=['balance'])
        self.assertTrue(calculate(proposal.pk)['fully_funded'])

    def test_zero_current_segment_retention(self):
        from orders.subsidy_transfer_agreement_plan import calculate
        _, _, _, proposal = self.accepted(retained=0)
        plan = calculate(proposal.pk)
        self.assertEqual((plan['required_subsidy_units'], plan['negotiated_reduction_units']), (4800, 3200))

    def test_full_current_segment_retention(self):
        from orders.subsidy_transfer_agreement_plan import calculate
        _, _, _, proposal = self.accepted(retained=3200)
        plan = calculate(proposal.pk)
        self.assertEqual((plan['required_subsidy_units'], plan['negotiated_reduction_units']), (8000, 0))

    def test_unconfirmed_or_tampered_evidence_cannot_generate_plan(self):
        from orders.subsidy_transfer_agreement_evidence import bind_draft
        from orders.subsidy_transfer_agreement_plan import calculate
        order, _, _, proposal = self.draft()
        evidence = bind_draft(proposal.pk)
        with self.assertRaises(ValidationError):
            calculate(proposal.pk)
        when=timezone.now(); proposal.decisions.update(accepted=True, decided_at=when)
        proposal.status='ACCEPTED'; proposal.accepted_at=when; proposal.save(update_fields=['status','accepted_at'])
        SubsidyTransferAgreementTerms.objects.filter(pk=evidence.pk).update(prior_income=0)
        with self.assertRaises(ValidationError):
            calculate(proposal.pk)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').exists())

    def test_calculation_disabled_without_explicit_test_switch(self):
        from orders.subsidy_transfer_agreement_plan import calculate
        _, _, _, proposal = self.accepted()
        with override_settings(VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PLAN_TESTING=False):
            with self.assertRaises(ValidationError):
                calculate(proposal.pk)

    def test_refundable_deposits_are_excluded_from_funding(self):
        from orders.subsidy_transfer_agreement_plan import calculate
        _, _, _, proposal = self.accepted()
        platform = get_platform_wallet(); platform.balance=6000; platform.save(update_fields=['balance'])
        Transaction.objects.create(wallet=platform, tx_type='DEPOSIT_INCOME', amount=500,
                                   balance_before=5500, balance_after=6000, remark='测试押金')
        plan = calculate(proposal.pk)
        self.assertEqual((plan['available_subsidy_units'], plan['funding_shortfall_units'], plan['fully_funded']),
                         (5500, 500, False))

    def test_missing_platform_wallet_is_not_created_by_calculation(self):
        from orders.subsidy_transfer_agreement_plan import calculate
        from wallet.models import Wallet
        _, _, _, proposal = self.accepted()
        wallet = get_platform_wallet(); user_id=wallet.user_id; wallet.delete()
        with self.assertRaises(ValidationError):
            calculate(proposal.pk)
        self.assertFalse(Wallet.objects.filter(user_id=user_id).exists())


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_NEGOTIATION_ENABLED=True,
    VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=True, VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True,
    VIP_SAFE_BOX_SEGMENT_SETTLEMENT_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PREVIEW_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EVIDENCE_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PLAN_TESTING=True,
    NEGOTIATION_PROPOSAL_ENABLED=True, ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    SEGMENT_STANDARD_TRANSFER_ENABLED=True, SEGMENT_REPORT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    PRESTART_HANDOFF_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class SafeBoxTransferAgreementPlanStoresTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    client_for = staticmethod(capture_fixture.SafeBoxSegmentCaptureStoresTest.client_for)
    ok = coupon_workflow.CouponAvailabilityWorkflowTest.ok
    prepare = capture_fixture.SafeBoxSegmentCaptureTest.prepare
    returned = preview_fixture.SafeBoxTransferNegotiationPreviewTest.returned
    draft = evidence_fixture.SafeBoxTransferAgreementEvidenceTest.draft
    accepted = SafeBoxTransferAgreementPlanTest.accepted

    def test_same_proposal_id_uses_only_its_own_store_funding(self):
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        from orders.subsidy_transfer_agreement_plan import calculate
        entries = []
        for store, balance in ((resolve_store(), 6000), (xinan_store(), 5000)):
            with store_context(store):
                coupon_workflow.CouponAvailabilityWorkflowTest.setUp(self)
                order, _, _, proposal = self.accepted()
                wallet = get_platform_wallet(); wallet.balance=balance; wallet.save(update_fields=['balance'])
                entries.append((store, order.pk, proposal.pk))
        self.assertEqual(entries[0][1:], entries[1][1:])
        for (store, order_id, proposal_id), expected in zip(entries, (0, 1000)):
            with store_context(store):
                self.assertEqual(calculate(proposal_id)['funding_shortfall_units'], expected)
                self.assertFalse(Transaction.objects.filter(order_id=order_id, tx_type='SUBSIDY_EXPENSE').exists())
