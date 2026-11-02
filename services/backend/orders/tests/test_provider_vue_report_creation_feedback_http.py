"""A stale report picker shows an actual rejection in the active dialog."""
import json
import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from orders.tests import test_release_flow as flow
from wallet.models import ProviderReport


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_RECHARGE_PROOF=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueReportCreationFeedbackHTTPTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        flow.ReleaseFlowTest.setUp(self)

    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order

    def test_rejected_order_is_reported_inside_picker(self):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root:
            self.skipTest('requires isolated provider Vue build')
        order = self.new_order()
        self.assertFalse(ProviderReport.objects.filter(order=order).exists())
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/provider-vue-report-creation-feedback-375.cjs')],
            input=json.dumps({'root': root, 'base': self.live_server_url, 'orderId': order.pk,
                'orderNo': order.order_no, 'serviceId': self.service.pk,
                'id': self.accounts[self.provider.pk].pk,
                'token': issue_account_tokens(self.accounts[self.provider.pk])[0],
                'bossToken': issue_account_tokens(self.accounts[self.boss.pk])[0],
                'staffToken': issue_account_tokens(self.accounts[self.admin.pk])[0],
                'fixed': os.environ.get('XA_REPORT_PICKER_FIXED') == '1'}),
            text=True, encoding='utf-8', capture_output=True, timeout=45, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout.strip())
        order.refresh_from_db()
        self.assertEqual(order.status, 'PENDING')
        self.assertFalse(ProviderReport.objects.filter(order=order).exists())
        if os.environ.get('XA_REPORT_PICKER_FIXED') == '1':
            self.assertTrue(ProviderReport.objects.filter(provider=self.provider).exclude(order=order).exists())
