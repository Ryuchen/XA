"""Native page performs bounded read against real isolated HTTP endpoints."""
import json
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ChatReadFrontendHTTPTest(LiveServerTestCase):
    def test_native_marks_only_history_actually_loaded(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required for native controller')
        boss = ClubAccount.objects.create_account(username='real-chat-page-boss', account_type='BOSS')
        staff_user = make_console_user(['chat:view', 'chat:reply'])
        staff = get_or_create_account_for_legacy_user(staff_user)
        staff_user.admin_membership.account = staff
        staff_user.admin_membership.save(update_fields=['account'])
        token, refresh = issue_account_tokens(boss)
        admin_token, _ = issue_account_tokens(staff)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        response = client.get('/api/chat/session/')
        self.assertEqual(response.data['code'], 0, response.data)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-chat-read-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'sessionId': response.data['data']['session']['id'],
                'adminToken': admin_token, 'session': {'token': token, 'refresh': refresh,
                    'user': {'id': str(boss.pk), 'role': 'customer'}}}),
            cwd=repo, text=True, encoding='utf-8', capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn('real HTTP bounded chat read', result.stdout)

    def test_staff_marks_only_history_actually_loaded(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required for admin controller')
        boss = ClubAccount.objects.create_account(username='real-chat-staff-page-boss', account_type='BOSS')
        staff_user = make_console_user(['chat:view', 'chat:reply'])
        staff = get_or_create_account_for_legacy_user(staff_user)
        staff_user.admin_membership.account = staff
        staff_user.admin_membership.save(update_fields=['account'])
        token, _ = issue_account_tokens(boss)
        admin_token, _ = issue_account_tokens(staff)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        response = client.get('/api/chat/session/')
        self.assertEqual(response.data['code'], 0, response.data)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/admin-chat-read-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'sessionId': response.data['data']['session']['id'],
                              'adminToken': admin_token, 'bossToken': token}),
            cwd=repo, text=True, encoding='utf-8', capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn('real HTTP staff bounded chat read', result.stdout)
