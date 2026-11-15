from unittest import skipUnless
import asyncio

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderProvider, OrderRoomMessage
from orders.state_machine import transition
from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user
from orders.tests.test_order_rooms import OrderRoomTests
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], ORIGINAL_ATTRIBUTION_ENABLED=False)
class RoomMessagesTests(APITestCase):
    def setUp(self):
        OrderRoomTests.setUp(self)
        self.order = OrderRoomTests.order(self)
        OrderRoomTests.assign(self, self.order, [(self.provider, self.provider_account)])
        self.url = f'/api/orders/orders/{self.order.pk}/room/messages/'

    def staff_client(self, permissions):
        user = make_console_user(permissions)
        account = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = account; user.admin_membership.save(update_fields=['account'])
        return APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(account)[0])

    def test_members_can_read_and_send_idempotently_with_staff_permissions(self):
        value = {'content': '准备开始', 'client_request_id': 'message-one'}
        first = self.boss_client.post(self.url, value, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(self.boss_client.post(self.url, value, format='json').data['data']['id'], first.data['data']['id'])
        self.assertEqual(self.boss_client.post(self.url, {**value, 'content': '变更'}, format='json').status_code, 409)
        self.assertEqual(self.provider_client.get(self.url).data['data']['list'][0]['content'], '准备开始')
        self.assertEqual(self.other_client.get(self.url).status_code, 404)
        self.assertEqual(self.other_client.post(self.url, value, format='json').status_code, 404)
        viewer = self.staff_client(['order:view'])
        editor = self.staff_client(['order:view', 'support:edit'])
        self.assertEqual(viewer.get(self.url).status_code, 200)
        self.assertEqual(viewer.post(self.url, value, format='json').status_code, 403)
        self.assertEqual(editor.post(self.url, value, format='json').status_code, 200)
        self.assertEqual(OrderRoomMessage.objects.count(), 2)
        self.assertEqual(self.boss_client.get(self.url)['Cache-Control'], 'no-store')

    def test_transferred_provider_sees_only_own_messages_and_cannot_post(self):
        own = {'content': '原陪玩的记录', 'client_request_id': 'old-entry'}
        self.assertEqual(self.provider_client.post(self.url, own, format='json').status_code, 200)
        self.assertEqual(self.boss_client.post(self.url, {'content': '老板信息', 'client_request_id': 'boss-entry'}, format='json').status_code, 200)
        assignment = self.order.providers.get(provider_account=self.provider_account)
        assignment.released_at = timezone.now(); assignment.save(update_fields=['released_at'])
        OrderProvider.objects.create(order=self.order, provider=self.other, provider_account=self.other_account)
        Order.objects.filter(pk=self.order.pk).update(provider=self.other, provider_account=self.other_account)
        self.assertEqual(self.provider_client.post(self.url, own, format='json').status_code, 200)
        self.assertEqual(self.provider_client.post(self.url, {'content': '新内容', 'client_request_id': 'new-entry'}, format='json').status_code, 403)
        old = self.provider_client.get(self.url).data['data']['list']
        self.assertEqual([item['content'] for item in old], ['原陪玩的记录'])
        new = self.other_client.get(self.url).data['data']['list']
        self.assertEqual({item['content'] for item in new}, {'老板信息', '原陪玩的记录'})

    def test_invalid_and_finished_orders_never_create_messages(self):
        for payload in ({'content': ' ', 'client_request_id': 'a'}, {'content': 'x'*1001, 'client_request_id': 'a'},
                        {'content': 'ok', 'client_request_id': ''}, {'content': 'ok', 'client_request_id': 'a', 'author': 1}):
            self.assertEqual(self.boss_client.post(self.url, payload, format='json').status_code, 400)
        Order.objects.filter(pk=self.order.pk).update(status=Order.Status.CANCELLED)
        self.assertEqual(self.boss_client.post(self.url, {'content': '结束', 'client_request_id': 'a'}, format='json').status_code, 400)
        self.assertFalse(OrderRoomMessage.objects.exists())

    @override_settings(CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
    def test_message_notification_is_post_commit_and_rechecks_access(self):
        from orders.consumers import current_room_message_payload
        layer = get_channel_layer()
        boss_channel = async_to_sync(layer.new_channel)()
        provider_channel = async_to_sync(layer.new_channel)()
        async_to_sync(layer.group_add)(f'account_{self.boss_account.pk}', boss_channel)
        async_to_sync(layer.group_add)(f'account_{self.provider_account.pk}', provider_channel)
        with self.captureOnCommitCallbacks(execute=True):
            result = self.boss_client.post(self.url, {'content': '房间更新', 'client_request_id': 'notice'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        for channel in (boss_channel, provider_channel):
            event = async_to_sync(asyncio.wait_for)(layer.receive(channel), timeout=1)
            self.assertEqual(event, {'type': 'room_message_update', 'data': {'order_id': self.order.pk}})
        with self.captureOnCommitCallbacks(execute=True):
            replay = self.boss_client.post(self.url, {'content': '房间更新', 'client_request_id': 'notice'}, format='json')
        self.assertEqual(replay.status_code, 200)
        with self.assertRaises(asyncio.TimeoutError):
            async_to_sync(asyncio.wait_for)(layer.receive(provider_channel), timeout=0.05)
        self.assertEqual(current_room_message_payload.func(self.provider_account, {'order_id': self.order.pk}), {'order_id': self.order.pk})
        assignment = self.order.providers.get(provider_account=self.provider_account)
        assignment.released_at = timezone.now(); assignment.save(update_fields=['released_at'])
        self.assertIsNone(current_room_message_payload.func(self.provider_account, {'order_id': self.order.pk}))
        self.assertIsNone(current_room_message_payload.func(self.other_account, {'order_id': self.order.pk}))


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], ORIGINAL_ATTRIBUTION_ENABLED=False)
class RoomMessagesStoresTest(APITestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_room_identifiers_never_share_messages(self):
        rows = []
        for store, host in ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club')):
            with store_context(store):
                boss, provider = make_user(), make_provider(deposit_mode='EARNINGS')
                owner, player = [get_or_create_account_for_legacy_user(u) for u in (boss, provider)]
                order = make_order(boss, customer_account=owner, service=make_service(room_enabled=True), payment_status='PAID')
                def assign(row):
                    row.provider, row.provider_account = provider, player
                    OrderProvider.objects.create(order=row, provider=provider, provider_account=player)
                transition(order.pk, Order.Status.GRABBED, side_effect=assign, update_fields=['provider', 'provider_account'])
                client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(owner)[0])
                rows.append((store, client, order.pk, owner.pk))
        self.assertEqual(rows[0][2], rows[1][2])
        url = f'/api/orders/orders/{rows[0][2]}/room/messages/'
        layer = get_channel_layer()
        channels = []
        for store, _, _, owner_id in rows:
            with store_context(store):
                channel = async_to_sync(layer.new_channel)()
                async_to_sync(layer.group_add)(f'account_{owner_id}', channel)
                channels.append(channel)
        with store_context(rows[1][0]), self.captureOnCommitCallbacks(using='store_b', execute=True):
            self.assertEqual(rows[1][1].post(url, {'content': '兴安记录', 'client_request_id': 'same'}, format='json').status_code, 200)
        with store_context(rows[1][0]):
            event = async_to_sync(asyncio.wait_for)(layer.receive(channels[1]), timeout=1)
            self.assertEqual(event['type'], 'room_message_update')
        with store_context(rows[0][0]):
            with self.assertRaises(asyncio.TimeoutError):
                async_to_sync(asyncio.wait_for)(layer.receive(channels[0]), timeout=0.05)
        self.assertEqual(rows[0][1].get(url).data['data']['total'], 0)
        self.assertEqual(rows[1][1].get(url).data['data']['total'], 1)
