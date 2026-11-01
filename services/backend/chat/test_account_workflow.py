"""Support chat works for a new business account without a legacy user."""
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase, APITransactionTestCase
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_user
from chat.models import ChatSession
from config.asgi import application


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class AccountSupportChatTest(APITestCase):
    def client_for(self, account):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        return client

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data['data']

    def test_new_boss_can_chat_and_staff_can_find_current_identity(self):
        boss = ClubAccount.objects.create_account(username='new-chat-boss',
            nickname='新老板小海', account_type=ClubAccount.AccountType.BOSS)
        staff_user = make_console_user(['chat:view', 'chat:reply'])
        staff = get_or_create_account_for_legacy_user(staff_user)
        staff_user.admin_membership.account = staff
        staff_user.admin_membership.save(update_fields=['account'])
        client = self.client_for(boss)
        admin = self.client_for(staff)
        session = self.ok(client.get('/api/chat/session/'))['session']
        self.ok(client.post('/api/chat/send/', {'content': '咨询套餐'}, format='json'))
        by_name = self.ok(admin.get('/api/admin/chat-sessions/', {'keyword': '新老板小海'}))
        self.assertEqual(by_name['total'], 1)
        item = by_name['list'][0]
        self.assertEqual(item['id'], session['id'])
        self.assertEqual(item['user_name'], '新老板小海')
        self.assertEqual(item['user_role_display'], '老板')
        self.assertEqual(item['unread_support'], 1)
        self.assertEqual(self.ok(admin.get('/api/admin/chat-sessions/', {'keyword': 'new-chat-boss'}))['total'], 1)
        self.ok(admin.post(f"/api/admin/chat-sessions/{session['id']}/reply/", {'content':'可以为您安排'}, format='json'))
        refreshed = self.ok(client.get('/api/chat/session/'))
        self.assertEqual([row['content'] for row in refreshed['messages']], ['咨询套餐', '可以为您安排'])
        self.assertEqual(refreshed['session']['unread_user'], 1)

        boss.nickname = '新昵称小海'
        boss.save(update_fields=['nickname'])
        self.assertEqual(self.ok(admin.get('/api/admin/chat-sessions/', {'keyword': '新昵称小海'}))['list'][0]['user_name'], '新昵称小海')

    def test_legacy_session_without_business_account_remains_searchable(self):
        legacy = make_user(nickname='旧老板昵称')
        session = ChatSession.objects.create(user=legacy)
        staff_user = make_console_user(['chat:view'])
        staff = get_or_create_account_for_legacy_user(staff_user)
        staff_user.admin_membership.account = staff
        staff_user.admin_membership.save(update_fields=['account'])
        data = self.ok(self.client_for(staff).get('/api/admin/chat-sessions/', {'keyword': '旧老板昵称'}))
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['list'][0]['id'], session.pk)
        self.assertEqual(data['list'][0]['user_name'], '旧老板昵称')

    def test_text_limit_matches_message_storage(self):
        boss = ClubAccount.objects.create_account(username='chat-long-message',
            account_type=ClubAccount.AccountType.BOSS)
        client = self.client_for(boss)
        self.assertEqual(self.ok(client.post('/api/chat/send/', {'content': '测' * 1000}, format='json'))['content'], '测' * 1000)
        result = client.post('/api/chat/send/', {'content': '测' * 1001}, format='json')
        self.assertEqual(result.data['code'], 400)
        self.assertEqual(len(self.ok(client.get('/api/chat/session/'))['messages']), 1)

    def test_boss_read_does_not_clear_reply_arriving_after_history_load(self):
        boss = ClubAccount.objects.create_account(username='chat-boss-read-race', account_type='BOSS')
        staff_user = make_console_user(['chat:view', 'chat:reply'])
        staff = get_or_create_account_for_legacy_user(staff_user)
        staff_user.admin_membership.account = staff
        staff_user.admin_membership.save(update_fields=['account'])
        client, admin = self.client_for(boss), self.client_for(staff)
        session_id = self.ok(client.get('/api/chat/session/'))['session']['id']
        url = f'/api/admin/chat-sessions/{session_id}/'
        first = self.ok(admin.post(url + 'reply/', {'content': '已展示'}, format='json'))
        loaded = self.ok(client.get('/api/chat/session/'))
        self.assertEqual(loaded['messages'][-1]['id'], first['id'])
        second = self.ok(admin.post(url + 'reply/', {'content': '随后到达'}, format='json'))
        self.ok(client.post('/api/chat/read/', {'through_id': first['id']}, format='json'))
        session = self.ok(client.get('/api/chat/session/'))
        self.assertEqual(session['session']['unread_user'], 1)
        self.assertFalse(next(row for row in session['messages'] if row['id'] == second['id'])['is_read'])
        self.assertEqual(self.ok(client.post('/api/chat/read/', {}, format='json'))['unread_user'], 0)

    def test_staff_read_does_not_clear_message_arriving_after_history_load(self):
        boss = ClubAccount.objects.create_account(username='chat-staff-read-race', account_type='BOSS')
        staff_user = make_console_user(['chat:view', 'chat:reply'])
        staff = get_or_create_account_for_legacy_user(staff_user)
        staff_user.admin_membership.account = staff
        staff_user.admin_membership.save(update_fields=['account'])
        client, admin = self.client_for(boss), self.client_for(staff)
        first = self.ok(client.post('/api/chat/send/', {'content': '已展示'}, format='json'))
        session_id = self.ok(client.get('/api/chat/session/'))['session']['id']
        url = f'/api/admin/chat-sessions/{session_id}/'
        loaded = self.ok(admin.get(url + 'messages/'))
        self.assertEqual(loaded['list'][-1]['id'], first['id'])
        second = self.ok(client.post('/api/chat/send/', {'content': '随后到达'}, format='json'))
        self.ok(admin.post(url + 'read/', {'through_id': first['id']}, format='json'))
        session = self.ok(admin.get('/api/admin/chat-sessions/', {'only_unread': 1}))
        self.assertEqual(session['total'], 1)
        self.assertEqual(session['list'][0]['unread_support'], 1)
        messages = self.ok(admin.get(url + 'messages/'))['list']
        self.assertFalse(next(row for row in messages if row['id'] == second['id'])['is_read'])
        self.assertEqual(self.ok(admin.post(url + 'read/', {}, format='json'))['unread_support'], 0)

    def test_staff_reply_keeps_later_unseen_customer_message_unread(self):
        boss = ClubAccount.objects.create_account(username='chat-reply-read-race', account_type='BOSS')
        staff_user = make_console_user(['chat:view', 'chat:reply'])
        staff = get_or_create_account_for_legacy_user(staff_user)
        staff_user.admin_membership.account = staff
        staff_user.admin_membership.save(update_fields=['account'])
        client, admin = self.client_for(boss), self.client_for(staff)
        first = self.ok(client.post('/api/chat/send/', {'content': '已展示'}, format='json'))
        session_id = self.ok(client.get('/api/chat/session/'))['session']['id']
        url = f'/api/admin/chat-sessions/{session_id}/'
        second = self.ok(client.post('/api/chat/send/', {'content': '尚未看到'}, format='json'))
        self.ok(admin.post(url + 'reply/', {'content': '回复已展示的内容', 'read_through_id': first['id']}, format='json'))
        data = self.ok(admin.get('/api/admin/chat-sessions/', {'only_unread': 1}))
        self.assertEqual(data['list'][0]['unread_support'], 1)
        messages = self.ok(admin.get(url + 'messages/'))['list']
        self.assertFalse(next(row for row in messages if row['id'] == second['id'])['is_read'])


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class AccountSupportChatRealtimeTest(APITransactionTestCase):
    def test_new_account_and_staff_receive_real_chat_events(self):
        boss = ClubAccount.objects.create_account(username='chat-realtime-new',
            account_type=ClubAccount.AccountType.BOSS)
        staff_user = make_console_user(['chat:view', 'chat:reply'])
        staff = get_or_create_account_for_legacy_user(staff_user)
        staff_user.admin_membership.account = staff
        staff_user.admin_membership.save(update_fields=['account'])
        tokens = [issue_account_tokens(account)[0] for account in (boss, staff)]
        clients = []
        for token in tokens:
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
            clients.append(client)

        async def verify():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={token}') for token in tokens]
            try:
                for socket in sockets:
                    self.assertTrue((await socket.connect())[0])
                response = await database_sync_to_async(clients[0].post)(
                    '/api/chat/send/', {'content': '咨询实际服务'}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                events = [await socket.receive_json_from(timeout=3) for socket in sockets]
                self.assertTrue(all(event['type'] == 'chat_message' for event in events), events)
                session_id = events[0]['data']['session_id']
                response = await database_sync_to_async(clients[1].post)(
                    f'/api/admin/chat-sessions/{session_id}/reply/', {'content': '已收到'}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                events = [await socket.receive_json_from(timeout=3) for socket in sockets]
                self.assertTrue(all(event['data']['message']['content'] == '已收到' for event in events), events)
            finally:
                for socket in sockets:
                    await socket.disconnect()

        async_to_sync(verify)()
