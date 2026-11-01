"""Opt-in browser verification of staff drafts against isolated real chat APIs."""
import json
import os
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from chat.models import ChatMessage
from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership
from orders.tests.factories import make_console_user


@skipUnless(os.environ.get('XA_ADMIN_CHAT_DRAFT_BROWSER') == '1', 'Requires an isolated browser')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class StaffDraftMobileHTTPTest(LiveServerTestCase):
    def test_customer_drafts_remain_separate_when_switching_sessions(self):
        bosses = [ClubAccount.objects.create_account(username=f'qa-draft-boss-{i}', account_type='BOSS') for i in (1, 2)]
        staff_user = make_console_user(['chat:view', 'chat:reply'])
        staff = get_or_create_account_for_legacy_user(staff_user)
        AdminMembership.objects.filter(user=staff_user).update(account=staff)
        session_ids = []
        for index, boss in enumerate(bosses, 1):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss)[0])
            sent = client.post('/api/chat/send/', {'content': f'隔离咨询 {index}'}, format='json')
            self.assertEqual(sent.data['code'], 0, sent.data)
            session_ids.append(client.get('/api/chat/session/').data['data']['session']['id'])
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/admin-chat-drafts-mobile-live.cjs')],
            input=json.dumps({'api': self.live_server_url, 'token': issue_account_tokens(staff)[0],
                              'names': [boss.username for boss in bosses]}),
            text=True, encoding='utf-8', capture_output=True, timeout=80, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(ChatMessage.objects.filter(session_id=session_ids[0], is_from_support=True,
                                                   content='第一位老板的回复').exists())
        self.assertFalse(ChatMessage.objects.filter(session_id=session_ids[1], is_from_support=True).exists())
        self.assertIn('PASS real admin drafts at 375px', result.stdout)
