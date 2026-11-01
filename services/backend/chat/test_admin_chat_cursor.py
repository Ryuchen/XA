"""Actual staff API cursor paging over real chat messages."""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from chat.models import ChatMessage
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class AdminChatCursorTest(TestCase):
    def setUp(self):
        boss, other = make_user(), make_user()
        staff = make_console_user(['chat:view', 'chat:reply'])
        self.boss, self.other, self.staff = (get_or_create_account_for_legacy_user(user) for user in (boss, other, staff))
        for user, account in ((boss, self.boss), (other, self.other), (staff, self.staff)):
            link_legacy_relations(user, account)
        staff.admin_membership.account = self.staff
        staff.admin_membership.save(update_fields=['account'])
        self.boss_client, self.other_client, self.staff_client = APIClient(), APIClient(), APIClient()
        for client, account in ((self.boss_client, self.boss), (self.other_client, self.other), (self.staff_client, self.staff)):
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])

    def test_cursor_history_is_complete_and_legacy_clients_remain_compatible(self):
        ids = []
        for index in range(105):
            result = self.boss_client.post('/api/chat/send/', {'content': f'真实咨询 {index}'}, format='json').data
            self.assertEqual(result['code'], 0, result)
            ids.append(result['data']['id'])
        self.other_client.post('/api/chat/send/', {'content': '其他会话'}, format='json')
        session = self.boss_client.get('/api/chat/session/').data['data']['session']['id']
        other_session = self.other_client.get('/api/chat/session/').data['data']['session']['id']
        other_id = ChatMessage.objects.get(session_id=other_session).pk
        # Imported or corrected timestamps may not follow insertion IDs. Ties
        # also need a stable ordering so no message disappears between pages.
        backdated = timezone.now() - timedelta(days=10)
        ChatMessage.objects.filter(pk=ids[-1]).update(created_at=backdated)
        tie = timezone.now() - timedelta(days=1)
        ChatMessage.objects.filter(pk__in=ids[10:13]).update(created_at=tie)
        expected = list(ChatMessage.objects.filter(session_id=session).order_by('created_at', 'id').values_list('id', flat=True))
        url = f'/api/admin/chat-sessions/{session}/messages/'
        legacy = self.staff_client.get(url).data
        self.assertEqual([row['id'] for row in legacy['data']['list']], expected)
        self.assertEqual(legacy['data']['total'], 105)
        newest = self.staff_client.get(url, {'cursor': '1'}).data
        self.assertEqual(newest['code'], 0)
        self.assertEqual(len(newest['data']['list']), 50)
        self.assertEqual(newest['data']['total'], 105)
        newest_ids = [row['id'] for row in newest['data']['list']]
        # A backdated high ID isn't in this page and must stay unread. A
        # message from a different session must remain untouched as well.
        read = self.staff_client.post(f'/api/admin/chat-sessions/{session}/read/',
                                      {'message_ids': newest_ids + [other_id]}, format='json').data
        self.assertEqual(read['code'], 0, read)
        self.assertEqual(read['data']['unread_support'], 55)
        self.assertFalse(ChatMessage.objects.get(pk=ids[-1]).is_read)
        self.assertFalse(ChatMessage.objects.get(pk=other_id).is_read)
        chunks = [[row['id'] for row in newest['data']['list']]]
        while newest['data']['has_more']:
            cursor = newest['data']['next_before_id']
            self.assertEqual(cursor, chunks[0][0])
            newest = self.staff_client.get(url, {'cursor': '1', 'before_id': cursor}).data
            self.assertEqual(newest['code'], 0, newest)
            chunks.insert(0, [row['id'] for row in newest['data']['list']])
        self.assertEqual([item for chunk in chunks for item in chunk], expected)
        self.assertIsNone(newest['data']['next_before_id'])
        self.assertEqual(self.staff_client.get(url, {'cursor': '1', 'before_id': other_id}).status_code, 400)
        self.assertEqual(self.staff_client.get(url, {'cursor': '1', 'before_id': 'invalid'}).status_code, 400)
