"""Three-role safety-box transfer agreement and real ledger integration."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from coupons import test_coupon_availability_workflow as coupon_workflow
from orders.models import Order, OrderParticipationSegment, SubsidyTransferAgreementTerms
from orders.tests import test_safebox_segment_capture as capture_fixture
from orders.tests import test_safebox_segment_settlement as settlement_fixture
from orders.tests import test_safebox_transfer_negotiation_preview as preview_fixture
from orders.tests.test_release_flow import screenshot
from wallet.models import Transaction, get_platform_wallet
from wallet.services import get_wallet


@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_NEGOTIATION_ENABLED=True,
    VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=True, VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True,
    VIP_SAFE_BOX_SEGMENT_SETTLEMENT_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PREVIEW_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EVIDENCE_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PLAN_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EXECUTION_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_SETTLEMENT_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_AGREEMENT_TESTING=True, NEGOTIATION_PROPOSAL_ENABLED=True,
    ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, SEGMENT_STANDARD_TRANSFER_ENABLED=True,
    SEGMENT_REPORT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True, PRESTART_HANDOFF_ENABLED=True,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True, LEGACY_FORCE_AUTH_COMPAT=False,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class SafeBoxTransferAgreementApiTest(TestCase):
    setUp=coupon_workflow.CouponAvailabilityWorkflowTest.setUp
    client_for=staticmethod(coupon_workflow.CouponAvailabilityWorkflowTest.client_for)
    ok=coupon_workflow.CouponAvailabilityWorkflowTest.ok
    prepare=capture_fixture.SafeBoxSegmentCaptureTest.prepare
    returned=preview_fixture.SafeBoxTransferNegotiationPreviewTest.returned
    report=settlement_fixture.SafeBoxSegmentSettlementTest.report
    settle=settlement_fixture.SafeBoxSegmentSettlementTest.settle

    def propose(self, order, players, staff, case_id, retained=1200):
        url=f'/api/admin/aftersales/{case_id}/negotiation-proposal/'
        payload={'request_id':'transfer-api-proposal', 'expected_version':0, 'refund_units':0,
                 'provider_terms':[{'account_id':players[0].pk, 'retained_units':retained}],
                 'explanation':'按当前已履约内容核定补贴'}
        return self.ok(staff.post(url, payload, format='json')), payload

    def test_three_roles_confirm_execute_report_and_settle(self):
        order,players,staff,current,case_id=self.returned()
        preview=self.ok(staff.get(f'/api/admin/aftersales/{case_id}/negotiation-preview/', {'refund_units':0}))
        self.assertTrue(preview['agreement_available']); self.assertEqual(preview['segment_evidence']['prior_income_units'],3200)
        proposal,payload=self.propose(order,players,staff,case_id)
        self.assertEqual((proposal['segment_evidence']['segment_id'],proposal['segment_evidence']['current_retained_income_units']),
                         (current.pk,1200))
        self.assertEqual(self.ok(staff.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/',payload,format='json'))['id'],proposal['id'])
        action=f'/api/orders/orders/{order.pk}/negotiation-proposal/'
        denied=self.client_for(players[1]).post(action,{'proposal_id':proposal['id'],'accepted':True},format='json')
        self.assertNotEqual(denied.data.get('code',400),0)
        for client in (self.boss,self.client_for(players[0])):
            result=self.ok(client.post(action,{'proposal_id':proposal['id'],'accepted':True},format='json'))
        self.assertEqual(result['status'],'ACCEPTED')
        execute=f'/api/admin/aftersales/{case_id}/subsidy-negotiation-execution/'
        params={'proposal_id':proposal['id'],'client_request_id':'transfer-api-execution'}
        receipt=self.ok(staff.post(execute,params,format='json'))
        self.assertEqual(self.ok(staff.get(execute,params))['execution_id'],receipt['execution_id'])
        for segment in OrderParticipationSegment.objects.filter(attribution__order=order).order_by('pk'):
            client=self.client_for(next(p for p in players if p.pk == segment.provider_account_id))
            self.ok(client.post(f'/api/orders/orders/{order.pk}/participation-evidence/',
                {'segment_id':segment.pk,'completion_image':screenshot()},format='multipart'))
            self.report(order,staff,client,segment)
        order=Order.objects.get(pk=order.pk)
        platform=get_platform_wallet();platform.balance=6000;platform.save(update_fields=['balance'])
        now=timezone.now()+timedelta(hours=73)
        self.assertEqual(len(self.settle(order,now)),2)
        self.assertEqual([get_wallet(account=p).balance for p in players],[4400,1600])
        self.assertEqual(Transaction.objects.filter(order=order,tx_type='SUBSIDY_EXPENSE').count(),2)
        with patch('django.utils.timezone.now',return_value=now):
            from engagement.confirmation import process_order
            self.assertTrue(process_order(order.pk))

    def test_changed_evidence_blocks_confirmation_and_creates_no_payment(self):
        order,players,staff,_,case_id=self.returned()
        proposal,_=self.propose(order,players,staff,case_id)
        SubsidyTransferAgreementTerms.objects.filter(proposal_id=proposal['id']).update(prior_income=0)
        response=self.boss.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
            {'proposal_id':proposal['id'],'accepted':True},format='json')
        self.assertNotEqual(response.data.get('code',400),0)
        self.assertFalse(Transaction.objects.filter(order=order,tx_type='SUBSIDY_EXPENSE').exists())

    def test_zero_current_segment_preserves_the_same_players_previous_income(self):
        order,players,staff,_,case_id=self.returned()
        proposal,_=self.propose(order,players,staff,case_id,retained=0)
        action=f'/api/orders/orders/{order.pk}/negotiation-proposal/'
        for client in (self.boss,self.client_for(players[0])):
            self.ok(client.post(action,{'proposal_id':proposal['id'],'accepted':True},format='json'))
        self.ok(staff.post(f'/api/admin/aftersales/{case_id}/subsidy-negotiation-execution/',
            {'proposal_id':proposal['id'],'client_request_id':'transfer-zero-execution'},format='json'))
        for segment in OrderParticipationSegment.objects.filter(attribution__order=order).order_by('pk'):
            client=self.client_for(next(p for p in players if p.pk == segment.provider_account_id))
            self.ok(client.post(f'/api/orders/orders/{order.pk}/participation-evidence/',
                {'segment_id':segment.pk,'completion_image':screenshot()},format='multipart'))
            self.report(order,staff,client,segment)
        platform=get_platform_wallet();platform.balance=4800;platform.save(update_fields=['balance'])
        self.assertEqual(len(self.settle(Order.objects.get(pk=order.pk),timezone.now()+timedelta(hours=73))),2)
        self.assertEqual([get_wallet(account=p).balance for p in players],[3200,1600])

    def test_rejection_can_be_retried_and_replaced_with_new_version(self):
        order,players,staff,_,case_id=self.returned()
        proposal,payload=self.propose(order,players,staff,case_id)
        action=f'/api/orders/orders/{order.pk}/negotiation-proposal/'
        response={'proposal_id':proposal['id'],'accepted':False}
        self.assertEqual(self.ok(self.boss.post(action,response,format='json'))['status'],'REJECTED')
        self.assertEqual(self.ok(self.boss.post(action,response,format='json'))['status'],'REJECTED')
        payload.update(request_id='transfer-api-proposal-2',expected_version=1)
        payload['provider_terms'][0]['retained_units']=2000
        replacement=self.ok(staff.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/',payload,format='json'))
        self.assertEqual((replacement['version'],replacement['segment_evidence']['current_retained_income_units']),(2,2000))
        self.assertTrue(SubsidyTransferAgreementTerms.objects.filter(proposal_id=proposal['id']).exists())
        self.assertTrue(SubsidyTransferAgreementTerms.objects.filter(proposal_id=replacement['id']).exists())

    def test_above_original_and_disabled_feature_refuse_proposal(self):
        order,players,staff,_,case_id=self.returned()
        url=f'/api/admin/aftersales/{case_id}/negotiation-proposal/'
        payload={'request_id':'invalid-transfer-proposal','expected_version':0,'refund_units':0,
                 'provider_terms':[{'account_id':players[0].pk,'retained_units':3201}], 'explanation':'核定当前补贴'}
        self.assertNotEqual(staff.post(url,payload,format='json').data.get('code',400),0)
        payload['provider_terms'][0]['retained_units']=1000
        with override_settings(VIP_SAFE_BOX_TRANSFER_NEGOTIATION_SETTLEMENT_TESTING=False):
            self.assertNotEqual(staff.post(url,payload,format='json').data.get('code',400),0)
        self.assertFalse(SubsidyTransferAgreementTerms.objects.filter(proposal__case_id=case_id).exists())

    @override_settings(ROOM_COMPLETION_REVIEW_ENABLED=True, ROOM_BOSS_CONFIRMATION_ENABLED=True)
    def test_room_objection_closes_with_accepted_termination(self):
        from orders.models import OrderRoom, OrderRoomObjection
        order,players,staff,_,case_id=self.returned()
        Order.objects.filter(pk=order.pk).update(room_required_snapshot=True)
        order.refresh_from_db()
        room=OrderRoom.objects.create(order=order)
        objection=OrderRoomObjection.objects.create(room=room,customer=order.customer_account,
            request_id='transfer-room-objection',reason='核对房间结果')
        proposal,_=self.propose(order,players,staff,case_id)
        action=f'/api/orders/orders/{order.pk}/negotiation-proposal/'
        for client in (self.boss,self.client_for(players[0])):
            self.ok(client.post(action,{'proposal_id':proposal['id'],'accepted':True},format='json'))
        response=staff.post(f'/api/admin/aftersales/{case_id}/subsidy-negotiation-execution/',
            {'proposal_id':proposal['id'],'client_request_id':'transfer-room-execution'},format='json')
        self.ok(response)
        order.refresh_from_db();objection.refresh_from_db()
        self.assertEqual((order.status,objection.status,objection.resolution_proposal_id),
                         ('COMPLETED','RESOLVED',proposal['id']))


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_NEGOTIATION_ENABLED=True,
    VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=True, VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True,
    VIP_SAFE_BOX_SEGMENT_SETTLEMENT_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PREVIEW_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EVIDENCE_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PLAN_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EXECUTION_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_SETTLEMENT_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_AGREEMENT_TESTING=True, NEGOTIATION_PROPOSAL_ENABLED=True,
    ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, SEGMENT_STANDARD_TRANSFER_ENABLED=True,
    SEGMENT_REPORT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True, PRESTART_HANDOFF_ENABLED=True,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True, LEGACY_FORCE_AUTH_COMPAT=False,
    ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class SafeBoxTransferAgreementApiStoresTest(TestCase):
    databases={'default','store_b'} if 'store_b' in settings.DATABASES else {'default'}
    client_for=staticmethod(capture_fixture.SafeBoxSegmentCaptureStoresTest.client_for)
    ok=coupon_workflow.CouponAvailabilityWorkflowTest.ok
    prepare=capture_fixture.SafeBoxSegmentCaptureTest.prepare
    returned=preview_fixture.SafeBoxTransferNegotiationPreviewTest.returned
    propose=SafeBoxTransferAgreementApiTest.propose

    def test_same_order_and_case_id_agree_only_in_authenticated_store(self):
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        from orders.models import NegotiationProposal
        entries=[]
        for store in (resolve_store(),xinan_store()):
            with store_context(store):
                coupon_workflow.CouponAvailabilityWorkflowTest.setUp(self)
                order,players,staff,_,case_id=self.returned()
                entries.append((store,order.pk,case_id,players,staff,
                                self.client_for(order.customer_account),self.client_for(players[0])))
        self.assertEqual(entries[0][1:3],entries[1][1:3])
        first,second=entries
        proposal,_=self.propose(None,first[3],first[4],first[2])
        action=f'/api/orders/orders/{first[1]}/negotiation-proposal/'
        for client in (first[5],first[6]):
            self.ok(client.post(action,{'proposal_id':proposal['id'],'accepted':True},format='json'))
        with store_context(first[0]):
            self.assertEqual(NegotiationProposal.objects.get(pk=proposal['id']).status,'ACCEPTED')
        with store_context(second[0]):
            self.assertFalse(NegotiationProposal.objects.filter(case_id=second[2]).exists())
            self.assertFalse(Transaction.objects.filter(order_id=second[1],tx_type='SUBSIDY_EXPENSE').exists())


@skipUnlessDBFeature('has_select_for_update')
@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_NEGOTIATION_ENABLED=True,
    VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=True, VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True,
    VIP_SAFE_BOX_SEGMENT_SETTLEMENT_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PREVIEW_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EVIDENCE_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PLAN_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EXECUTION_TESTING=True, VIP_SAFE_BOX_TRANSFER_NEGOTIATION_SETTLEMENT_TESTING=True,
    VIP_SAFE_BOX_TRANSFER_NEGOTIATION_AGREEMENT_TESTING=True, NEGOTIATION_PROPOSAL_ENABLED=True,
    ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, SEGMENT_STANDARD_TRANSFER_ENABLED=True,
    SEGMENT_REPORT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True, PRESTART_HANDOFF_ENABLED=True,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True, LEGACY_FORCE_AUTH_COMPAT=False,
    ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class SafeBoxTransferAgreementApiRaceTest(TransactionTestCase):
    setUp=coupon_workflow.CouponAvailabilityWorkflowTest.setUp
    client_for=staticmethod(coupon_workflow.CouponAvailabilityWorkflowTest.client_for)
    ok=coupon_workflow.CouponAvailabilityWorkflowTest.ok
    prepare=capture_fixture.SafeBoxSegmentCaptureTest.prepare
    returned=preview_fixture.SafeBoxTransferNegotiationPreviewTest.returned
    propose=SafeBoxTransferAgreementApiTest.propose

    def test_two_parties_concurrently_confirm_the_same_snapshot(self):
        from club_accounts.tokens import issue_account_tokens
        from rest_framework.test import APIClient
        from orders.models import NegotiationProposal
        order,players,staff,_,case_id=self.returned()
        proposal,_=self.propose(order,players,staff,case_id)
        tokens=[issue_account_tokens(order.customer_account)[0],issue_account_tokens(players[0])[0]]
        barrier=Barrier(2)
        def run(token):
            close_old_connections()
            try:
                client=APIClient(HTTP_AUTHORIZATION='Bearer '+token)
                barrier.wait(timeout=10)
                result=client.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                    {'proposal_id':proposal['id'],'accepted':True},format='json')
                return result.data.get('code')
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(run,tokens))
        self.assertEqual(results,[0,0])
        stored=NegotiationProposal.objects.get(pk=proposal['id'])
        self.assertEqual(stored.status,'ACCEPTED')
        self.assertEqual(stored.decisions.filter(accepted=True).count(),2)
