"""Workbench counts the same real, shared unread conversations as the queue."""
from django.test import override_settings
from rest_framework.test import APITestCase, APIClient

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class DashboardChatUnreadWorkflowTest(APITestCase):
    def client_for(self, account):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        return client

    def staff(self, permissions):
        user = make_console_user(permissions)
        account = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = account
        user.admin_membership.save(update_fields=['account'])
        return user, self.client_for(account)

    def ok(self, response):
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data['data']

    def test_shared_unread_conversations_match_paginated_queue_and_permission(self):
        _, first = self.staff(['dashboard:view', 'chat:view', 'chat:reply'])
        _, second = self.staff(['dashboard:view', 'chat:view', 'chat:reply'])
        _, limited = self.staff(['dashboard:view'])
        bosses = []
        sessions = []
        for number in range(22):
            account = ClubAccount.objects.create_account(username=f'isolated-chat-queue-{number}', account_type='BOSS')
            boss = self.client_for(account)
            session = self.ok(boss.get('/api/chat/session/'))['session']
            message = self.ok(boss.post('/api/chat/send/', {'content': f'隔离咨询 {number}'}, format='json'))
            bosses.append(boss)
            sessions.append((session['id'], message['id']))
        second_message = self.ok(bosses[0].post('/api/chat/send/', {'content': '补充咨询'}, format='json'))

        def count_and_queue(client, expected):
            stats = self.ok(client.get('/api/admin/dashboard/stats'))
            first_page = self.ok(client.get('/api/admin/chat-sessions/', {'only_unread': 1, 'page_size': 20}))
            second_page = self.ok(client.get('/api/admin/chat-sessions/', {'only_unread': 1, 'page_size': 20, 'page': 2}))
            self.assertEqual(stats['unread_chat_session_total'], expected)
            self.assertEqual(first_page['total'], expected)
            self.assertEqual(second_page['total'], expected)
            self.assertEqual(len(first_page['list']) + len(second_page['list']), expected)

        count_and_queue(first, 22)
        count_and_queue(second, 22)
        session_id, first_message = sessions[0]
        self.ok(first.post(f'/api/admin/chat-sessions/{session_id}/read/', {'message_ids': [first_message]}, format='json'))
        count_and_queue(second, 22)  # A second unread message keeps the conversation in the queue.
        self.ok(second.post(f'/api/admin/chat-sessions/{session_id}/read/', {'message_ids': [second_message['id']]}, format='json'))
        count_and_queue(first, 21)
        self.ok(bosses[0].post('/api/chat/send/', {'content': '再次咨询'}, format='json'))
        count_and_queue(second, 22)
        self.assertIsNone(self.ok(limited.get('/api/admin/dashboard/stats'))['unread_chat_session_total'])
        self.assertEqual(limited.get('/api/admin/chat-sessions/', {'only_unread': 1}).status_code, 403)
