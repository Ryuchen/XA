"""Opt-in browser check: a real incoming message must not interrupt history review."""
import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_user


@skipUnless(os.environ.get('XA_ADMIN_CHAT_HISTORY_BROWSER') == '1', 'Requires an isolated browser')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class AdminChatHistoryScrollHTTPTest(LiveServerTestCase):
    def test_new_message_preserves_history_and_unread_until_viewed(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        boss = make_user()
        staff = make_console_user(['chat:view', 'chat:reply'])
        boss_account, staff_account = [get_or_create_account_for_legacy_user(user) for user in (boss, staff)]
        for user, account in ((boss, boss_account), (staff, staff_account)):
            link_legacy_relations(user, account)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        boss_token, staff_token = [issue_account_tokens(account)[0] for account in (boss_account, staff_account)]
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
        for number in range(275):
            response = client.post('/api/chat/send/', {'content': f'隔离历史消息 {number}'}, format='json')
            self.assertEqual(response.data['code'], 0)
        session_id = client.get('/api/chat/session/').data['data']['session']['id']
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/admin-chat-history-scroll-live.cjs')],
            input=json.dumps({'api': self.live_server_url, 'staffToken': staff_token, 'bossToken': boss_token,
                              'bossName': boss_account.nickname or boss_account.username, 'sessionId': session_id}),
            text=True, encoding='utf-8', capture_output=True, timeout=100, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
