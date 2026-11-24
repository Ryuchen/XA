"""A paid staff order reaches only its buyer's current WebSocket."""
from unittest.mock import patch
from uuid import uuid4

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import override_settings
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.models import ClubAccount, LegacyAccountMap
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from console.grab_config import save_grab_window
from engagement.models import ClubOperationConfig, PointFundingLot
from orders.models import Order
from orders.tests.factories import make_console_user, make_service, make_user
from site_messages.models import Message
from wallet.models import Transaction
from wallet.services import get_wallet


@override_settings(
    GRAB_SINGLE_WINDOW_ENABLED=True, GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True,
    GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, LEGACY_FORCE_AUTH_COMPAT=False,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
)
class DispatchMessageRealtimeTest(APITransactionTestCase):
    dispatch_url = '/api/admin/orders/dispatch/'
    host = 'sl.esports.club'

    def setUp(self):
        buyer = make_user()
        self.buyer = ClubAccount.objects.create_account(username=buyer.username, account_type='BOSS',
                                                        business_cycle=4)
        LegacyAccountMap.objects.create(legacy_user_id=buyer.pk, account=self.buyer, legacy_role=buyer.role)
        wallet = get_wallet(account=self.buyer, user=buyer)
        wallet.balance = 20000
        wallet.save(update_fields=['balance'])
        PointFundingLot.objects.create(account=self.buyer, source_key='staff-message-principal',
                                       kind='PREPAID', remaining=20000)
        self.stranger = get_or_create_account_for_legacy_user(make_user())
        staff = make_console_user(['order:view', 'order:dispatch'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account
        staff.admin_membership.save(update_fields=['account'])
        self.staff = APIClient(HTTP_HOST='admin.esports.club',
                               HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        tokens = [issue_account_tokens(account)[0] for account in (self.buyer, self.stranger)]
        self.reader = APIClient(HTTP_HOST=self.host, HTTP_AUTHORIZATION='Bearer ' + tokens[0])
        self.sockets = [
            WebsocketCommunicator(application, f'/ws/orders/?token={token}',
                                  headers=[(b'host', self.host.encode())]) for token in tokens
        ]
        ClubOperationConfig.objects.get_or_create(pk=1)
        save_grab_window(30, 0, lambda *args: None)
        self.payload = {'customer_id': self.buyer.pk, 'service_id': make_service(price=10000).pk,
                        'client_request_id': uuid4().hex}

    def dispatch(self):
        return self.staff.post(self.dispatch_url, self.payload, format='json').data

    def assert_saved_message(self, order_id):
        order = Order.objects.get(pk=order_id)
        message = Message.objects.get(related_order_id=order_id, title='客服已为你下单')
        self.assertEqual((order.business_cycle, message.recipient_business_cycle), (4, 4))
        self.assertEqual(message.recipient_account_id, self.buyer.pk)
        listed = self.reader.get('/api/messages/', {'type': 'ORDER'}).data['data']
        self.assertEqual(listed['unread'], 1)
        self.assertEqual([row['id'] for row in listed['messages']], [message.pk])

    async def assert_no_message_event(self, socket):
        while not await socket.receive_nothing(timeout=0.1):
            self.assertNotEqual((await socket.receive_json_from(timeout=1))['type'], 'site_message_update')

    def test_paid_dispatch_invalidates_only_the_buyer_and_replay_is_silent(self):
        async def verify():
            buyer, stranger = self.sockets
            self.assertTrue((await buyer.connect())[0])
            self.assertTrue((await stranger.connect())[0])
            try:
                placed = await database_sync_to_async(self.dispatch)()
                self.assertEqual(placed['code'], 0, placed)
                await database_sync_to_async(self.assert_saved_message)(placed['data']['id'])
                for _ in range(4):
                    event = await buyer.receive_json_from(timeout=1)
                    if event['type'] == 'site_message_update':
                        self.assertEqual(event['data'], {})
                        break
                else:
                    self.fail('老板未收到站内信刷新')
                self.assertTrue(await stranger.receive_nothing(timeout=0.1))
                replay = await database_sync_to_async(self.dispatch)()
                self.assertEqual(replay['fundResult'], placed)
                await self.assert_no_message_event(buyer)
                await database_sync_to_async(self.assert_saved_message)(placed['data']['id'])
            finally:
                await buyer.disconnect()
                await stranger.disconnect()

        async_to_sync(verify)()
        self.assertEqual(Transaction.objects.filter(tx_type='PAY').count(), 1)

    def test_rolled_back_dispatch_does_not_save_or_notify(self):
        async def verify():
            buyer = self.sockets[0]
            self.assertTrue((await buyer.connect())[0])
            try:
                with patch('console.views.fund_success', side_effect=RuntimeError('回执写入中断')):
                    with self.assertRaisesMessage(RuntimeError, '回执写入中断'):
                        await database_sync_to_async(self.dispatch)()
                self.assertTrue(await buyer.receive_nothing(timeout=0.1))
            finally:
                await buyer.disconnect()

        async_to_sync(verify)()
        self.assertFalse(Order.objects.exists())
        self.assertFalse(Message.objects.exists())
        self.assertFalse(Transaction.objects.exists())
        self.assertEqual(get_wallet(account=self.buyer).balance, 20000)
