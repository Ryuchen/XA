"""Transferred safety-box preview uses real orders and participation evidence."""
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings

from coupons import test_coupon_availability_workflow as coupon_workflow
from orders.models import (Order, OrderParticipationSegment, OrderSegmentFinancialSnapshot,
                           NegotiationProposal, SubsidyAgreementExecution)
from orders.tests.test_release_flow import screenshot
from orders.tests import test_safebox_segment_capture as capture_fixture
from wallet.models import Transaction


@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_NEGOTIATION_ENABLED=True,
    VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=True, VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True,
    VIP_SAFE_BOX_SEGMENT_SETTLEMENT_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PREVIEW_TESTING=True,
    NEGOTIATION_PROPOSAL_ENABLED=True, ORIGINAL_ATTRIBUTION_ENABLED=True,
    SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, SEGMENT_STANDARD_TRANSFER_ENABLED=True,
    SEGMENT_REPORT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True, PRESTART_HANDOFF_ENABLED=True,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
    CELERY_TASK_ALWAYS_EAGER=False)
class SafeBoxTransferNegotiationPreviewTest(TestCase):
    setUp = coupon_workflow.CouponAvailabilityWorkflowTest.setUp
    client_for = staticmethod(coupon_workflow.CouponAvailabilityWorkflowTest.client_for)
    ok = coupon_workflow.CouponAvailabilityWorkflowTest.ok
    prepare = capture_fixture.SafeBoxSegmentCaptureTest.prepare

    def returned(self):
        _, players, staff, order_id = self.prepare()
        first, second = (self.client_for(player) for player in players)
        self.ok(staff.post(f'/api/admin/orders/{order_id}/assign/', {'provider_id':players[0].pk}, format='json'))
        self.ok(first.post(f'/api/orders/orders/{order_id}/start/', {'entry_image':screenshot()}, format='multipart'))
        self.ok(staff.post(f'/api/admin/orders/{order_id}/transfer/', {'reason':'首次转单', 'transfers':[
            {'old_provider_id':players[0].pk, 'new_provider_id':players[1].pk, 'split_value':0,
             'original_share':{'retained':4000, 'transferred':6000, 'agreement':'双方确认份额'}}]}, format='json'))
        middle = OrderParticipationSegment.objects.get(attribution__order_id=order_id, provider_account=players[1])
        self.ok(second.post(f'/api/orders/orders/{order_id}/participation-start/',
                            {'segment_id':middle.pk, 'entry_image':screenshot()}, format='multipart'))
        self.ok(staff.post(f'/api/admin/orders/{order_id}/transfer/', {'reason':'再次转单', 'transfers':[
            {'old_provider_id':players[1].pk, 'new_provider_id':players[0].pk, 'split_value':0,
             'original_share':{'retained':2000, 'transferred':4000, 'agreement':'双方确认份额'}}]}, format='json'))
        current = OrderParticipationSegment.objects.get(attribution__order_id=order_id,
            provider_account=players[0], released_at__isnull=True)
        self.ok(first.post(f'/api/orders/orders/{order_id}/participation-start/',
                           {'segment_id':current.pk, 'entry_image':screenshot()}, format='multipart'))
        case = self.ok(staff.post('/api/admin/aftersales/open-negotiation/', {
            'order_id':order_id, 'reason':'协商终止', 'affected_provider_account_ids':[players[0].pk],
            'client_request_id':'transfer-subsidy-case'}, format='json'))
        return Order.objects.get(pk=order_id), players, staff, current, case['id']

    def test_preview_separates_prior_from_current_and_never_authorizes_agreement(self):
        order, players, staff, current, case_id = self.returned()
        result = self.ok(staff.get(f'/api/admin/aftersales/{case_id}/negotiation-preview/', {'refund_units':0}))
        self.assertEqual(result['segment_evidence'], {'account_id':players[0].pk, 'segment_id':current.pk,
            'prior_income_units':3200, 'current_original_income_units':3200, 'original_total_income_units':6400})
        self.assertEqual((result['read_only'], result['agreement_available'], result['execution_available']),
                         (True, False, False))
        availability = self.ok(staff.get(f'/api/admin/aftersales/{case_id}/negotiation-proposal/'))
        self.assertFalse(availability['execution_available'])
        self.assertTrue(availability['followup_preview_only'])
        proposal = staff.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/', {
            'request_id':'unsupported-proposal', 'expected_version':0, 'refund_units':0,
            'provider_terms':[{'account_id':players[0].pk, 'retained_units':4000}],
            'explanation':'保留历史和当前补贴'}, format='json')
        self.assertNotEqual(proposal.data.get('code', 400), 0)
        self.assertFalse(NegotiationProposal.objects.filter(case_id=case_id).exists())
        self.assertFalse(SubsidyAgreementExecution.objects.filter(order=order).exists())
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').exists())

    def test_tampered_entry_snapshot_is_not_presented_as_verified(self):
        order, _, staff, current, case_id = self.returned()
        snapshot = OrderSegmentFinancialSnapshot.objects.get(segment=current)
        OrderSegmentFinancialSnapshot.objects.filter(pk=snapshot.pk).update(income_at_entry=snapshot.income_at_entry+1)
        result = staff.get(f'/api/admin/aftersales/{case_id}/negotiation-preview/', {'refund_units':0})
        self.assertNotEqual(result.data.get('code', 400), 0)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').exists())


    def test_preview_switch_off_does_not_create_consent_or_payment(self):
        order, _, staff, _, case_id = self.returned()
        with override_settings(VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PREVIEW_TESTING=False):
            result = staff.get(f'/api/admin/aftersales/{case_id}/negotiation-preview/', {'refund_units':0})
        self.assertNotEqual(result.data.get('code', 400), 0)
        self.assertFalse(NegotiationProposal.objects.filter(case_id=case_id).exists())
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').exists())

    def test_missing_agreement_table_fails_closed(self):
        order, _, staff, _, case_id = self.returned()
        with patch('orders.subsidy_execution.table_available', return_value=False):
            result = staff.get(f'/api/admin/aftersales/{case_id}/negotiation-preview/', {'refund_units':0})
        self.assertNotEqual(result.data.get('code', 400), 0)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').exists())

    def test_only_current_provider_is_eligible_and_customer_cannot_use_staff_preview(self):
        from orders.models import AfterSale
        order, players, staff, _, case_id = self.returned()
        url = f'/api/admin/aftersales/{case_id}/negotiation-preview/'
        result = self.boss.get(url, {'refund_units':0})
        self.assertNotEqual(result.status_code, 200)
        AfterSale.objects.filter(pk=case_id).update(affected_provider_account_ids=[players[1].pk])
        result = staff.get(url, {'refund_units':0})
        self.assertNotEqual(result.data.get('code', 400), 0)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').exists())

