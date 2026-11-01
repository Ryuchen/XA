"""Opt-in 375px browser review against isolated, real report APIs."""
import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from orders.tests import test_provider_aftersale_status as flow
from wallet.models import ProviderReport


@skipUnless(os.environ.get('XA_ADMIN_REPORT_375') == '1', 'Requires local browser and isolated database')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class AdminReportMobile375Test(LiveServerTestCase):
    def setUp(self):
        self.client = APIClient()
        flow.ProviderAfterSaleStatusTest.setUp(self)

    login = flow.ProviderAfterSaleStatusTest.login
    ok = flow.ProviderAfterSaleStatusTest.ok

    def test_staff_reviews_and_rejects_real_report(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'client_request_id': 'admin-report-mobile-375',
        }, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': flow.image()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/', {'completion_image': flow.image()}, format='multipart'))
        report = ProviderReport.objects.get(order_id=order_id, provider=self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': flow.image()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/admin-report-mobile-375-live.cjs')],
            input=json.dumps({'api': self.live_server_url, 'token': self.tokens[self.staff.pk], 'reportId': report.pk}),
            text=True, encoding='utf-8', capture_output=True, timeout=120, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report.refresh_from_db()
        self.assertEqual(report.status, ProviderReport.Status.REJECTED)
        self.assertEqual(report.audit_remark, '请补充清晰战绩截图')
        self.login(self.provider)
        reports = self.ok(self.client.get('/api/wallet/reports/'))
        provider_view = next(row for row in reports if row['id'] == report.pk)
        self.assertEqual((provider_view['status'], provider_view['audit_remark']),
                         ('REJECTED', '请补充清晰战绩截图'))
