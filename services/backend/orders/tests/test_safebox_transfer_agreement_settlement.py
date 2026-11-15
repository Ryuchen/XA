"""Negotiated transfer reports and wallet settlement through real business code."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.conf import settings
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from coupons import test_coupon_availability_workflow as coupon_workflow
from orders.models import Order, OrderParticipationSegment, OrderSegmentSettlementManifest
from orders.tests import test_safebox_segment_capture as capture_fixture
from orders.tests import test_safebox_segment_settlement as settlement_fixture
from orders.tests import test_safebox_transfer_negotiation_preview as preview_fixture
from orders.tests import test_safebox_transfer_agreement_evidence as evidence_fixture
from orders.tests import test_safebox_transfer_agreement_plan as plan_fixture
from orders.tests import test_safebox_transfer_agreement_execution as execution_fixture
from orders.tests.test_release_flow import screenshot
from wallet.models import Transaction, get_platform_wallet
from wallet.services import get_wallet


@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_NEGOTIATION_ENABLED=True,
    VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=True, VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True,
    VIP_SAFE_BOX_SEGMENT_SETTLEMENT_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PREVIEW_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EVIDENCE_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PLAN_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EXECUTION_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_SETTLEMENT_TESTING=True,
    NEGOTIATION_PROPOSAL_ENABLED=True, ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    SEGMENT_STANDARD_TRANSFER_ENABLED=True, SEGMENT_REPORT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    PRESTART_HANDOFF_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class SafeBoxTransferAgreementSettlementTest(TestCase):
    setUp = coupon_workflow.CouponAvailabilityWorkflowTest.setUp
    client_for = staticmethod(coupon_workflow.CouponAvailabilityWorkflowTest.client_for)
    ok = coupon_workflow.CouponAvailabilityWorkflowTest.ok
    prepare = capture_fixture.SafeBoxSegmentCaptureTest.prepare
    returned = preview_fixture.SafeBoxTransferNegotiationPreviewTest.returned
    draft = evidence_fixture.SafeBoxTransferAgreementEvidenceTest.draft
    accepted = plan_fixture.SafeBoxTransferAgreementPlanTest.accepted
    execute = execution_fixture.SafeBoxTransferAgreementExecutionTest.execute
    report = settlement_fixture.SafeBoxSegmentSettlementTest.report
    settle = settlement_fixture.SafeBoxSegmentSettlementTest.settle

    def ready(self):
        order, players, current, proposal = self.accepted()
        self.execute(proposal)
        segments = list(OrderParticipationSegment.objects.filter(attribution__order=order).order_by('pk'))
        clients = {account.pk:self.client_for(account) for account in players}
        staff = self.client_for(proposal.created_by)
        for segment in segments:
            client = clients[segment.provider_account_id]
            self.ok(client.post(f'/api/orders/orders/{order.pk}/participation-evidence/',
                {'segment_id':segment.pk, 'completion_image':screenshot()}, format='multipart'))
            self.report(order, staff, client, segment)
        order.refresh_from_db()
        return order, players, current, proposal

    def test_settles_negotiated_income_after_clocks_and_full_funding_once(self):
        from engagement.confirmation import process_order
        order, players, current, proposal = self.ready()
        now=timezone.now()+timedelta(hours=73)
        platform=get_platform_wallet(); platform.balance=5999; platform.save(update_fields=['balance'])
        self.assertEqual(self.settle(order, now), [])
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').exists())

        platform.balance=6000; platform.save(update_fields=['balance'])
        self.assertEqual(len(self.settle(order, now)), 2)
        self.assertEqual(self.settle(order, now), [])
        self.assertEqual([get_wallet(account=account).balance for account in players], [4400, 1600])
        manifest=OrderSegmentSettlementManifest.objects.get(order=order)
        line=next(item for item in manifest.plan['segments'] if item['segment_id'] == current.pk)
        self.assertEqual((line['original_income'], line['income'], line['negotiated_reduction']), (3200, 1200, 2000))
        self.assertEqual(manifest.plan['agreement']['proposal_id'], proposal.pk)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').count(), 2)
        from unittest.mock import patch
        with patch('django.utils.timezone.now', return_value=now):
            self.assertTrue(process_order(order.pk))

    def test_current_completion_evidence_is_only_available_to_its_provider(self):
        order, players, current, proposal = self.accepted()
        self.execute(proposal)
        path=f'/api/orders/orders/{order.pk}/participation-evidence/'
        denied=self.client_for(players[1]).post(path, {'segment_id':current.pk, 'completion_image':screenshot()}, format='multipart')
        self.assertNotEqual(denied.data.get('code', 400), 0)
        response=self.client_for(players[0]).post(path, {'segment_id':current.pk, 'completion_image':screenshot()}, format='multipart')
        self.ok(response)

    def test_corrupt_agreement_cannot_pay(self):
        from orders.models import SubsidyTransferAgreementTerms
        from orders.settlement import SettlementError
        order, _, _, proposal = self.ready()
        SubsidyTransferAgreementTerms.objects.filter(proposal=proposal).update(prior_income=0)
        platform=get_platform_wallet(); platform.balance=6000; platform.save(update_fields=['balance'])
        with self.assertRaises(SettlementError):
            self.settle(order, timezone.now()+timedelta(hours=73))
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').exists())

    def test_credit_failure_rolls_back_platform_and_provider_wallet(self):
        from unittest.mock import patch
        order, players, _, _ = self.ready()
        platform=get_platform_wallet(); platform.balance=6000; platform.save(update_fields=['balance'])
        with patch('orders.segment_settlement._report_versions', side_effect=RuntimeError('report failed')):
            with self.assertRaisesMessage(RuntimeError, 'report failed'):
                self.settle(order, timezone.now()+timedelta(hours=73))
        platform.refresh_from_db(); order.refresh_from_db()
        self.assertEqual((platform.balance, order.settled_at), (6000, None))
        self.assertEqual([get_wallet(account=account).balance for account in players], [0, 0])
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').exists())


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_NEGOTIATION_ENABLED=True,
    VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=True, VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True,
    VIP_SAFE_BOX_SEGMENT_SETTLEMENT_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PREVIEW_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EVIDENCE_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PLAN_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EXECUTION_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_SETTLEMENT_TESTING=True,
    NEGOTIATION_PROPOSAL_ENABLED=True, ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    SEGMENT_STANDARD_TRANSFER_ENABLED=True, SEGMENT_REPORT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    PRESTART_HANDOFF_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class SafeBoxTransferAgreementSettlementStoresTest(TestCase):
    databases={'default','store_b'} if 'store_b' in settings.DATABASES else {'default'}
    client_for=staticmethod(capture_fixture.SafeBoxSegmentCaptureStoresTest.client_for)
    ok=coupon_workflow.CouponAvailabilityWorkflowTest.ok
    prepare=capture_fixture.SafeBoxSegmentCaptureTest.prepare
    returned=preview_fixture.SafeBoxTransferNegotiationPreviewTest.returned
    draft=evidence_fixture.SafeBoxTransferAgreementEvidenceTest.draft
    accepted=plan_fixture.SafeBoxTransferAgreementPlanTest.accepted
    execute=execution_fixture.SafeBoxTransferAgreementExecutionTest.execute
    report=settlement_fixture.SafeBoxSegmentSettlementTest.report
    settle=settlement_fixture.SafeBoxSegmentSettlementTest.settle
    ready=SafeBoxTransferAgreementSettlementTest.ready

    def test_settlement_and_transfer_counts_are_store_local(self):
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        from orders.models import OrderTransferSeat
        entries=[]
        for store in (resolve_store(),xinan_store()):
            with store_context(store):
                coupon_workflow.CouponAvailabilityWorkflowTest.setUp(self)
                order,players,_,_=self.ready()
                wallet=get_platform_wallet(); wallet.balance=6000; wallet.save(update_fields=['balance'])
                entries.append((store,order.pk,[p.pk for p in players]))
        self.assertEqual(entries[0][1:],entries[1][1:])
        with store_context(entries[0][0]):
            order=Order.objects.get(pk=entries[0][1])
            self.assertEqual(OrderTransferSeat.objects.get(order=order).transfer_count,2)
            self.assertEqual(len(self.settle(order,timezone.now()+timedelta(hours=73))),2)
        with store_context(entries[1][0]):
            order=Order.objects.get(pk=entries[1][1])
            self.assertEqual(OrderTransferSeat.objects.get(order=order).transfer_count,2)
            self.assertIsNone(order.settled_at)
            self.assertFalse(Transaction.objects.filter(order=order,tx_type='SUBSIDY_EXPENSE').exists())
            self.assertEqual([get_wallet(account_id=pk).balance for pk in entries[1][2]],[0,0])


@skipUnlessDBFeature('has_select_for_update')
@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_NEGOTIATION_ENABLED=True,
    VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=True, VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True,
    VIP_SAFE_BOX_SEGMENT_SETTLEMENT_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PREVIEW_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EVIDENCE_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PLAN_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EXECUTION_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_SETTLEMENT_TESTING=True,
    NEGOTIATION_PROPOSAL_ENABLED=True, ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    SEGMENT_STANDARD_TRANSFER_ENABLED=True, SEGMENT_REPORT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    PRESTART_HANDOFF_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class SafeBoxTransferAgreementSettlementRaceTest(TransactionTestCase):
    setUp=coupon_workflow.CouponAvailabilityWorkflowTest.setUp
    client_for=staticmethod(coupon_workflow.CouponAvailabilityWorkflowTest.client_for)
    ok=coupon_workflow.CouponAvailabilityWorkflowTest.ok
    prepare=capture_fixture.SafeBoxSegmentCaptureTest.prepare
    returned=preview_fixture.SafeBoxTransferNegotiationPreviewTest.returned
    draft=evidence_fixture.SafeBoxTransferAgreementEvidenceTest.draft
    accepted=plan_fixture.SafeBoxTransferAgreementPlanTest.accepted
    execute=execution_fixture.SafeBoxTransferAgreementExecutionTest.execute
    report=settlement_fixture.SafeBoxSegmentSettlementTest.report
    settle=settlement_fixture.SafeBoxSegmentSettlementTest.settle
    ready=SafeBoxTransferAgreementSettlementTest.ready

    def test_concurrent_settlement_pays_each_person_once(self):
        from unittest.mock import patch
        order,players,_,_=self.ready()
        platform=get_platform_wallet(); platform.balance=6000; platform.save(update_fields=['balance'])
        barrier=Barrier(2); now=timezone.now()+timedelta(hours=73)
        def run(_):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return self.settle(order,now)
            finally:
                close_old_connections()
        with patch('django.utils.timezone.now',return_value=now),ThreadPoolExecutor(max_workers=2) as pool:
            result=list(pool.map(run,(0,1)))
        self.assertEqual(sorted(map(len,result)),[0,2])
        self.assertEqual([get_wallet(account=player).balance for player in players],[4400,1600])
        self.assertEqual(Transaction.objects.filter(order=order,tx_type='SUBSIDY_EXPENSE').count(),2)
