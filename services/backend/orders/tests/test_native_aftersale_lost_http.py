"""Native customer aftersale recovery using real order and staff APIs."""
import json
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from orders.models import AfterSale
from orders.tests import test_provider_aftersale_status as flow


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class NativeAfterSaleLostHTTPTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        flow.ProviderAfterSaleStatusTest.setUp(self)

    login = flow.ProviderAfterSaleStatusTest.login
    ok = flow.ProviderAfterSaleStatusTest.ok
    prepare = flow.ProviderAfterSaleStatusTest.prepare

    def test_lost_open_then_staff_resolve_and_boss_reopens(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node required for native page controller')
        order_id, _ = self.prepare()
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-aftersale-lost-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'orderId': order_id,
                'staffToken': self.tokens[self.staff.pk], 'session': {'token': self.tokens[self.boss.pk],
                'user': {'id': str(self.accounts[self.boss.pk].pk), 'role': 'customer', 'nickname': '隔离老板'}}}),
            text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        sale = AfterSale.objects.get(order_id=order_id)
        self.assertEqual((sale.status, sale.reason), ('OPEN', '第二次新的售后问题'))
        self.assertEqual([row['status'] for row in sale.history], ['OPEN', 'RESOLVED', 'OPEN'])
