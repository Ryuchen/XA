"""Three real roles confirm and revise a negotiation proposal through the HTTP API."""
import os
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from orders.models import NegotiationDecision, NegotiationProposal, Order
from orders.tests.test_provider_aftersale_status import ProviderAfterSaleStatusTest, image
from wallet.models import Wallet


@skipUnless(os.environ.get('XA_NEGOTIATION_PROPOSAL_BROWSER') == '1', 'Requires isolated ASGI and local Vite')
@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class NegotiationProposalBrowserTest(LiveServerTestCase):
    setUp = ProviderAfterSaleStatusTest.setUp
    login = ProviderAfterSaleStatusTest.login
    ok = ProviderAfterSaleStatusTest.ok

    def test_three_roles_confirm_and_recover_lost_results(self):
        self.client = APIClient()
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'provider_id':self.accounts[self.provider.pk].pk, 'client_request_id':'proposal-browser-order'}, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image':image()}, format='multipart'))
        balance = Wallet.objects.get(user=self.boss).balance
        role = self.staff.admin_membership.roles.first()
        role.permissions = list(set([*role.permissions, 'support:edit', 'order:refund'])); role.save(update_fields=['permissions'])
        self.login(self.staff)
        case_id = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id':order_id,
            'affected_provider_account_ids':[self.accounts[self.provider.pk].pk], 'reason':'老板联系后申请协商',
            'client_request_id':'proposal-browser-case'}, format='json'))['id']
        order = Order.objects.get(pk=order_id)
        provider_account_id = self.accounts[self.provider.pk].pk
        proposal_url = f'/api/admin/aftersales/{case_id}/negotiation-proposal/'
        draft_v1 = {'request_id':'proposal-browser-v1', 'expected_version':0, 'refund_units':500,
            'provider_terms':[{'account_id':provider_account_id, 'retained_units':7500}],
            'explanation':'双方协商后中止服务'}
        v1 = self.ok(self.client.post(proposal_url, draft_v1, format='json'))
        self.ok(self.client.post(proposal_url, draft_v1, format='json'))
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/', {'completion_image':image()}, format='multipart'))
        self.login(self.boss)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
            {'proposal_id':v1['id'], 'accepted':True}, format='json'))
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
            {'proposal_id':v1['id'], 'accepted':True}, format='json'))
        self.login(self.staff)
        blocked = self.client.post(f'/api/admin/aftersales/{case_id}/resolve/', {'resolution':'尝试结案'}, format='json')
        self.assertEqual(blocked.data['msg'], '已有各方同意的协商方案，请先完成约定处理，不能直接结束售后')
        v2 = self.ok(self.client.post(proposal_url, {'request_id':'proposal-browser-v2', 'expected_version':1,
            'refund_units':600, 'provider_terms':[{'account_id':provider_account_id, 'retained_units':7400}],
            'explanation':'双方协商后中止服务'}, format='json'))
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
            {'proposal_id':v2['id'], 'accepted':False}, format='json'))
        proposal = NegotiationProposal.objects.get(case__order=order, version=1)
        self.assertEqual((proposal.status, proposal.refund_units, proposal.provider_terms[0]['retained_units']),
                         ('ACCEPTED', 500, 7500))
        self.assertEqual(proposal.case.status, 'OPEN')
        self.assertEqual(NegotiationDecision.objects.filter(proposal=proposal, accepted=True).count(), 2)
        from site_messages.models import Message
        self.assertEqual(NegotiationProposal.objects.get(case__order=order, version=2).status, 'REJECTED')
        self.assertEqual(Message.objects.filter(related_order_id=order_id, title='订单协商方案待确认').count(), 4)
        self.assertEqual(Message.objects.filter(related_order_id=order_id, title='订单协商方案已确认').count(), 2)
        self.assertEqual(Message.objects.filter(related_order_id=order_id, title='订单协商方案有异议').count(), 2)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, balance)
        self.assertFalse(order.refund_records.exists())
