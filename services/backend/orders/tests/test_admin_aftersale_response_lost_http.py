"""Exercise the real admin page with authenticated HTTP and a lost response."""
import json
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from orders.models import AfterSale, OrderRefund
from orders.tests import test_provider_aftersale_status as flow
from wallet.models import Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class AdminAfterSaleResponseLostHTTPTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        flow.ProviderAfterSaleStatusTest.setUp(self)
        role = self.staff.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'order:refund']
        role.save(update_fields=['permissions'])

    login = flow.ProviderAfterSaleStatusTest.login
    ok = flow.ProviderAfterSaleStatusTest.ok
    prepare = flow.ProviderAfterSaleStatusTest.prepare
    open_aftersale = flow.ProviderAfterSaleStatusTest.open_aftersale
    settle = flow.ProviderAfterSaleStatusTest.settle

    def test_refund_and_resolution_committed_before_response_lost(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node required for admin page controller')
        order_id, report_id = self.prepare()
        self.settle(order_id)
        ticket_id = self.open_aftersale(order_id)
        second_id, _ = self.prepare()
        second_ticket = self.open_aftersale(second_id)
        balance_before = Wallet.objects.get(user=self.boss).balance
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/admin-aftersale-lost-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'orderId': order_id, 'ticketId': ticket_id,
                'secondOrder': second_id, 'secondTicket': second_ticket, 'reportId': report_id,
                'staffToken': self.tokens[self.staff.pk], 'bossToken': self.tokens[self.boss.pk],
                'providerToken': self.tokens[self.provider.pk], 'staffId': self.accounts[self.staff.pk].pk}),
            text=True, encoding='utf-8', capture_output=True, timeout=45, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(OrderRefund.objects.filter(order_id=order_id).count(), 1)
        self.assertEqual(OrderRefund.objects.get(order_id=order_id).amount, 2500)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, balance_before + 2500)
        self.assertEqual(AfterSale.objects.get(pk=ticket_id).status, 'PARTIALLY_REFUNDED')
        second = AfterSale.objects.get(pk=second_ticket)
        self.assertEqual(second.status, 'OPEN')
        self.assertEqual(second.reason, '再次申请核对')
        self.assertEqual([entry['status'] for entry in second.history], ['OPEN', 'RESOLVED', 'OPEN'])
