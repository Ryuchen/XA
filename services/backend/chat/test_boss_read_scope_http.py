"""Native chat only marks messages actually loaded, even with backdated history."""
import json
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from chat.models import ChatMessage
from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class BossChatReadScopeHTTPTest(LiveServerTestCase):
    def test_backdated_high_id_stays_unread_until_loaded(self):
        self.run_scope(51, True)

    def test_loaded_history_over_200_messages_is_acknowledged_in_batches(self):
        self.run_scope(205, False)

    def run_scope(self, count, backdate):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        boss = ClubAccount.objects.create_account(username='read-scope-boss', account_type='BOSS')
        user = make_console_user(['chat:view', 'chat:reply'])
        staff = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = staff
        user.admin_membership.save(update_fields=['account'])
        token, refresh = issue_account_tokens(boss)
        customer = APIClient()
        customer.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        session = customer.get('/api/chat/session/').data['data']['session']
        admin = APIClient()
        admin.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
        ids = []
        for number in range(count):
            result = admin.post(f"/api/admin/chat-sessions/{session['id']}/reply/", {'content': f'隔离客服回复 {number}'}, format='json').data
            self.assertEqual(result['code'], 0, result)
            ids.append(result['data']['id'])
        backdated = ids[-2] if backdate else ids[0]
        if backdate:
            ChatMessage.objects.filter(pk=backdated).update(created_at=timezone.now() - timedelta(days=1))
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-chat-read-scope-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'backdated': backdated, 'total': count,
                'session': {'token': token, 'refresh': refresh, 'user': {'id': str(boss.pk), 'role': 'customer'}}}),
            cwd=repo, text=True, encoding='utf-8', capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class BossChatReadScopeTest(TestCase):
    def test_ids_are_bounded_to_own_session_and_legacy_through_still_works(self):
        boss = ClubAccount.objects.create_account(username='read-scope-owner', account_type='BOSS')
        other = ClubAccount.objects.create_account(username='read-scope-other', account_type='BOSS')
        own = APIClient(); own.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss)[0])
        stranger = APIClient(); stranger.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(other)[0])
        own_session = own.get('/api/chat/session/').data['data']['session']['id']
        other_session = stranger.get('/api/chat/session/').data['data']['session']['id']
        own_message = ChatMessage.objects.create(session_id=own_session, is_from_support=True, content='本人的客服回复')
        other_message = ChatMessage.objects.create(session_id=other_session, is_from_support=True, content='另一账号的客服回复')
        result = own.post('/api/chat/read/', {'message_ids': [other_message.pk]}, format='json')
        self.assertEqual(result.data['code'], 0)
        other_message.refresh_from_db(); own_message.refresh_from_db()
        self.assertFalse(other_message.is_read)
        self.assertFalse(own_message.is_read)
        self.assertEqual(own.post('/api/chat/read/', {'message_ids': list(range(1, 202))}, format='json').status_code, 400)
        self.assertEqual(own.post('/api/chat/read/', {'message_ids': [own_message.pk], 'through_id': own_message.pk}, format='json').status_code, 400)
        self.assertEqual(own.post('/api/chat/read/', {'through_id': own_message.pk}, format='json').data['code'], 0)
        own_message.refresh_from_db()
        self.assertTrue(own_message.is_read)
