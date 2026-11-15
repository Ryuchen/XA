"""Three real web clients against isolated ASGI and a migrated SQLite DB."""
import json
import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from orders.models import NegotiationDecision, NegotiationProposal, Order
from orders.tests.test_provider_aftersale_status import ProviderAfterSaleStatusTest, image
from wallet.models import Wallet
from wallet.test_deposit_realtime_workflow import single_asgi


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
        self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id':order_id,
            'affected_provider_account_ids':[self.accounts[self.provider.pk].pk], 'reason':'老板联系后申请协商',
            'client_request_id':'proposal-browser-case'}, format='json'))
        order = Order.objects.get(pk=order_id)
        def identity(user, permissions=None):
            return {'id':self.accounts[user.pk].pk, 'token':self.tokens[user.pk], 'permissions':permissions or []}
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/negotiation-proposal-three-role.cjs')], cwd=root,
                input=json.dumps({'api':api, 'order':order_id, 'orderNo':order.order_no,
                    'boss':identity(self.boss), 'provider':identity(self.provider),
                    'staff':identity(self.staff, role.permissions)}), text=True, encoding='utf-8',
                capture_output=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
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
