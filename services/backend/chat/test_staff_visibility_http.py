"""Two staff accounts exercise shared unread state through the real chat API."""
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
class StaffChatVisibilityHTTPTest(LiveServerTestCase):
    def test_hidden_tab_keeps_shared_message_unread_until_staff_returns(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        boss = ClubAccount.objects.create_account(username='visibility-chat-boss', account_type='BOSS')
        staff_accounts = []
        for _ in range(2):
            user = make_console_user(['chat:view', 'chat:reply'])
            account = get_or_create_account_for_legacy_user(user)
            user.admin_membership.account = account
            user.admin_membership.save(update_fields=['account'])
            staff_accounts.append(account)
        tokens = [issue_account_tokens(account)[0] for account in [boss, *staff_accounts]]
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
        session = client.get('/api/chat/session/').data['data']['session']
        first = APIClient(); first.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[1])
        second = APIClient(); second.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[2])
        self.assertEqual(first.post(f"/api/admin/chat-sessions/{session['id']}/claim/", {'expected_version': 0}, format='json').data['code'], 0)
        self.assertEqual(second.get(f"/api/admin/chat-sessions/{session['id']}/messages/").status_code, 403)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/scenarios/admin-chat-visibility-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'sessionId': session['id'],
                              'bossToken': tokens[0], 'firstStaffToken': tokens[1], 'secondStaffToken': tokens[2]}),
            cwd=repo, text=True, encoding='utf-8', capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn('PASS real HTTP staff visibility', result.stdout)
