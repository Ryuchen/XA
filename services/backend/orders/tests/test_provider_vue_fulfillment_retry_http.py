"""A browser transport retry after committed fulfillment uses real APIs."""
import json
import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.tokens import issue_account_tokens
from orders.tests import test_release_flow as flow
from orders.tests import test_team_invitation_detail as invitation_flow


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_RECHARGE_PROOF=True, REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueFulfillmentRetryHTTPTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        flow.ReleaseFlowTest.setUp(self)

    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    invite = invitation_flow.TeamInvitationDetailTest.invite

    def run_browser(self, order, provider, operation, partial=False):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root:
            self.skipTest('requires isolated provider Vue build')
        file = Path(self.media.name) / 'fulfillment.png'
        file.write_bytes(flow.screenshot().read())
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/provider-vue-fulfillment-retry-375.cjs')],
            input=json.dumps({'root': root, 'base': self.live_server_url, 'orderId': order.pk,
                'orderNo': order.order_no, 'id': self.accounts[provider.pk].pk, 'operation': operation,
                'image': str(file), 'token': issue_account_tokens(self.accounts[provider.pk])[0],
                'bossToken': issue_account_tokens(self.accounts[self.boss.pk])[0],
                'staffToken': issue_account_tokens(self.accounts[self.admin.pk])[0],
                'fixed': os.environ.get('XA_FULFILLMENT_RECEIPT_FIXED') == '1', 'partial': partial}),
            text=True, encoding='utf-8', capture_output=True, timeout=45, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout.strip())

    def exercise(self, operation, partial=False):
        order = self.new_order()
        self.login(self.provider)
        if operation == 'complete':
            self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': flow.screenshot()}, format='multipart'))
        self.run_browser(order, self.provider, operation, partial)

    def test_start_response_reset(self):
        self.exercise('start')

    def test_complete_response_reset(self):
        self.exercise('complete')

    def test_start_response_truncated(self):
        if os.environ.get('XA_FULFILLMENT_RECEIPT_FIXED') != '1':
            self.skipTest('requires updated fulfillment receipt')
        self.exercise('start', partial=True)

    def test_complete_response_truncated(self):
        if os.environ.get('XA_FULFILLMENT_RECEIPT_FIXED') != '1':
            self.skipTest('requires updated fulfillment receipt')
        self.exercise('complete', partial=True)

    def test_team_last_completion_response_reset(self):
        if os.environ.get('XA_FULFILLMENT_RECEIPT_FIXED') != '1':
            self.skipTest('requires updated fulfillment receipt')
        self.invite()
        self.login(self.partner)
        self.ok(self.client.post(self.action, {'action': 'CONFIRM_TEAM'}, format='json'))
        self.login(self.boss)
        self.ok(self.client.post(f'/api/orders/orders/{self.order.pk}/applications/{self.application.pk}/select/', {}, format='json'))
        for provider in (self.provider, self.partner):
            self.login(provider)
            self.ok(self.client.post(f'/api/orders/orders/{self.order.pk}/start/', {'entry_image': flow.screenshot()}, format='multipart'))
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{self.order.pk}/complete/', {'completion_image': flow.screenshot()}, format='multipart'))
        self.run_browser(self.order, self.partner, 'complete')
        self.assertEqual(self.order.status_logs.filter(action='COMPLETE').count(), 1)
