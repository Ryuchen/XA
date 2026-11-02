"""Real refunded order and report-review drilldown at mobile width."""
import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.tokens import issue_account_tokens
from console.test_report_order_block import RefundedReportWorkQueueTest
from wallet.models import ProviderReport


@skipUnless(os.environ.get('XA_REPORT_BLOCK_BROWSER') == '1', 'Requires isolated browser and database')
@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
                   CORS_ALLOW_ALL_ORIGINS=True)
class DashboardReportBlockBrowserTest(LiveServerTestCase):
    client_class = APIClient
    host = '127.0.0.1'
    port = 19166
    setUp = RefundedReportWorkQueueTest.setUp
    login = RefundedReportWorkQueueTest.login
    ok = RefundedReportWorkQueueTest.ok
    recharge = RefundedReportWorkQueueTest.recharge
    new_order = RefundedReportWorkQueueTest.new_order
    finish = RefundedReportWorkQueueTest.finish
    prepare = RefundedReportWorkQueueTest.prepare
    refund = RefundedReportWorkQueueTest.refund

    def test_refunded_report_drilldown_and_rejection_at_375(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        self.prepare()
        self.refund()
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/admin-dashboard-report-block-375-live.cjs')],
            input=json.dumps({'api': self.live_server_url, 'token': issue_account_tokens(self.accounts[self.admin.pk])[0],
                              'reportId': self.report.pk, 'orderNo': self.order.order_no,
                              'providerToken': issue_account_tokens(self.accounts[self.provider.pk])[0],
                              'providerId': str(self.accounts[self.provider.pk].pk)}),
            cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, ProviderReport.Status.REJECTED)
        self.login(self.admin)
        dashboard = self.ok(self.client.get('/api/admin/dashboard/stats'))
        self.assertEqual((dashboard['pending_report_total'], dashboard['pending_report_order_blocked_total']), (1, 0))
