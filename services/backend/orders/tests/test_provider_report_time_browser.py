"""Real report timestamps displayed consistently across server and device timezones."""
import json
import os
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless
from zoneinfo import ZoneInfo
from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.tokens import issue_account_tokens
from orders.models import OrderRefund
from orders.tests import test_release_flow as flow
from wallet.models import Wallet


@skipUnless(os.environ.get('XA_REPORT_TIME_BROWSER') == '1', 'Requires isolated browser build and database')
@override_settings(TIME_ZONE='UTC', ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False,
                   WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ProviderReportTimeBrowserTest(LiveServerTestCase):
    def setUp(self):
        self.client = APIClient()
        flow.ReleaseFlowTest.setUp(self)

    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    finish = flow.ReleaseFlowTest.finish

    def test_real_submission_uses_beijing_time(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        order = self.new_order()
        report = self.finish(order)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        # Simulate an elapsed aftersale window; settlement itself runs unmodified.
        earlier = timezone.now() - timedelta(hours=73)
        type(order).objects.filter(pk=order.pk).update(completed_at=earlier)
        type(report).objects.filter(pk=report.pk).update(submitted_at=earlier)
        from engagement.confirmation import process_order
        process_order(order.pk)
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        report.refresh_from_db()
        preview = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/', {'refund_amount': 2500}))
        self.ok(self.client.post(f'/api/admin/orders/{order.pk}/refund/', {'refund_amount': 2500,
            'reason': '隔离验收协商退款', 'client_request_id': 'report-time-refund-qa',
            'plan_token': preview['plan_token']}, format='json'))
        refund = OrderRefund.objects.get(order=order)
        self.assertEqual(refund.amount, 2500)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 13500)
        self.login(self.boss)
        boss_order = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        self.assertEqual((boss_order['payment_status'], boss_order['refund_summary']['amount']), ('PARTIALLY_REFUNDED', 2500))
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/provider-report-time-375.cjs')],
            input=json.dumps({'base': self.live_server_url, 'providerToken': issue_account_tokens(self.accounts[self.provider.pk])[0],
                'providerId': str(self.accounts[self.provider.pk].pk), 'orderNo': order.order_no,
                'beijingTime': timezone.localtime(report.submitted_at, ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M'),
                'refundTime': timezone.localtime(refund.created_at, ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M'),
                'refundId': refund.pk}),
            text=True, encoding='utf-8', capture_output=True, timeout=90, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
