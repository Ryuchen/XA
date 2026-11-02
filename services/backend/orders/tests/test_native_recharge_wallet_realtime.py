"""Boss wallet remains open through real recharge and order payment."""
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_service, make_user, make_wallet
from orders.tests.test_release_flow import screenshot
from wallet.models import Wallet


@skipUnless(os.environ.get('XA_NATIVE_RECHARGE_REALTIME') == '1', 'Requires isolated ASGI and Redis')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   REQUIRE_RECHARGE_PROOF=True, CELERY_TASK_ALWAYS_EAGER=False)
class NativeRechargeWalletRealtimeTest(TransactionTestCase):
    def test_open_boss_wallet_updates_recharge_then_order_payment(self):
        self.assertEqual(connection.settings_dict['NAME'], os.environ.get('XA_ASGI_NETWORK_TEST_DB'))
        with tempfile.TemporaryDirectory(prefix='xa-native-recharge-') as temp, override_settings(MEDIA_ROOT=temp):
            boss = make_user(); account = get_or_create_account_for_legacy_user(boss); link_legacy_relations(boss, account)
            wallet = make_wallet(boss, balance=0); wallet.account = account; wallet.save(update_fields=['account'])
            staff = make_console_user(['wallet:view', 'wallet:recharge'])
            staff_account = get_or_create_account_for_legacy_user(staff); link_legacy_relations(staff, staff_account)
            staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
            service = make_service(name='隔离充值下单', price=5000)
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
            env = {**os.environ, 'KOOK_ENABLED': 'false', 'WECOM_DISPATCH_ENABLED': 'false',
                   'DJANGO_SETTINGS_MODULE': 'config.native_recharge_realtime_test_settings', 'XA_ISOLATED_MEDIA_ROOT': temp}
            server = subprocess.Popen([sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', str(port), 'config.asgi:application'], cwd=settings.BASE_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            native = None
            try:
                for _ in range(150):
                    if server.poll() is not None: self.fail('isolated ASGI exited')
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=.2): break
                    except OSError: time.sleep(.1)
                else: self.fail('isolated ASGI did not start')
                repo = Path(__file__).resolve().parents[4]
                ready = str(Path(temp) / 'ready')
                confirmation_ready = str(Path(temp) / 'confirmation-ready')
                native = subprocess.Popen(['node', str(repo / 'scripts/native-recharge-wallet-realtime.cjs')], cwd=repo, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                native.stdin.write(json.dumps({'base': f'http://127.0.0.1:{port}', 'token': issue_account_tokens(account)[0], 'bossId': account.pk, 'ready': ready, 'serviceId': service.pk, 'confirmationReady': confirmation_ready})); native.stdin.close(); native.stdin = None
                def wait_for(signal):
                    deadline = time.monotonic() + 35
                    while not Path(signal).exists() and time.monotonic() < deadline and native.poll() is None: time.sleep(.1)
                    if not Path(signal).exists():
                        if native.poll() is None: native.terminate()
                        out, err = native.communicate(timeout=10); self.fail(out + err)
                wait_for(ready)
                result = subprocess.run(['node', str(repo / 'scripts/admin-recharge-wallet-live.cjs')],
                    input=json.dumps({'api': f'http://127.0.0.1:{port}', 'token': issue_account_tokens(staff_account)[0],
                        'username': account.username, 'image': base64.b64encode(screenshot().read()).decode('ascii')}),
                    cwd=repo, text=True, encoding='utf-8', capture_output=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                wait_for(confirmation_ready)
                admin = APIClient(); admin.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
                second = admin.post('/api/admin/wallets/recharge/', {'account_id': account.pk, 'amount': 10000,
                    'gift_amount': 1000, 'trade_no': 'ISOLATED-SECOND-RECHARGE',
                    'client_request_id': 'native-second-recharge', 'proof_image': screenshot()}, format='multipart')
                self.assertEqual(second.data['code'], 0, second.data)
                out, err = native.communicate(timeout=30)
                self.assertEqual(native.returncode, 0, out + err)
                self.assertEqual(Wallet.objects.get(pk=wallet.pk).balance, 17000)
                from orders.models import Order
                order = Order.objects.get(customer_account=account)
                self.assertEqual((order.status, order.amount), ('PENDING', 5000))
            finally:
                for process in (native, server):
                    if process and process.poll() is None:
                        process.terminate()
                        try: process.wait(timeout=10)
                        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
