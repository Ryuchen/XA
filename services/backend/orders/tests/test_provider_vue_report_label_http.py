"""Report labels reflect real audit state after refunds."""
import json
import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests import test_release_flow as flow
from wallet.models import ProviderReport


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ProviderReportLabelHTTPTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        flow.ReleaseFlowTest.setUp(self)

    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    finish = flow.ReleaseFlowTest.finish
    settle_after_window = flow.ReleaseFlowTest.settle_after_window

    def exercise(self, approved, *, settled=False):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root:
            self.skipTest('requires isolated provider Vue build')
        order = self.new_order()
        report = self.finish(order)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                                 {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        if approved:
            self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
            if settled:
                self.settle_after_window()
                order.refresh_from_db()
                self.assertIsNotNone(order.settled_at)
        else:
            self.ok(self.client.post(f'/api/admin/reports/{report.pk}/reject/',
                                     {'audit_remark': '隔离测试需要补充'}, format='json'))
        url = f'/api/admin/orders/{order.pk}/'
        amount = 2500 if settled else order.amount
        preview = self.ok(self.client.get(url + 'refund-preview/', {'refund_amount': amount}))
        self.ok(self.client.post(url + 'refund/', {'refund_amount': amount, 'reason': '隔离退款核对',
            'plan_token': preview['plan_token'], 'client_request_id': 'report-label-refund'}, format='json'))
        self.login(self.provider)
        reports = self.ok(self.client.get('/api/wallet/reports/'))
        record = next(row for row in reports if row['id'] == report.pk)
        self.assertEqual(record['status'], 'APPROVED' if approved else 'REJECTED')
        self.assertEqual(record['expected_income'], 8000)
        self.assertEqual(record['refund_summary']['net_income'], 6000 if settled else 0)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/provider-vue-report-label-375.cjs')],
            input=json.dumps({'root': root, 'base': self.live_server_url,
                'token': issue_account_tokens(self.accounts[self.provider.pk])[0],
                'id': self.accounts[self.provider.pk].pk, 'orderId': order.pk, 'orderNo': order.order_no,
                'statusText': record['status_display'], 'label': '原核定单钱' if approved else '原预计单钱',
                'snapshot': 800, 'refundText': '累计已退 250 币' if settled else '累计已退 1000 币',
                'netText': '服务净收益 600 币' if settled else '服务净收益 0 币'}),
            text=True, encoding='utf-8', capture_output=True, timeout=35, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_approved_partial_refund(self):
        self.exercise(True, settled=True)

    def test_approved_unsettled_full_refund(self):
        self.exercise(True)

    def test_rejected_unsettled_full_refund(self):
        self.exercise(False)
