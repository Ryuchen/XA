"""Provider H5 refreshes an open invitation after a separate worker cancels it."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderApplication
from orders.tasks import auto_cancel_pending_order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from wallet.models import Wallet


@skipUnless(os.environ.get('XA_PROVIDER_TIMEOUT_REALTIME_H5_DIR'), 'Requires isolated H5, ASGI, Redis and Celery')
class ProviderTimeoutLivePageTest(TransactionTestCase):
    def test_open_team_invitation_updates_when_worker_expires_order(self):
        self.assertEqual(connection.settings_dict['NAME'], os.environ.get('XA_ASGI_NETWORK_TEST_DB'))
        users = [make_user(), make_provider(display_name='隔离报名人'), make_provider(display_name='隔离队友')]
        staff = make_console_user(['dashboard:view', 'order:view'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        link_legacy_relations(staff, staff_account)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in users]
        for user, account in zip(users, accounts):
            link_legacy_relations(user, account)
        service = make_service(name='实时双陪套餐', price=10000, billing_provider_count=2)
        for user, account in zip(users[1:], accounts[1:]):
            user.escort_profile.account = account
            user.escort_profile.pass_tier = 'BLACK'
            user.escort_profile.pass_expires_at = timezone.now() + timedelta(days=1)
            user.escort_profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])
            user.escort_profile.service_items.add(service)
        make_wallet(users[0], balance=50000)
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
        created = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC',
            'dispatch_mode': 'BOSS_SELECT', 'client_request_id': 'timeout-open-h5-isolated'}, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order_id = created.data['data']['id']
        Order.objects.filter(pk=order_id).update(pool_entered_at=timezone.now() - timedelta(hours=1))
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[1])
        invited = client.post(f'/api/orders/orders/{order_id}/grab/', {'team_size': 2, 'partner_id': accounts[2].pk}, format='json')
        self.assertEqual(invited.data['code'], 0, invited.data)
        application_id = OrderApplication.objects.get(order_id=order_id, applicant=accounts[1]).pk
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); port = listener.getsockname()[1]
        env = {**os.environ, 'KOOK_ENABLED': 'false', 'WECOM_DISPATCH_ENABLED': 'false'}
        server = subprocess.Popen([sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', str(port), 'config.asgi:application'],
            cwd=settings.BASE_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        browser = native = admin = worker = None
        try:
            for _ in range(150):
                if server.poll() is not None:
                    self.fail('isolated ASGI server exited')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2):
                        break
                except OSError:
                    time.sleep(.1)
            else:
                self.fail('isolated ASGI server did not start')
            with tempfile.TemporaryDirectory(prefix='xa-timeout-live-page-') as temp:
                ready = str(Path(temp) / 'ready')
                native_ready = str(Path(temp) / 'native-ready')
                admin_ready = str(Path(temp) / 'admin-ready')
                repo = Path(__file__).resolve().parents[4]
                browser = subprocess.Popen(['node', str(repo / 'scripts/provider-timeout-open-375-live.cjs')],
                    cwd=repo, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                browser.stdin.write(json.dumps({'base': f'http://127.0.0.1:{port}', 'ready': ready,
                    'root': os.environ['XA_PROVIDER_TIMEOUT_REALTIME_H5_DIR'], 'token': tokens[2],
                    'providerId': str(accounts[2].pk), 'orderId': order_id, 'applicationId': application_id}))
                browser.stdin.close(); browser.stdin = None
                native = subprocess.Popen(['node', str(repo / 'scripts/native-timeout-open-live.cjs')],
                    cwd=repo, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                native.stdin.write(json.dumps({'base': f'http://127.0.0.1:{port}', 'ready': native_ready,
                    'token': tokens[0], 'bossId': accounts[0].pk, 'orderId': order_id}))
                native.stdin.close(); native.stdin = None
                admin = subprocess.Popen(['node', str(repo / 'scripts/admin-timeout-dashboard-live.cjs')],
                    cwd=repo, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                admin.stdin.write(json.dumps({'base': f'http://127.0.0.1:{port}', 'ready': admin_ready,
                    'token': issue_account_tokens(staff_account)[0]}))
                admin.stdin.close(); admin.stdin = None
                ready_paths = [Path(ready), Path(native_ready), Path(admin_ready)]
                deadline = time.monotonic() + 50
                while not all(path.exists() for path in ready_paths) and time.monotonic() < deadline and all(process.poll() is None for process in (browser, native, admin)):
                    time.sleep(.1)
                if not all(path.exists() for path in ready_paths):
                    outputs = []
                    for process in (browser, native, admin):
                        if process.poll() is None:
                            process.terminate()
                        stdout, stderr = process.communicate(timeout=5)
                        outputs.append(stdout + stderr)
                    self.fail('Clients did not reach their open pages: ' + '\n'.join(outputs))
                Order.objects.filter(pk=order_id).update(auto_cancel_at=timezone.now() - timedelta(seconds=1))
                worker = subprocess.Popen([sys.executable, '-m', 'celery', '-A', 'config', 'worker', '-P', 'solo', '-l', 'WARNING'],
                    cwd=settings.BASE_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                auto_cancel_pending_order.delay(order_id)
                stdout, stderr = browser.communicate(timeout=35)
                self.assertEqual(browser.returncode, 0, stdout + stderr)
                stdout, stderr = native.communicate(timeout=35)
                self.assertEqual(native.returncode, 0, stdout + stderr)
                stdout, stderr = admin.communicate(timeout=35)
                self.assertEqual(admin.returncode, 0, stdout + stderr)
                self.assertEqual(Wallet.objects.get(user=users[0]).balance, 50000)
        finally:
            for process in (browser, native, admin, worker, server):
                if process and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait(timeout=10)
