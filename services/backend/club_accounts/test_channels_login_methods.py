"""Real WebSocket authentication for the current unified login methods."""
from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings

from config.asgi import application
from stores.context import resolve_store, store_context

from .models import ClubAccount, OtpCredential, WechatIdentity
from .tokens import issue_account_tokens


@override_settings(
    LOGIN_OTP_REQUIRED=True,
    LEGACY_FORCE_AUTH_COMPAT=False,
    CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}},
)
class ChannelLoginMethodTests(TransactionTestCase):
    def setUp(self):
        self.store = resolve_store()
        with store_context(self.store):
            self.account = ClubAccount.objects.create_account(
                username='socket-password-sms-provider', account_type='PROVIDER',
                is_phone_verified=True,
            )

    def connect_and_ping(self, token):
        async def check():
            socket = WebsocketCommunicator(application, '/ws/orders/?token=' + token)
            try:
                connected, _ = await socket.connect()
                self.assertTrue(connected)
                await socket.send_json_to({'type': 'ping'})
                self.assertEqual(await socket.receive_json_from(), {'type': 'pong'})
            finally:
                await socket.disconnect()
        async_to_sync(check)()

    def assert_revoked(self, token):
        async def check():
            socket = WebsocketCommunicator(application, '/ws/orders/?token=' + token)
            try:
                connected, _ = await socket.connect()
                if connected:
                    await socket.send_json_to({'type': 'ping'})
                    self.assertEqual(await socket.receive_output(), {'type': 'websocket.close', 'code': 4401})
                else:
                    self.assertFalse(connected)
            finally:
                await socket.disconnect()
        async_to_sync(check)()

    def test_password_sms_socket_keeps_verified_login_and_rechecks_phone(self):
        with store_context(self.store):
            token, _ = issue_account_tokens(self.account, auth_method='password_sms')
        self.connect_and_ping(token)
        with store_context(self.store):
            ClubAccount.objects.filter(pk=self.account.pk).update(is_phone_verified=False)
        self.assert_revoked(token)

    def test_password_otp_socket_rechecks_authenticator_binding(self):
        from .otp import encrypt_secret
        with store_context(self.store):
            credential = OtpCredential.objects.create(
                account=self.account, secret_ciphertext=encrypt_secret('JBSWY3DPEHPK3PXP'),
            )
            token, _ = issue_account_tokens(self.account, otp_verified=True, auth_method='password_otp')
        self.connect_and_ping(token)
        with store_context(self.store):
            credential.delete()
        self.assert_revoked(token)

    def test_wechat_socket_keeps_channel_identity_and_rechecks_binding(self):
        with store_context(self.store):
            identity = WechatIdentity.objects.create(
                account=self.account, channel='OFFICIAL_H5', appid='wx-socket-test',
                openid='original-socket-openid',
            )
            ClubAccount.objects.filter(pk=self.account.pk).update(is_openid_bound=True)
            self.account.refresh_from_db()
            token, _ = issue_account_tokens(
                self.account, auth_method='wechat', wechat_identity=identity,
            )
        self.connect_and_ping(token)
        with store_context(self.store):
            WechatIdentity.objects.filter(pk=identity.pk).update(openid='changed-socket-openid')
        self.assert_revoked(token)