@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_NEGOTIATION_ENABLED=True,
    VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=True, VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True,
    VIP_SAFE_BOX_SEGMENT_SETTLEMENT_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PREVIEW_TESTING=True,
    NEGOTIATION_PROPOSAL_ENABLED=True, ORIGINAL_ATTRIBUTION_ENABLED=True,
    SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, SEGMENT_STANDARD_TRANSFER_ENABLED=True,
    SEGMENT_REPORT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True, PRESTART_HANDOFF_ENABLED=True,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class SafeBoxTransferNegotiationStoresTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    client_for = staticmethod(capture_fixture.SafeBoxSegmentCaptureStoresTest.client_for)
    ok = coupon_workflow.CouponAvailabilityWorkflowTest.ok
    prepare = capture_fixture.SafeBoxSegmentCaptureTest.prepare
    returned = SafeBoxTransferNegotiationPreviewTest.returned

    def test_same_case_id_reads_only_the_authenticated_store(self):
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        entries = []
        for store in (resolve_store(), xinan_store()):
            with store_context(store):
                coupon_workflow.CouponAvailabilityWorkflowTest.setUp(self)
                order, _, staff, current, case_id = self.returned()
                entries.append((store, order.pk, current.pk, case_id, staff))
        first, second = entries
        self.assertEqual(first[1:4], second[1:4])
        with store_context(second[0]):
            snapshot = OrderSegmentFinancialSnapshot.objects.get(segment_id=second[2])
            OrderSegmentFinancialSnapshot.objects.filter(pk=snapshot.pk).update(income_at_entry=snapshot.income_at_entry+1)
        for entry, succeeds in ((first, True), (second, False)):
            result = entry[4].get(f'/api/admin/aftersales/{entry[3]}/negotiation-preview/', {'refund_units':0})
            self.assertEqual(result.data.get('code') == 0, succeeds, result.data)
