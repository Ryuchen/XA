"""Authorization changes must affect sockets that were already connected."""
from datetime import timedelta
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.db import InterfaceError
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from orders.tests.factories import make_console_user, make_provider


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, CHANNEL_LAYERS={
    'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'},
})
class LiveAuthorizationTest(TransactionTestCase):
    def staff(self):
        user = make_console_user(['order:view', 'chat:view'])
        account = get_or_create_account_for_legacy_user(user)
        membership = user.admin_membership
        membership.account = account
        membership.save(update_fields=['account'])
        return account, membership

    async def connect(self, token):
        socket = WebsocketCommunicator(application, f'/ws/orders/?token={token}',
                                  headers=[(b'host', b'sl.esports.club')])
        self.assertTrue((await socket.connect())[0])
        return socket

    async def order(self, group):
        # 公共陪玩组只承载空刷新信号；个人订单数据走本人账号组。
        data = {} if group == 'providers' else {'id': 42, 'status': 'PENDING'}
        await get_channel_layer().group_send(group, {
            'type': 'order_status_update', 'data': data,
        })

    def test_staff_permission_read_failure_blocks_subscription_and_order_delivery(self):
        account, _ = self.staff()
        token, _ = issue_account_tokens(account)

        async def verify():
            group = f'account_{account.pk}'
            with patch('orders.consumers.get_account_permissions', side_effect=InterfaceError('role read failed')):
                socket = WebsocketCommunicator(application, f'/ws/orders/?token={token}',
                                               headers=[(b'host', b'sl.esports.club')])
                self.assertEqual(await socket.connect(), (False, 1013))
                self.assertFalse(get_channel_layer().groups.get(group))
                self.assertFalse(get_channel_layer().groups.get('operators'))
                await socket.disconnect()

            socket = await self.connect(token)
            try:
                with patch('orders.consumers.get_account_permissions', side_effect=InterfaceError('role read failed')):
                    await self.order(group)
                    self.assertEqual(await socket.receive_output(), {'type': 'websocket.close', 'code': 1013})
            finally:
                await socket.disconnect()

        async_to_sync(verify)()

    def test_disabled_provider_cannot_receive_on_an_existing_connection(self):
        account = get_or_create_account_for_legacy_user(make_provider())
        token, _ = issue_account_tokens(account)

        async def verify():
            socket = await self.connect(token)
            try:
                await self.order('providers')
                self.assertEqual((await socket.receive_json_from())['type'], 'order_status_update')
                await database_sync_to_async(ClubAccount.objects.filter(pk=account.pk).update)(can_login=False)
                await self.order('providers')
                self.assertEqual(await socket.receive_output(), {'type': 'websocket.close', 'code': 4401})
            finally:
                await socket.disconnect()

        async_to_sync(verify)()

    def test_disabled_account_is_closed_on_heartbeat_without_waiting_for_an_order(self):
        account = get_or_create_account_for_legacy_user(make_provider())
        token, _ = issue_account_tokens(account)

        async def verify():
            socket = await self.connect(token)
            try:
                await database_sync_to_async(ClubAccount.objects.filter(pk=account.pk).update)(is_active=False)
                await socket.send_json_to({'type': 'ping'})
                self.assertEqual(await socket.receive_output(), {'type': 'websocket.close', 'code': 4401})
            finally:
                await socket.disconnect()

        async_to_sync(verify)()

    def test_disabling_staff_membership_revokes_cached_order_access(self):
        account, membership = self.staff()
        token, _ = issue_account_tokens(account)

        async def verify():
            socket = await self.connect(token)
            try:
                # Warm the reverse relation that the former consumer cached.
                await self.order('operators')
                self.assertEqual((await socket.receive_json_from())['type'], 'order_status_update')
                membership.is_active = False
                await database_sync_to_async(membership.save)(update_fields=['is_active'])
                await self.order('operators')
                self.assertEqual(await socket.receive_output(), {'type': 'websocket.close', 'code': 4401})
            finally:
                await socket.disconnect()

        async_to_sync(verify)()

    def test_removing_only_order_permission_stops_order_data_and_keeps_heartbeat(self):
        account, membership = self.staff()
        role = membership.roles.get()
        token, _ = issue_account_tokens(account)

        async def verify():
            socket = await self.connect(token)
            try:
                await self.order('operators')
                await socket.receive_json_from()
                role.permissions = ['chat:view']
                await database_sync_to_async(role.save)(update_fields=['permissions'])
                await self.order('operators')
                self.assertTrue(await socket.receive_nothing(timeout=0.1))
                await socket.send_json_to({'type': 'ping'})
                self.assertEqual(await socket.receive_json_from(), {'type': 'pong'})
            finally:
                await socket.disconnect()

        async_to_sync(verify)()

    def test_expiry_is_enforced_on_heartbeat_after_successful_handshake(self):
        account = get_or_create_account_for_legacy_user(make_provider())
        token, _ = issue_account_tokens(account)

        async def verify():
            socket = await self.connect(token)
            try:
                with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(days=1)):
                    await socket.send_json_to({'type': 'ping'})
                    self.assertEqual(await socket.receive_output(), {'type': 'websocket.close', 'code': 4401})
            finally:
                await socket.disconnect()

        async_to_sync(verify)()

    def test_expiry_blocks_push_even_without_client_heartbeat(self):
        account = get_or_create_account_for_legacy_user(make_provider())
        token, _ = issue_account_tokens(account)

        async def verify():
            socket = await self.connect(token)
            try:
                with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(days=1)):
                    await self.order('providers')
                    self.assertEqual(await socket.receive_output(), {'type': 'websocket.close', 'code': 4401})
            finally:
                await socket.disconnect()

        async_to_sync(verify)()

    def test_identity_change_revokes_http_refresh_and_existing_socket(self):
        account = get_or_create_account_for_legacy_user(make_provider())
        token, refresh = issue_account_tokens(account)

        async def verify():
            socket = await self.connect(token)
            try:
                await database_sync_to_async(ClubAccount.objects.filter(pk=account.pk).update)(account_type='BOSS')
                await self.order('providers')
                self.assertEqual(await socket.receive_output(), {'type': 'websocket.close', 'code': 4401})
            finally:
                await socket.disconnect()

        async_to_sync(verify)()
        client = APIClient()
        self.assertEqual(client.get('/api/users/me/', HTTP_AUTHORIZATION='Bearer ' + token).status_code, 401)
        self.assertEqual(client.post('/api/users/refresh/', {'refresh': refresh}, format='json').status_code, 401)

    def test_changed_identity_cannot_reconnect_with_old_token(self):
        account = get_or_create_account_for_legacy_user(make_provider())
        token, _ = issue_account_tokens(account)
        from club_accounts.models import ClubAccount
        ClubAccount.objects.filter(pk=account.pk).update(account_type='BOSS')

        async def verify():
            socket = WebsocketCommunicator(application, f'/ws/orders/?token={token}',
                                  headers=[(b'host', b'sl.esports.club')])
            self.assertFalse((await socket.connect())[0])
            await socket.disconnect()

        async_to_sync(verify)()

    def test_disabled_membership_cannot_refresh_console_credentials(self):
        account, membership = self.staff()
        token, refresh = issue_account_tokens(account)
        membership.is_active = False
        membership.save(update_fields=['is_active'])
        client = APIClient()
        self.assertEqual(client.get('/api/admin/auth/profile', HTTP_AUTHORIZATION='Bearer ' + token).status_code, 401)
        self.assertEqual(client.post('/api/admin/auth/refresh', {'refresh': refresh}, format='json').status_code, 401)

    def test_non_console_account_cannot_use_console_refresh(self):
        account = get_or_create_account_for_legacy_user(make_provider())
        _, refresh = issue_account_tokens(account)
        self.assertEqual(APIClient().post('/api/admin/auth/refresh', {'refresh': refresh}, format='json').status_code, 401)

    def test_signed_malformed_identity_fails_without_server_error(self):
        account = get_or_create_account_for_legacy_user(make_provider())
        token, _ = issue_account_tokens(account)
        for identity in ('not-an-id', None, [], True):
            custom = AccessToken(token)
            custom['account_id'] = identity
            client = APIClient()
            self.assertEqual(client.get('/api/users/me/', HTTP_AUTHORIZATION='Bearer ' + str(custom)).status_code, 401)

    def test_non_object_or_binary_input_does_not_crash_consumer(self):
        account = get_or_create_account_for_legacy_user(make_provider())
        token, _ = issue_account_tokens(account)

        async def verify():
            socket = await self.connect(token)
            try:
                for data in ('[]', 'null', '42', 'bad-json'):
                    await socket.send_to(text_data=data)
                await socket.send_json_to({'type': 'ping'})
                self.assertEqual(await socket.receive_json_from(), {'type': 'pong'})
                await socket.send_to(bytes_data=b'\x00')
                self.assertEqual(await socket.receive_output(), {'type': 'websocket.close', 'code': 1003})
            finally:
                await socket.disconnect()

        async_to_sync(verify)()
