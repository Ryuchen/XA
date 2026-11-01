"""Expired orders notify only their owner after the durable message commits."""
from datetime import timedelta
from uuid import uuid4

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import override_settings
from django.db import transaction
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from orders.models import Order
from orders.tasks import auto_cancel_pending_order
from orders.tests.factories import make_service, make_user, make_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class TimeoutMessageRealtimeTest(APITransactionTestCase):
    def setUp(self):
        self.boss = make_user()
        self.other = make_user()
        self.accounts = {user.pk: get_or_create_account_for_legacy_user(user) for user in (self.boss, self.other)}
        self.tokens = {pk: issue_account_tokens(account)[0] for pk, account in self.accounts.items()}
        self.service = make_service(price=10000)
        make_wallet(self.boss, balance=20000)

    def login(self, user):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + self.tokens[user.pk])

    def prepare(self):
        self.login(self.boss)
        response = self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'dispatch_route': 'STAFF', 'client_request_id': str(uuid4()),
        }, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order_id = response.data['data']['id']
        Order.objects.filter(pk=order_id).update(auto_cancel_at=timezone.now() - timedelta(seconds=1))
        return order_id

    def cancel_and_fetch(self, order_id):
        self.assertEqual(auto_cancel_pending_order.run(order_id), 'cancelled')
        self.login(self.boss)
        data = self.client.get('/api/messages/', {'type': 'ORDER'}).data['data']
        row = next(item for item in data['messages'] if item['related_order_id'] == order_id)
        self.assertEqual(data['unread'], 1)
        self.assertIn('已退回兴安币余额', row['preview'])
        return row['id']

    def read(self, message_id):
        self.login(self.boss)
        self.assertEqual(self.client.get(f'/api/messages/{message_id}/').data['code'], 0)
        self.assertEqual(self.client.get('/api/messages/unread-count/').data['data']['unread'], 0)

    def rolled_back_message(self):
        from site_messages.utils import create_message
        try:
            with transaction.atomic():
                create_message(recipient_id=self.boss.pk, recipient_account_id=self.accounts[self.boss.pk].pk,
                               title='回滚测试消息')
                raise ValueError('回滚测试')
        except ValueError:
            pass
        self.login(self.boss)
        self.assertEqual(self.client.get('/api/messages/unread-count/').data['data']['unread'], 0)

    def test_timeout_message_creation_and_read_notify_only_owner(self):
        order_id = self.prepare()

        async def verify():
            owner = WebsocketCommunicator(application, f'/ws/orders/?token={self.tokens[self.boss.pk]}')
            other = WebsocketCommunicator(application, f'/ws/orders/?token={self.tokens[self.other.pk]}')
            self.assertTrue((await owner.connect())[0])
            self.assertTrue((await other.connect())[0])
            try:
                message_id = await database_sync_to_async(self.cancel_and_fetch)(order_id)
                received = []
                while 'site_message_update' not in received:
                    event = await owner.receive_json_from(timeout=2)
                    received.append(event['type'])
                    if event['type'] == 'site_message_update':
                        self.assertEqual(event['data'], {})
                self.assertTrue(await other.receive_nothing(timeout=0.1))
                # Drain any order/wallet event before testing the read update.
                while not await owner.receive_nothing(timeout=0.1):
                    await owner.receive_json_from(timeout=1)
                await database_sync_to_async(self.read)(message_id)
                event = await owner.receive_json_from(timeout=2)
                self.assertEqual(event, {'type': 'site_message_update', 'data': {}})
                self.assertTrue(await other.receive_nothing(timeout=0.1))
            finally:
                await owner.disconnect()
                await other.disconnect()

        async_to_sync(verify)()

    def test_rolled_back_message_does_not_notify_or_appear(self):
        async def verify():
            owner = WebsocketCommunicator(application, f'/ws/orders/?token={self.tokens[self.boss.pk]}')
            self.assertTrue((await owner.connect())[0])
            try:
                await database_sync_to_async(self.rolled_back_message)()
                self.assertTrue(await owner.receive_nothing(timeout=0.1))
            finally:
                await owner.disconnect()
        async_to_sync(verify)()
