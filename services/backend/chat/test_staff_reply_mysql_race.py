"""Concurrent staff retries use the existing MySQL session row lock."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from chat.models import ChatMessage, ChatSession
from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership
from orders.tests.factories import make_console_user


@skipUnless(connection.vendor == 'mysql', 'requires MySQL row locks')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class StaffReplyMySQLRaceTest(TransactionTestCase):
    def test_two_identical_replies_commit_once(self):
        boss = ClubAccount.objects.create_account(username='staff-reply-race-boss', account_type='BOSS')
        user = make_console_user(['chat:view', 'chat:reply'])
        staff = get_or_create_account_for_legacy_user(user)
        AdminMembership.objects.filter(user=user).update(account=staff)
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss)[0])
        session_id = client.get('/api/chat/session/').data['data']['session']['id']
        token = issue_account_tokens(staff)[0]
        gate = Barrier(2)

        def send():
            close_old_connections()
            try:
                client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                gate.wait(timeout=10)
                return client.post(f'/api/admin/chat-sessions/{session_id}/reply/',
                    {'content': '真实并发回复', 'client_message_id': 'staff-race-message-123'}, format='json').data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: send(), range(2)))
        self.assertTrue(all(result['code'] == 0 for result in results), results)
        self.assertEqual(results[0]['data']['id'], results[1]['data']['id'])
        self.assertEqual(ChatMessage.objects.filter(session_id=session_id, content='真实并发回复').count(), 1)
        self.assertEqual(ChatSession.objects.get(pk=session_id).unread_user, 1)
