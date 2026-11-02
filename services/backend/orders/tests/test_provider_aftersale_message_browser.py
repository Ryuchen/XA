"""A provider follows an actual aftersale notice to its order on a phone."""
import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from orders.models import Order
from orders.tests import test_provider_aftersale_status as flow


@skipUnless(os.environ.get('XA_AFTERSALE_PROVIDER_BROWSER') == '1', 'Requires isolated H5 build and database')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class ProviderAfterSaleMessageBrowserTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        flow.ProviderAfterSaleStatusTest.setUp(self)

    login = flow.ProviderAfterSaleStatusTest.login
    ok = flow.ProviderAfterSaleStatusTest.ok
    prepare = flow.ProviderAfterSaleStatusTest.prepare
    open_aftersale = flow.ProviderAfterSaleStatusTest.open_aftersale
    resolve = flow.ProviderAfterSaleStatusTest.resolve

    def test_notice_opens_resolved_order_at_375(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        order_id, _ = self.prepare()
        ticket = self.open_aftersale(order_id)
        self.resolve(ticket)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/provider-aftersale-message-375-live.cjs')],
            input=json.dumps({'api': self.live_server_url, 'token': self.tokens[self.provider.pk],
                              'providerId': str(self.accounts[self.provider.pk].pk), 'checkReport': True,
                              'orderNo': Order.objects.get(pk=order_id).order_no}),
            text=True, encoding='utf-8', capture_output=True, timeout=90, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
