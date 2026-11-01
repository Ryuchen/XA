"""Render provider navigation against isolated, real message APIs."""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.db import connection
from django.test import LiveServerTestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership
from orders.tests.factories import make_console_user, make_provider
from site_messages.models import Message


@skipUnless(os.environ.get('XA_PROVIDER_UNREAD_H5_DIR'), 'Requires isolated H5 build and browser')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CORS_ALLOW_ALL_ORIGINS=True, CELERY_TASK_ALWAYS_EAGER=False)
class ProviderUnreadH5HTTPTest(LiveServerTestCase):
    host = '127.0.0.1'
    port = 19178

    def test_navigation_counts_follow_real_read_and_account(self):
        first, second = make_provider(display_name='隔离陪玩甲'), make_provider(display_name='隔离陪玩乙')
        staff = make_console_user(['message:view', 'message:push'])
        accounts = []
        for user in (first, second, staff):
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            accounts.append(account)
        AdminMembership.objects.filter(user=staff).update(account=accounts[2])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[2])[0])
        for account, title in ((accounts[0], '隔离消息一'), (accounts[0], '隔离消息二'), (accounts[1], '隔离消息三')):
            result = client.post('/api/admin/messages/push/', {'audience': 'CUSTOM', 'recipient_account_ids': [account.pk], 'title': title}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
        repo = Path(__file__).resolve().parents[3]
        inputs = {'root': os.environ['XA_PROVIDER_UNREAD_H5_DIR'], 'tokens': [issue_account_tokens(a)[0] for a in accounts[:2]],
                  'ids': [str(a.pk) for a in accounts[:2]]}
        result = subprocess.run(['node', str(repo / 'scripts/provider-unread-h5-live.cjs')],
            input=json.dumps(inputs), text=True, encoding='utf-8', capture_output=True, timeout=100, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(Message.objects.filter(recipient_account=accounts[0], is_read=False).count(), 0)
        self.assertEqual(Message.objects.filter(recipient_account=accounts[1], is_read=False).count(), 1)


@skipUnless(os.environ.get('XA_PROVIDER_UNREAD_SOCKET_H5') == '1', 'Requires isolated H5, database and ASGI server')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CORS_ALLOW_ALL_ORIGINS=True, CELERY_TASK_ALWAYS_EAGER=False)
class ProviderUnreadSocketH5Test(TransactionTestCase):
    def test_real_socket_refreshes_navigation_count(self):
        database = os.environ.get('XA_PROVIDER_BUNDLE_TEST_DB')
        self.assertTrue(database and connection.settings_dict['NAME'] == database, 'isolated file-backed database required')
        first, second = make_provider(display_name='隔离陪玩甲'), make_provider(display_name='隔离陪玩乙')
        staff = make_console_user(['message:view', 'message:push'])
        accounts = []
        for user in (first, second, staff):
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            accounts.append(account)
        AdminMembership.objects.filter(user=staff).update(account=accounts[2])
        client = APIClient()
        staff_token = issue_account_tokens(accounts[2])[0]
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        for account, title in ((accounts[0], '隔离消息一'), (accounts[0], '隔离消息二'), (accounts[1], '隔离消息三')):
            result = client.post('/api/admin/messages/push/', {'audience': 'CUSTOM', 'recipient_account_ids': [account.pk], 'title': title}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
        repo = Path(__file__).resolve().parents[3]
        env = {**os.environ, 'KOOK_ENABLED': 'false', 'WECOM_DISPATCH_ENABLED': 'false', 'WECHAT_APPID': '', 'WECHAT_SECRET': ''}
        server = subprocess.Popen([sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', '19178', 'config.asgi:application'],
            cwd=settings.BASE_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            for _ in range(150):
                if server.poll() is not None:
                    self.fail('isolated ASGI server exited')
                try:
                    with socket.create_connection(('127.0.0.1', 19178), timeout=.2):
                        break
                except OSError:
                    time.sleep(.1)
            else:
                self.fail('isolated ASGI server did not start')
            inputs = {'root': os.environ['XA_PROVIDER_UNREAD_H5_DIR'], 'tokens': [issue_account_tokens(a)[0] for a in accounts[:2]],
                      'ids': [str(a.pk) for a in accounts[:2]], 'staffToken': staff_token, 'firstAccount': accounts[0].pk}
            result = subprocess.run(['node', str(repo / 'scripts/provider-unread-h5-live.cjs')],
                input=json.dumps(inputs), text=True, encoding='utf-8', capture_output=True, timeout=100, cwd=repo)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(Message.objects.filter(recipient_account=accounts[0], is_read=False).count(), 0)
            self.assertEqual(Message.objects.filter(recipient_account=accounts[1], is_read=False).count(), 1)
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
