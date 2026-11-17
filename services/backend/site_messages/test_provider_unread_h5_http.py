"""Provider navigation unread counts follow real read state and account isolation."""
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from console.models import AdminMembership
from orders.tests.factories import make_console_user, make_provider
from site_messages.models import Message
from stores.context import resolve_store, store_context


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CORS_ALLOW_ALL_ORIGINS=True, CELERY_TASK_ALWAYS_EAGER=False)
class ProviderUnreadH5HTTPTest(LiveServerTestCase):
    host = '127.0.0.1'
    port = 19178

    def test_navigation_counts_follow_real_read_and_account(self):
        self.enterContext(store_context(resolve_store()))
        first, second = make_provider(display_name='隔离陪玩甲'), make_provider(display_name='隔离陪玩乙')
        staff = make_console_user(['message:view', 'message:push'])
        accounts = []
        for user in (first, second, staff):
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            accounts.append(account)
        AdminMembership.objects.filter(user=staff).update(account=accounts[2])
        client = APIClient(HTTP_HOST='admin.esports.club')
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[2])[0])
        for account, title in ((accounts[0], '隔离消息一'), (accounts[0], '隔离消息二'), (accounts[1], '隔离消息三')):
            result = client.post('/api/admin/messages/push/', {'recipient_account_ids': [account.pk], 'title': title}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
        readers = []
        for account in accounts[:2]:
            reader = APIClient(HTTP_HOST='sl.esports.club')
            reader.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            readers.append(reader)
        self.assertEqual(readers[0].get('/api/messages/unread-count/').data['data']['unread'], 2)
        self.assertEqual(readers[1].get('/api/messages/unread-count/').data['data']['unread'], 1)
        listed = readers[0].get('/api/messages/').data['data']['messages']
        first_id = next(row['id'] for row in listed if row['title'] == '隔离消息一')
        self.assertEqual(readers[0].get(f'/api/messages/{first_id}/').data['code'], 0)
        self.assertEqual(readers[0].get('/api/messages/unread-count/').data['data']['unread'], 1)
        self.assertEqual(readers[0].post('/api/messages/read-all/', {}, format='json').data['code'], 0)
        self.assertEqual(readers[0].get('/api/messages/unread-count/').data['data']['unread'], 0)
        self.assertEqual(readers[1].get('/api/messages/unread-count/').data['data']['unread'], 1)
        self.assertEqual(Message.objects.filter(recipient_account=accounts[0], is_read=False).count(), 0)
        self.assertEqual(Message.objects.filter(recipient_account=accounts[1], is_read=False).count(), 1)


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CORS_ALLOW_ALL_ORIGINS=True, CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class ProviderUnreadSocketH5Test(APITransactionTestCase):
    def test_real_socket_refreshes_navigation_count(self):
        self.enterContext(store_context(resolve_store()))
        first, second = make_provider(display_name='隔离陪玩甲'), make_provider(display_name='隔离陪玩乙')
        staff = make_console_user(['message:view', 'message:push'])
        accounts = []
        for user in (first, second, staff):
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            accounts.append(account)
        AdminMembership.objects.filter(user=staff).update(account=accounts[2])
        client = APIClient(HTTP_HOST='admin.esports.club')
        staff_token = issue_account_tokens(accounts[2])[0]
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        for account, title in ((accounts[0], '隔离消息一'), (accounts[0], '隔离消息二'), (accounts[1], '隔离消息三')):
            result = client.post('/api/admin/messages/push/', {'recipient_account_ids': [account.pk], 'title': title}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
        tokens = [issue_account_tokens(a)[0] for a in accounts[:2]]
        readers = []
        for token in tokens:
            reader = APIClient(HTTP_HOST='sl.esports.club')
            reader.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
            readers.append(reader)

        async def verify():
            socket = WebsocketCommunicator(application, f'/ws/orders/?token={tokens[0]}',
                                           headers=[(b'host', b'sl.esports.club')])
            self.assertTrue((await socket.connect())[0])
            try:
                pushed = await database_sync_to_async(client.post)(
                    '/api/admin/messages/push/', {'recipient_account_ids': [accounts[0].pk], 'title': '实时隔离消息'}, format='json')
                self.assertEqual(pushed.data['code'], 0, pushed.data)
                received = []
                while 'site_message_update' not in received:
                    event = await socket.receive_json_from(timeout=3)
                    received.append(event['type'])
                self.assertEqual((await database_sync_to_async(readers[0].get)('/api/messages/unread-count/')).data['data']['unread'], 3)
                listed = await database_sync_to_async(readers[0].get)('/api/messages/')
                first_id = next(row['id'] for row in listed.data['data']['messages'] if row['title'] == '隔离消息一')
                self.assertEqual((await database_sync_to_async(readers[0].get)(f'/api/messages/{first_id}/')).data['code'], 0)
                self.assertEqual((await database_sync_to_async(readers[0].get)('/api/messages/unread-count/')).data['data']['unread'], 2)
                self.assertEqual((await database_sync_to_async(readers[0].post)('/api/messages/read-all/', {}, format='json')).data['code'], 0)
                self.assertEqual((await database_sync_to_async(readers[0].get)('/api/messages/unread-count/')).data['data']['unread'], 0)
                self.assertEqual((await database_sync_to_async(readers[1].get)('/api/messages/unread-count/')).data['data']['unread'], 1)
            finally:
                await socket.disconnect()
        async_to_sync(verify)()
        self.assertEqual(Message.objects.filter(recipient_account=accounts[0], is_read=False).count(), 0)
        self.assertEqual(Message.objects.filter(recipient_account=accounts[1], is_read=False).count(), 1)