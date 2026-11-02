"""Older customer-service messages are reachable through the real API and page."""
import json
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from chat.models import ChatMessage
from orders.tests.factories import make_console_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ChatHistoryFrontendHTTPTest(LiveServerTestCase):
    def test_older_history_and_new_message_with_real_chat_api(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required for native controller')
        boss = ClubAccount.objects.create_account(username='history-boss', account_type='BOSS')
        other = ClubAccount.objects.create_account(username='history-other', account_type='BOSS')
        staff_user = make_console_user(['chat:view', 'chat:reply'])
        staff = get_or_create_account_for_legacy_user(staff_user)
        staff_user.admin_membership.account = staff
        staff_user.admin_membership.save(update_fields=['account'])
        token, refresh = issue_account_tokens(boss)
        other_token, other_refresh = issue_account_tokens(other)
        admin_token, _ = issue_account_tokens(staff)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        session = client.get('/api/chat/session/').data['data']['session']
        admin = APIClient()
        admin.credentials(HTTP_AUTHORIZATION='Bearer ' + admin_token)
        first_id = None
        for index in range(105):
            response = admin.post(f"/api/admin/chat-sessions/{session['id']}/reply/", {'content': f'历史消息 {index:03}'}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            if first_id is None:
                first_id = response.data['data']['id']
        # Historical imports may share a timestamp; the ID tiebreaker must
        # still make all three pages complete and stable.
        ChatMessage.objects.filter(session_id=session['id']).update(created_at=timezone.now())
        other_client = APIClient()
        other_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(other)[0])
        self.assertEqual(other_client.get('/api/chat/session/', {'before_id': first_id}).status_code, 400)
        self.assertEqual(client.get('/api/chat/session/', {'before_id': '-1'}).status_code, 400)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-chat-history-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'sessionId': session['id'], 'adminToken': admin_token,
                'session': {'token': token, 'refresh': refresh, 'user': {'id': str(boss.pk), 'role': 'customer'}},
                'otherSession': {'token': other_token, 'refresh': other_refresh, 'user': {'id': str(other.pk), 'role': 'customer'}}}),
            cwd=repo, text=True, encoding='utf-8', capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn('real HTTP chat history', result.stdout)
