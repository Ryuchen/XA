"""Customer reopens a real case while staff reviews it at mobile width."""
import json
import os
import shutil
import subprocess
import socket
import sys
import time
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from rest_framework.test import APITransactionTestCase
from django.db import connection

from orders.models import AfterSale, Order
from orders.tests import test_provider_aftersale_status as flow


@skipUnless(os.environ.get('XA_AFTERSALE_HISTORY_BROWSER') == '1', 'Requires isolated browser and database')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class AdminAfterSaleHistoryBrowserTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        flow.ProviderAfterSaleStatusTest.setUp(self)

    login = flow.ProviderAfterSaleStatusTest.login
    ok = flow.ProviderAfterSaleStatusTest.ok
    prepare = flow.ProviderAfterSaleStatusTest.prepare
    open_aftersale = flow.ProviderAfterSaleStatusTest.open_aftersale
    resolve = flow.ProviderAfterSaleStatusTest.resolve

    def test_reopened_history_in_mobile_order_drawer(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        order_id, _ = self.prepare()
        ticket_id = self.open_aftersale(order_id)
        self.resolve(ticket_id)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/admin-aftersale-history-mobile-live.cjs')],
            input=json.dumps({'api': self.live_server_url, 'orderId': order_id,
                              'orderNo': Order.objects.get(pk=order_id).order_no,
                              'staffToken': self.tokens[self.staff.pk], 'bossToken': self.tokens[self.boss.pk]}),
            text=True, encoding='utf-8', capture_output=True, timeout=90, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        ticket = AfterSale.objects.get(pk=ticket_id)
        self.assertEqual((ticket.status, ticket.reason), ('OPEN', '第二次核对服务'))
        self.assertEqual([item['status'] for item in ticket.history], ['OPEN', 'RESOLVED', 'OPEN'])

    def test_dashboard_queue_opens_and_resolves_oldest_case(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        role = self.staff.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'dashboard:view']
        role.save(update_fields=['permissions'])
        first, _ = self.prepare()
        first_ticket = self.open_aftersale(first)
        second, _ = self.prepare()
        self.open_aftersale(second)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/admin-aftersale-queue-mobile-live.cjs')],
            input=json.dumps({'api': self.live_server_url, 'token': self.tokens[self.staff.pk],
                              'firstNo': Order.objects.get(pk=first).order_no,
                              'secondNo': Order.objects.get(pk=second).order_no}),
            text=True, encoding='utf-8', capture_output=True, timeout=90, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        ticket = AfterSale.objects.get(pk=first_ticket)
        self.assertEqual((ticket.status, ticket.resolution), ('RESOLVED', '已沟通并处理'))


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class AdminAfterSaleRealtimeBrowserTest(APITransactionTestCase):
    def setUp(self):
        super().setUp()
        flow.ProviderAfterSaleStatusTest.setUp(self)

    login = flow.ProviderAfterSaleStatusTest.login
    ok = flow.ProviderAfterSaleStatusTest.ok
    prepare = flow.ProviderAfterSaleStatusTest.prepare
    open_aftersale = flow.ProviderAfterSaleStatusTest.open_aftersale
    resolve = flow.ProviderAfterSaleStatusTest.resolve

    def test_reopened_case_updates_open_drawer_via_real_socket(self):
        database = os.environ.get('XA_PROVIDER_CHAT_QA_DB')
        node = shutil.which('node')
        if not node or not database or connection.settings_dict['NAME'] != database:
            self.skipTest('Requires isolated file-backed database and Node')
        order_id, _ = self.prepare()
        ticket_id = self.open_aftersale(order_id)
        self.resolve(ticket_id)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        process = subprocess.Popen([sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', str(port), 'config.asgi:application'],
            cwd=settings.BASE_DIR, env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            for _ in range(150):
                if process.poll() is not None:
                    self.fail('isolated ASGI server exited')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2):
                        break
                except OSError:
                    time.sleep(.1)
            else:
                self.fail('isolated ASGI server did not start')
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run([node, str(repo / 'scripts/admin-aftersale-history-mobile-live.cjs')],
                input=json.dumps({'api': f'http://127.0.0.1:{port}', 'orderId': order_id,
                                  'orderNo': Order.objects.get(pk=order_id).order_no, 'realtime': True,
                                  'staffToken': self.tokens[self.staff.pk], 'bossToken': self.tokens[self.boss.pk]}),
                text=True, encoding='utf-8', capture_output=True, timeout=90, cwd=repo)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            ticket = AfterSale.objects.get(pk=ticket_id)
            self.assertEqual((ticket.status, ticket.reason), ('OPEN', '第二次核对服务'))
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=10)
