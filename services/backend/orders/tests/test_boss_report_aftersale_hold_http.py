"""Owner sees an actionable aftersale state while a rejected report holds settlement."""
import json
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from orders.tests import test_provider_aftersale_status as flow
from wallet.models import ProviderReport


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class BossReportAfterSaleHoldHTTPTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        flow.ProviderAfterSaleStatusTest.setUp(self)

    login = flow.ProviderAfterSaleStatusTest.login
    ok = flow.ProviderAfterSaleStatusTest.ok

    def test_rejected_report_after_service_window_has_clear_owner_next_step(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node required for native page controller')
        past = timezone.now() - timedelta(hours=73)
        # Advance only the clock: all orders, uploads, report review and balance logic are real.
        with patch('django.utils.timezone.now', return_value=past):
            self.login(self.boss)
            order = self.ok(self.client.post('/api/orders/create/', {
                'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
                'client_request_id': 'owner-rejected-report-window',
            }, format='json'))
            self.login(self.provider)
            self.ok(self.client.post(f"/api/orders/orders/{order['id']}/start/", {'entry_image': flow.image()}, format='multipart'))
            self.ok(self.client.post(f"/api/orders/orders/{order['id']}/complete/", {'completion_image': flow.image()}, format='multipart'))
            report = ProviderReport.objects.get(order_id=order['id'], provider=self.provider)
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': flow.image()}, format='multipart'))
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
            self.login(self.staff)
            self.ok(self.client.post(f'/api/admin/reports/{report.pk}/reject/', {'audit_remark': '请补充截图'}, format='json'))
        self.login(self.boss)
        data = self.ok(self.client.get(f"/api/orders/orders/{order['id']}/"))
        self.assertFalse(data['can_aftersale'])
        self.assertIsNone(data['report_ready_at'])
        self.assertIsNone(data['settled_at'])
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-boss-report-hold-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'orderId': order['id'], 'reportId': report.pk,
                'providerToken': self.tokens[self.provider.pk], 'staffToken': self.tokens[self.staff.pk],
                'session': {'token': self.tokens[self.boss.pk],
                            'user': {'id': str(self.accounts[self.boss.pk].pk), 'role': 'customer', 'nickname': '隔离老板'}}}),
            text=True, encoding='utf-8', capture_output=True, timeout=35, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
