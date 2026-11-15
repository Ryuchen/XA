"""Isolated real HTTP/browser test for staff refund and lost responses."""
import json
import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from orders.models import NegotiationExecution, Order
from orders.tests import test_provider_aftersale_status as fixture
from wallet.models import Wallet
from wallet.services import get_wallet
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_NEGOTIATION_EXECUTION_BROWSER') == '1', 'Requires isolated ASGI and the local admin web')
@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
                   NEGOTIATION_ZERO_EXECUTION_ENABLED=True,
                   BOSS_FUNDING_SELECTION_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class NegotiationExecutionBrowserTest(LiveServerTestCase):
    setUp = fixture.ProviderAfterSaleStatusTest.setUp
    login = fixture.ProviderAfterSaleStatusTest.login
    ok = fixture.ProviderAfterSaleStatusTest.ok

    def test_staff_recovers_response_lost_after_real_refund(self):
        self.exercise_browser(zero=False)

    def test_staff_recovers_response_lost_after_zero_refund_termination(self):
        self.exercise_browser(zero=True)

    def exercise_browser(self, *, zero):
        self.client = APIClient()
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'provider_id': self.accounts[self.provider.pk].pk, 'client_request_id':'execute-browser-order'}, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': fixture.image()}, format='multipart'))
        get_wallet(user=self.provider, account=self.accounts[self.provider.pk])
        role = self.staff.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'support:edit', 'order:refund']; role.save(update_fields=['permissions'])
        self.login(self.staff)
        case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id':order_id,
            'affected_provider_account_ids':[self.accounts[self.provider.pk].pk], 'reason':'隔离浏览器协商',
            'client_request_id':'execute-browser-case'}, format='json'))
        proposal = self.ok(self.client.post(f"/api/admin/aftersales/{case['id']}/negotiation-proposal/", {
            'request_id':'execute-browser-proposal', 'expected_version':0, 'refund_units':0 if zero else 5000,
            'provider_terms':[{'account_id':self.accounts[self.provider.pk].pk, 'retained_units':8000 if zero else 3000}],
            'explanation':'双方同意协商终止'}, format='json'))
        for actor in (self.boss, self.provider):
            self.login(actor)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
                {'proposal_id':proposal['id'], 'accepted':True}, format='json'))
        before = Wallet.objects.get(user=self.boss).balance
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/negotiation-execution-browser.cjs')], cwd=root,
                input=json.dumps({'api':api, 'order':order_id, 'case':case['id'], 'zero':zero,
                    'staff':{'id':self.accounts[self.staff.pk].pk, 'token':self.tokens[self.staff.pk],
                             'permissions':role.permissions}}), text=True, encoding='utf-8',
                capture_output=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        order = Order.objects.get(pk=order_id)
        self.assertEqual((order.status, order.payment_status), ('COMPLETED', 'PAID' if zero else 'PARTIALLY_REFUNDED'))
        self.assertEqual(order.refund_records.count(), 0 if zero else 1)
        receipts = NegotiationExecution.objects.filter(zero_order=order) if zero else NegotiationExecution.objects.filter(refund__order=order)
        self.assertEqual(receipts.count(), 1)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, before + (0 if zero else 5000))
        from console.models import AdminAuditLog
        self.assertEqual(AdminAuditLog.objects.filter(
            path=f"/api/admin/aftersales/{case['id']}/negotiation-execution/").count(), 1)
