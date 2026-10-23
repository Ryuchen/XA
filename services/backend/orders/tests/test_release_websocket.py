from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from orders.tests.factories import make_provider, make_console_user
from orders.tests.factories import make_order, make_user


@override_settings(CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class ReleaseWebSocketTest(TransactionTestCase):
    def test_real_completed_order_notification_renders_deadline_before_delivery(self):
        user = make_provider()
        account = get_or_create_account_for_legacy_user(user)
        boss = make_user()
        boss_account = get_or_create_account_for_legacy_user(boss)
        order = make_order(boss, provider=user, customer_account=boss_account,
                           provider_account=account, status='COMPLETED',
                           completed_at=timezone.now())
        token, _ = issue_account_tokens(account)

        async def verify():
            from orders.notifier import _notify_order_update
            socket = WebsocketCommunicator(application, f'/ws/orders/?token={token}')
            self.assertTrue((await socket.connect())[0])
            try:
                await database_sync_to_async(_notify_order_update)(order)
                event = await socket.receive_json_from()
                self.assertEqual(event['type'], 'order_status_update')
                self.assertEqual(event['data']['id'], order.pk)
                self.assertIsInstance(event['data']['aftersale_deadline'], str)
                self.assertEqual(event['data']['status'], 'COMPLETED')
                await socket.send_json_to({'type': 'ping'})
                self.assertEqual(await socket.receive_json_from(), {'type': 'pong'})
            finally:
                await socket.disconnect()

        async_to_sync(verify)()

    def test_real_account_can_connect_receive_updates_and_ping_on_both_routes(self):
        account = get_or_create_account_for_legacy_user(make_provider())
        token, _ = issue_account_tokens(account)

        async def verify():
            for path in ('/ws/orders/', '/api/ws/orders/'):
                socket = WebsocketCommunicator(application, f'{path}?token={token}')
                connected, _ = await socket.connect()
                self.assertTrue(connected)
                try:
                    await socket.send_json_to({'type': 'ping'})
                    self.assertEqual(await socket.receive_json_from(), {'type': 'pong'})
                    await get_channel_layer().group_send(f'account_{account.pk}', {
                        'type': 'order_status_update', 'data': {'order_id': 123, 'status': 'COMPLETED'},
                    })
                    self.assertEqual(await socket.receive_json_from(), {
                        'type': 'order_status_update', 'data': {'order_id': 123, 'status': 'COMPLETED'},
                    })
                finally:
                    await socket.disconnect()

        async_to_sync(verify)()

    def test_anonymous_socket_is_rejected(self):
        async def verify():
            socket = WebsocketCommunicator(application, '/ws/orders/')
            connected, _ = await socket.connect()
            self.assertFalse(connected)
            await socket.disconnect()

        async_to_sync(verify)()

    def test_staff_order_broadcast_requires_order_view_permission(self):
        for permissions in (['order:view'], ['chat:view']):
            user = make_console_user(permissions)
            account = get_or_create_account_for_legacy_user(user)
            membership = user.admin_membership
            membership.account = account
            membership.save(update_fields=['account'])
            token, _ = issue_account_tokens(account)

            async def verify():
                socket = WebsocketCommunicator(application, f'/ws/orders/?token={token}')
                connected, _ = await socket.connect()
                self.assertTrue(connected)
                try:
                    await get_channel_layer().group_send('operators', {
                        'type': 'order_status_update', 'data': {'id': 123},
                    })
                    if 'order:view' in permissions:
                        self.assertEqual((await socket.receive_json_from())['data'], {'id': 123})
                    else:
                        self.assertTrue(await socket.receive_nothing(timeout=0.1))
                finally:
                    await socket.disconnect()

            async_to_sync(verify)()

    def test_chat_broadcasts_require_current_chat_permission_and_membership(self):
        from chat.models import ChatSession, ChatMessage
        for permissions in (['chat:view'], ['order:view']):
            user = make_console_user(permissions)
            account = get_or_create_account_for_legacy_user(user)
            membership = user.admin_membership
            membership.account = account
            membership.save(update_fields=['account'])
            owner = make_provider()
            session = ChatSession.objects.create(user=owner, account=get_or_create_account_for_legacy_user(owner))
            message = ChatMessage.objects.create(session=session, content='仅会话读者可见')
            token, _ = issue_account_tokens(account)

            async def verify():
                socket = WebsocketCommunicator(application, f'/ws/orders/?token={token}')
                self.assertTrue((await socket.connect())[0])
                try:
                    for event_type in ('chat_message', 'chat_session_update'):
                        event = {'type': event_type, 'data': {'session_id': session.pk, 'message': {'id': message.pk}}}
                        await get_channel_layer().group_send('operators', event)
                        if 'chat:view' in permissions:
                            self.assertEqual((await socket.receive_json_from())['data']['session_id'], session.pk)
                        else:
                            self.assertTrue(await socket.receive_nothing(timeout=0.1))
                    # A connection made before revocation must not retain access.
                    membership.is_active = False
                    await database_sync_to_async(membership.save)(update_fields=['is_active'])
                    for index, event_type in enumerate(('chat_message', 'chat_session_update')):
                        await get_channel_layer().group_send('operators', {
                            'type': event_type, 'data': {'session_id': session.pk, 'message': {'id': message.pk}},
                        })
                        if index == 0:
                            self.assertEqual(await socket.receive_output(), {'type': 'websocket.close', 'code': 4401})
                        else:
                            self.assertTrue(await socket.receive_nothing(timeout=0.1))
                finally:
                    await socket.disconnect()

            async_to_sync(verify)()

    def test_chat_images_get_recipient_scoped_capabilities_and_wrong_owner_is_blocked(self):
        from chat.models import ChatSession, ChatMessage
        from django.core import signing
        from common.private_media import SALT
        owner = make_provider()
        account = get_or_create_account_for_legacy_user(owner)
        other = get_or_create_account_for_legacy_user(make_provider())
        session = ChatSession.objects.create(user=owner, account=account)
        message = ChatMessage.objects.create(session=session, image='chat/ws.png', content_type='IMAGE')
        tokens = [issue_account_tokens(item)[0] for item in (account, other)]

        async def verify():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={token}') for token in tokens]
            try:
                for socket in sockets:
                    self.assertTrue((await socket.connect())[0])
                for item in (account, other):
                    await get_channel_layer().group_send(f'account_{item.pk}', {
                        'type': 'chat_message', 'data': {'session_id': session.pk, 'message': {'id': message.pk}},
                    })
                payload = (await sockets[0].receive_json_from())['data']['message']
                self.assertEqual(payload['id'], message.pk)
                ticket = payload['image_url'].strip('/').split('/')[-1]
                self.assertEqual(signing.loads(ticket, salt=SALT)['account'], account.pk)
                self.assertTrue(await sockets[1].receive_nothing(timeout=0.1))
            finally:
                for socket in sockets:
                    await socket.disconnect()

        async_to_sync(verify)()


@override_settings(
    LOGIN_OTP_REQUIRED=True,
    CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}},
)
class OtpReleaseWebSocketTest(TransactionTestCase):
    def enrolled_account(self, staff=False):
        from club_accounts.models import OtpCredential
        from club_accounts.otp import encrypt_secret
        user = make_console_user(['order:view']) if staff else make_provider()
        account = get_or_create_account_for_legacy_user(user)
        if staff:
            membership = user.admin_membership
            membership.account = account
            membership.save(update_fields=['account'])
        credential = OtpCredential.objects.create(
            account=account, secret_ciphertext=encrypt_secret('JBSWY3DPEHPK3PXP'),
        )
        token, _ = issue_account_tokens(account, otp_verified=True)
        return account, credential, token

    def test_enrolled_staff_and_provider_can_connect_and_keep_heartbeat(self):
        for staff in (False, True):
            account, _, token = self.enrolled_account(staff=staff)

            async def verify():
                for path in ('/ws/orders/', '/api/ws/orders/'):
                    socket = WebsocketCommunicator(application, f'{path}?token={token}')
                    connected, _ = await socket.connect()
                    self.assertTrue(connected)
                    try:
                        await socket.send_json_to({'type': 'ping'})
                        self.assertEqual(await socket.receive_json_from(), {'type': 'pong'})
                        await get_channel_layer().group_send(f'account_{account.pk}', {
                            'type': 'order_status_update', 'data': {'id': 123},
                        })
                        self.assertEqual((await socket.receive_json_from())['data'], {'id': 123})
                    finally:
                        await socket.disconnect()

            async_to_sync(verify)()

    def test_otp_version_change_closes_existing_socket_and_rejects_old_token(self):
        import uuid
        from club_accounts.models import OtpCredential
        _, credential, token = self.enrolled_account()

        async def verify():
            socket = WebsocketCommunicator(application, f'/ws/orders/?token={token}')
            self.assertTrue((await socket.connect())[0])
            try:
                await database_sync_to_async(
                    OtpCredential.objects.filter(pk=credential.pk).update,
                )(version=uuid.uuid4())
                await socket.send_json_to({'type': 'ping'})
                self.assertEqual(await socket.receive_output(), {'type': 'websocket.close', 'code': 4401})
            finally:
                await socket.disconnect()
            stale = WebsocketCommunicator(application, f'/ws/orders/?token={token}')
            self.assertFalse((await stale.connect())[0])
            await stale.disconnect()

        async_to_sync(verify)()

    def test_password_only_token_cannot_connect_when_otp_is_required(self):
        account, _, _ = self.enrolled_account()
        with override_settings(LOGIN_OTP_REQUIRED=False):
            token, _ = issue_account_tokens(account)

        async def verify():
            socket = WebsocketCommunicator(application, f'/ws/orders/?token={token}')
            self.assertFalse((await socket.connect())[0])
            await socket.disconnect()

        async_to_sync(verify)()

    def test_boss_socket_does_not_require_staff_otp_claims(self):
        from orders.tests.factories import make_user
        account = get_or_create_account_for_legacy_user(make_user())
        token, _ = issue_account_tokens(account)

        async def verify():
            socket = WebsocketCommunicator(application, f'/ws/orders/?token={token}')
            self.assertTrue((await socket.connect())[0])
            try:
                await socket.send_json_to({'type': 'ping'})
                self.assertEqual(await socket.receive_json_from(), {'type': 'pong'})
            finally:
                await socket.disconnect()

        async_to_sync(verify)()
