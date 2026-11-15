"""Current React report controls follow real rejection, correction and approval."""
import base64
import json
from datetime import timedelta
from pathlib import Path
import subprocess
from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.tokens import issue_account_tokens
from common.fronted_test_support import current_http, fronted_root
from orders.tests import test_release_flow as flow
from orders.tests.test_provider_vue_team_progress_http import isolated_realtime
from wallet.models import Transaction, Wallet


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ReportReviewLiveVueTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        flow.ReleaseFlowTest.setUp(self)

    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    finish = flow.ReleaseFlowTest.finish

    def test_reject_correct_approve_updates_provider_and_boss(self):
        fronted_root('provider')  # Current source and executable Node, no retired build flag.
        order = self.new_order()
        report = self.finish(order)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                                 {'kind':'RESULT', 'image':flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        order.refresh_from_db()
        self.assertIsNotNone(order.consumption_ready_at)
        repo = Path(settings.BASE_DIR).parent.parent
        with current_http(self, isolated_realtime, report_approval=True) as (base, ws_base):
            result = subprocess.run(['node', str(repo / 'scripts/scenarios/provider-report-review-live.cjs')], cwd=repo,
                input=json.dumps({'base':base, 'wsBase':ws_base,
                    'id':self.accounts[self.provider.pk].pk, 'token':issue_account_tokens(self.accounts[self.provider.pk])[0],
                    'staffToken':issue_account_tokens(self.accounts[self.admin.pk])[0],
                    'bossToken':issue_account_tokens(self.accounts[self.boss.pk])[0], 'bossId':self.accounts[self.boss.pk].pk,
                    'reportId':report.pk, 'orderId':order.pk, 'orderNo':order.order_no,
                    'image':base64.b64encode(flow.screenshot().read()).decode()}),
                text=True, encoding='utf-8', capture_output=True, timeout=70)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())
        report.refresh_from_db(); order.refresh_from_db()
        self.assertEqual(report.status, 'APPROVED')
        self.assertEqual(report.result_images.count(), 2)
        self.assertIsNotNone(order.consumption_ready_at)
        self.assertEqual(order.consumption_ready_at, report.submitted_at + timedelta(hours=72))
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 11000)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)
