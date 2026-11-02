"""An open mobile admin drawer follows genuine team evidence events."""
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
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from orders.tests.test_release_flow import screenshot
from wallet.models import ProviderReport


@skipUnless(os.environ.get('XA_ADMIN_PROGRESS_REALTIME') == '1', 'Requires isolated ASGI and Redis')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class AdminTeamProgressRealtimeTest(TransactionTestCase):
    def test_open_drawer_refreshes_when_both_providers_submit_evidence(self):
        self.assertEqual(connection.settings_dict['NAME'], os.environ.get('XA_ASGI_NETWORK_TEST_DB'))
        with tempfile.TemporaryDirectory(prefix='xa-admin-team-live-') as temp, override_settings(MEDIA_ROOT=temp):
            boss = make_user()
            providers = [make_provider(display_name=name, deposit_mode='EARNINGS', pass_tier='BLACK',
                         pass_expires_at=timezone.now() + timedelta(days=1)) for name in ('陪玩甲', '陪玩乙', '陪玩替补')]
            users = [boss, *providers]
            accounts = [get_or_create_account_for_legacy_user(user) for user in users]
            for user, account in zip(users, accounts):
                link_legacy_relations(user, account)
            service = make_service(name='实时双陪验收', price=10000, billing_provider_count=2)
            for user, account in zip(providers, accounts[1:]):
                user.escort_profile.account = account
                user.escort_profile.save(update_fields=['account'])
                user.escort_profile.service_items.add(service)
            make_wallet(boss, balance=50000)
            staff = make_console_user(['order:view'])
            staff_account = get_or_create_account_for_legacy_user(staff)
            link_legacy_relations(staff, staff_account)
            staff.admin_membership.account = staff_account
            staff.admin_membership.save(update_fields=['account'])
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0)); port = listener.getsockname()[1]
            base = f'http://127.0.0.1:{port}'
            env = {**os.environ, 'KOOK_ENABLED': 'false', 'WECOM_DISPATCH_ENABLED': 'false',
                   'DJANGO_SETTINGS_MODULE': 'config.team_progress_realtime_test_settings'}
            server = subprocess.Popen([sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', str(port), 'config.asgi:application'], cwd=settings.BASE_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            browser = native = None
            try:
                for _ in range(150):
                    if server.poll() is not None: self.fail('ASGI server exited')
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=.2): break
                    except OSError: time.sleep(.1)
                else: self.fail('ASGI server did not start')
                repo = Path(__file__).resolve().parents[4]
                result = subprocess.run(['node', str(repo / 'scripts/native-checkout-double-status-live.cjs')], input=json.dumps({'base': base, 'bossToken': issue_account_tokens(accounts[0])[0], 'bossId': accounts[0].pk, 'providerTokens': [issue_account_tokens(a)[0] for a in accounts[1:]], 'providerIds': [a.pk for a in accounts[1:]], 'serviceId': service.pk}), cwd=repo, text=True, encoding='utf-8', capture_output=True, timeout=45)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                order = Order.objects.get(customer_account=accounts[0])
                paths = {name: str(Path(temp) / name) for name in ('ready', 'entrySeen', 'startedSeen', 'completionSeen')}
                browser = subprocess.Popen(['node', str(repo / 'scripts/admin-team-progress-realtime.cjs')], cwd=repo, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                browser.stdin.write(json.dumps({'base': base, 'token': issue_account_tokens(staff_account)[0], 'orderNo': order.order_no, **paths})); browser.stdin.close(); browser.stdin = None
                native_paths = {name: str(Path(temp) / ('native-' + name)) for name in ('ready', 'entrySeen', 'startedSeen', 'completionSeen')}
                native = subprocess.Popen(['node', str(repo / 'scripts/native-team-progress-realtime.cjs')], cwd=repo, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                native.stdin.write(json.dumps({'base': base, 'token': issue_account_tokens(accounts[0])[0], 'bossId': accounts[0].pk, 'orderId': order.pk, **native_paths})); native.stdin.close(); native.stdin = None
                def wait_for(name, process=browser, signals=paths):
                    deadline = time.monotonic() + 40
                    while not Path(signals[name]).exists() and time.monotonic() < deadline and process.poll() is None: time.sleep(.1)
                    if not Path(signals[name]).exists():
                        if process.poll() is None: process.terminate()
                        out, err = process.communicate(timeout=10)
                        self.fail(f'Browser did not reach {name}: {out}{err}')
                clients = []
                for account in (accounts[1], accounts[3]):
                    client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0]); clients.append(client)
                wait_for('ready')
                wait_for('ready', native, native_paths)
                for index, signal in ((0, 'entrySeen'), (1, 'startedSeen')):
                    response = clients[index].post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
                    self.assertEqual(response.data['code'], 0, response.data)
                    wait_for(signal)
                    wait_for(signal, native, native_paths)
                response = clients[0].post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                wait_for('completionSeen')
                wait_for('completionSeen', native, native_paths)
                response = clients[1].post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                order.refresh_from_db()
                self.assertEqual(order.status, 'COMPLETED')
                self.assertIsNone(order.settled_at)
                reports = ProviderReport.objects.filter(order=order)
                self.assertEqual(reports.count(), 2)
                self.assertEqual(set(reports.values_list('provider_account_id', flat=True)), {accounts[1].pk, accounts[3].pk})
                self.assertEqual(set(reports.values_list('status', flat=True)), {'DRAFT'})
                out, err = browser.communicate(timeout=30)
                self.assertEqual(browser.returncode, 0, out + err)
                out, err = native.communicate(timeout=30)
                self.assertEqual(native.returncode, 0, out + err)
            finally:
                for process in (browser, native, server):
                    if process and process.poll() is None:
                        process.terminate()
                        try: process.wait(timeout=10)
                        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
