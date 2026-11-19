"""Neutral admin credentials, real OTP, and token-owned two-database routing."""
import os
from unittest import skipUnless
import base64
from io import BytesIO
import secrets
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.core.files.base import ContentFile
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from club_accounts.models import ClubAccount, OtpCredential, OtpLoginChallenge
from club_accounts.otp import encrypt_secret, hotp
from club_accounts.tokens import issue_account_tokens
from console.models import AdminRole

from .context import current_store, resolve_store, store_context
from .models import StorePlatformGrant
from .tests_foundation import admin
from .tests_hosts import xinan_store


HOST = 'admin.esports.club'


def credential(account):
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')
    OtpCredential.objects.create(account=account, secret_ciphertext=encrypt_secret(secret), last_counter=-1)
    return secret


@override_settings(LOGIN_OTP_REQUIRED=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class NeutralAdminEntryTest(TestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first, self.second = resolve_store(), xinan_store()
        self.actors = {}
        for store, username in [(self.first, 'neutral-first'), (self.second, 'neutral-second')]:
            with store_context(store):
                account = admin(username, super_admin=True)
                account.nickname = store.name + '后台'
                account.phone = '13800001111'
                account.save(update_fields=['nickname', 'phone'])
                self.actors[store.slug] = account
        self.client = APIClient()
        self.client.defaults['HTTP_HOST'] = HOST
        self.codes = []
        sender = patch('users.password_login.send_verification_code',
                       side_effect=lambda phone, code: self.codes.append(code))
        sender.start()
        self.addCleanup(sender.stop)

    def login(self, username, **data):
        return self.client.post('/api/admin/auth/login', {'username': username,
            'password': 'test-password', **data}, format='json')

    def enroll(self, store):
        account = self.actors[store.slug]
        first = self.login(account.username)
        self.assertEqual(first.status_code, 200, first.data)
        sms = first.data['data']
        self.assertTrue(sms['requiresSms'])
        initial = self.login(account.username, smsChallenge=sms['smsChallenge'], smsCode=self.codes[-1]).data['data']
        auth = {'HTTP_AUTHORIZATION': 'Bearer ' + initial['token']}
        draft = self.client.post('/api/admin/auth/otp-setup', {}, format='json', **auth).data['data']
        self.assertTrue(draft['requiresOTPSetup'])
        code = hotp(draft['otpSecret'], int(timezone.now().timestamp()) // 30)
        setup = self.client.post('/api/admin/auth/otp-setup', {
            'otpChallenge': draft['otpChallenge'], 'otpCode': code,
        }, format='json', **auth).data['data']
        otp = self.login(account.username).data['data']
        self.assertEqual(otp['store_slug'], store.slug)
        self.assertNotEqual(len(otp['otpChallenge']), 36)
        completed = self.login(account.username, otpChallenge=otp['otpChallenge'], otpCode=setup['recoveryCodes'][0])
        self.assertEqual(completed.data['code'], 0, completed.data)
        self.assertEqual(completed.data['data']['store_slug'], store.slug)
        return {**completed.data['data'], 'recoveryCodes': setup['recoveryCodes'][1:]}, draft

    def auth_client(self, account, store):
        with store_context(store):
            if not OtpCredential.objects.filter(account=account).exists():
                credential(account)
            token, refresh = issue_account_tokens(account, otp_verified=True)
        client = APIClient()
        client.credentials(HTTP_HOST=HOST, HTTP_AUTHORIZATION='Bearer ' + token)
        return client, token, refresh

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_real_two_store_password_otp_enrollment_jwts_profile_and_refresh(self):
        for store in (self.first, self.second):
            data, _ = self.enroll(store)
            self.assertEqual(data['store']['slug'], store.slug)
            self.assertEqual(AccessToken(data['token'])['store_slug'], store.slug)
            self.assertEqual(RefreshToken(data['refresh'])['store_slug'], store.slug)
            client = APIClient()
            client.credentials(HTTP_HOST=HOST, HTTP_AUTHORIZATION='Bearer ' + data['token'])
            response = client.get('/api/admin/auth/profile')
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['data']['nickname'], store.name + '后台')
            self.assertEqual(response['X-Store-Slug'], store.slug)
            current = client.get('/api/stores/current/')
            self.assertEqual(current.data['data']['slug'], store.slug)
            response = self.client.post('/api/admin/auth/refresh', {'refresh': data['refresh']}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['data']['store_slug'], store.slug)
            self.assertEqual(AccessToken(response.data['data']['token'])['store_slug'], store.slug)
        self.assertIsNone(current_store())

    def test_public_login_never_uses_header_query_or_body_store_selectors(self):
        username = self.actors['xinan'].username
        for suffix, extra, headers in [('', {}, {'HTTP_X_STORE_SLUG': 'xinan'}),
                                       ('?store=xinan', {}, {}),
                                       ('', {'store_slug': 'xinan'}, {}),
                                       ('', {'database_alias': 'store_b'}, {})]:
            response = self.client.post('/api/admin/auth/login' + suffix,
                {'username': username, 'password': 'test-password', **extra}, format='json', **headers)
            self.assertEqual(response.status_code, 400, getattr(response, 'data', response.content))
        for store in (self.first, self.second):
            with store_context(store):
                self.assertEqual(OtpLoginChallenge.objects.count(), 0)

    def test_malformed_public_credentials_fail_without_a_store_or_otp_challenge(self):
        for data in ([], {'username': ['neutral-first'], 'password': 'test-password'},
                     {'username': 'neutral-first', 'password': {'value': 'test-password'}}):
            response = self.client.post('/api/admin/auth/login', data, format='json')
            self.assertEqual(response.status_code, 400)
        for store in (self.first, self.second):
            with store_context(store):
                self.assertEqual(OtpLoginChallenge.objects.count(), 0)

    def test_duplicate_credentials_require_the_explicit_username_namespace(self):
        for store in (self.first, self.second):
            with store_context(store):
                credential(admin('same-console-name'))
        response = self.login('same-console-name')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['errorCode'], 'ADMIN_STORE_AMBIGUOUS')
        for slug in ('shenglang', 'xinan'):
            response = self.login(slug + '/same-console-name')
            self.assertEqual(response.data['data']['store_slug'], slug)
            self.assertTrue(response.data['data']['requiresOTP'])

    def test_same_username_with_different_passwords_resolves_only_the_authenticated_store(self):
        for store in (self.first, self.second):
            with store_context(store):
                account = admin('same-name-different-password')
                credential(account)
                if store.slug == 'xinan':
                    account.set_password('different-strong-password')
                    account.save(update_fields=['password'])
        response = self.login('same-name-different-password', password='different-strong-password')
        self.assertEqual(response.data['data']['store_slug'], 'xinan')
        self.assertTrue(response.data['data']['requiresOTP'])
        self.assertEqual(self.login('same-name-different-password', password='invalid').status_code, 400)

    def test_signed_otp_challenge_cannot_be_rebound_replayed_or_tampered(self):
        secret = None
        for store in (self.first, self.second):
            with store_context(store):
                account = admin('duplicate-otp')
                if store.slug == 'xinan':
                    secret = credential(account)
        draft = self.login('xinan/duplicate-otp').data['data']
        code = hotp(secret, int(timezone.now().timestamp()) // 30)
        self.assertEqual(self.login('shenglang/duplicate-otp', otpChallenge=draft['otpChallenge'], otpCode=code).data['code'], 400)
        bad = draft['otpChallenge'] + 'tamper'
        self.assertEqual(self.login('xinan/duplicate-otp', otpChallenge=bad, otpCode=code).data['code'], 400)
        succeeded = self.login('xinan/duplicate-otp', otpChallenge=draft['otpChallenge'], otpCode=code)
        self.assertEqual(succeeded.data['code'], 0)
        self.assertEqual(succeeded.data['data']['store_slug'], 'xinan')
        self.assertEqual(self.login('xinan/duplicate-otp', otpChallenge=draft['otpChallenge'], otpCode=code).data['code'], 400)
        with store_context(self.first):
            self.assertEqual(OtpCredential.objects.count(), 0)

    def test_challenge_expiration_and_real_otp_recovery_remain_store_local(self):
        data, _ = self.enroll(self.second)
        account = self.actors['xinan']
        draft = self.login(account.username).data['data']
        response = self.login(account.username, otpChallenge=draft['otpChallenge'], otpCode=data['recoveryCodes'][0])
        self.assertEqual(response.data['code'], 0)
        next_draft = self.login(account.username).data['data']
        self.assertEqual(self.login(account.username, otpChallenge=next_draft['otpChallenge'],
            otpCode=data['recoveryCodes'][0]).data['code'], 400)
        with store_context(self.second):
            OtpLoginChallenge.objects.filter(consumed=False).update(expires_at=timezone.now())
        self.assertEqual(self.login(account.username, otpChallenge=next_draft['otpChallenge'],
            otpCode=data['recoveryCodes'][1]).data['code'], 400)

    def test_token_context_owns_database_and_foreign_hints_never_switch_it(self):
        for store in (self.first, self.second):
            account = self.actors[store.slug]
            client, token, refresh = self.auth_client(account, store)
            other = 'xinan' if store.slug == 'shenglang' else 'shenglang'
            self.assertEqual(client.get('/api/admin/auth/profile?store=' + other).status_code, 400)
            self.assertEqual(client.get('/api/stores/current/', HTTP_X_STORE_SLUG=other).status_code, 400)
            response = self.client.post('/api/admin/auth/refresh', {'refresh': refresh}, format='json', HTTP_X_STORE_SLUG=other)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(client.get('/api/admin/auth/profile', HTTP_X_STORE_SLUG=store.slug).status_code, 200)
            forged = token[:-8] + 'tampered'
            self.assertEqual(self.client.get('/api/admin/auth/profile', HTTP_AUTHORIZATION='Bearer ' + forged).status_code, 401)

    def test_anonymous_boss_and_unscoped_legacy_tokens_cannot_read_admin_data(self):
        for path in ('/api/admin/auth/profile', '/api/stores/current/', '/api/stores/available/', '/api/admin/dashboard/'):
            self.assertEqual(self.client.get(path + '?store=xinan', HTTP_X_STORE_SLUG='xinan').status_code, 401)
        with store_context(self.second):
            boss = ClubAccount.objects.create_account(username='not-console', account_type='BOSS')
            token, _ = issue_account_tokens(boss)
        self.assertEqual(self.client.get('/api/stores/current/', HTTP_AUTHORIZATION='Bearer ' + token).status_code, 401)
        _, token, refresh = self.auth_client(self.actors['shenglang'], self.first)
        old = AccessToken(token)
        del old['store_slug']
        self.assertEqual(self.client.get('/api/admin/auth/profile', HTTP_AUTHORIZATION='Bearer ' + str(old)).status_code, 401)
        old_refresh = RefreshToken(refresh)
        del old_refresh['store_slug']
        self.assertEqual(self.client.post('/api/admin/auth/refresh', {'refresh': str(old_refresh)}, format='json').status_code, 401)

    def test_regular_admin_finance_aftersales_and_support_stay_in_their_own_store(self):
        for index, permissions in enumerate((['user:view'], ['wallet:view'], ['engagement:fulfill'], ['chat:view'])):
            with store_context(self.second):
                account = admin('neutral-ordinary-' + str(index))
                membership = account.admin_membership
                role = AdminRole.objects.create(code='neutral_role_' + str(index), name='中立权限测试' + str(index), permissions=permissions)
                membership.roles.set([role])
            client, _, _ = self.auth_client(account, self.second)
            profile = client.get('/api/admin/auth/profile')
            self.assertEqual(profile.status_code, 200)
            self.assertFalse(profile.data['data']['is_superuser'])
            self.assertEqual(profile.data['data']['permissions'], permissions)
            self.assertEqual(client.get('/api/stores/current/').data['data']['slug'], 'xinan')
            self.assertEqual(client.get('/api/stores/current/?store=shenglang').status_code, 400)
            self.assertEqual(client.get('/api/stores/manage/').status_code, 403)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_explicit_platform_grant_still_requires_the_original_stores_real_super_role(self):
        owner = self.actors['shenglang']
        StorePlatformGrant.objects.create(account_id=owner.pk)
        client, _, _ = self.auth_client(owner, self.first)
        self.assertEqual(client.get('/api/stores/manage/').status_code, 200)
        self.assertEqual(len(client.get('/api/stores/available/').data['data']['results']), 2)
        client, _, _ = self.auth_client(self.actors['xinan'], self.second)
        self.assertEqual(client.get('/api/stores/manage/').status_code, 403)
        self.assertEqual(len(client.get('/api/stores/available/').data['data']['results']), 1)

    def test_disabled_store_and_revoked_membership_cannot_use_old_tokens_or_refresh(self):
        client, _, refresh = self.auth_client(self.actors['xinan'], self.second)
        self.second.status = 'DISABLED'
        self.second.save(update_fields=['status'])
        self.assertEqual(client.get('/api/admin/auth/profile').status_code, 401)
        self.assertEqual(self.client.post('/api/admin/auth/refresh', {'refresh': refresh}, format='json').status_code, 401)
        self.assertEqual(self.login('xinan/' + self.actors['xinan'].username).status_code, 400)
        client, _, _ = self.auth_client(self.actors['shenglang'], self.first)
        membership = self.actors['shenglang'].admin_membership
        membership.is_active = False
        membership.save(update_fields=['is_active'])
        self.assertEqual(client.get('/api/stores/current/').status_code, 401)

    def test_neutral_private_images_use_only_their_existing_signed_file_capability(self):
        from chat.models import ChatMessage, ChatSession
        from common.private_media import private_media_url
        with TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=folder):
            with store_context(self.second):
                account = self.actors['xinan']
                # Supervisor can access an unclaimed shared-pool conversation.
                role = AdminRole.objects.create(code='neutral_media', name='中立图片权限', permissions=['chat:view', 'chat:supervise'])
                account.admin_membership.roles.add(role)
                boss = ClubAccount.objects.create_account(username='private-image-boss', account_type='BOSS')
                session = ChatSession.objects.create(account=boss)
                message = ChatMessage.objects.create(session=session, sender_account=account, content_type='IMAGE')
                output = BytesIO()
                Image.new('RGB', (8, 8), 'blue').save(output, format='PNG')
                message.image.save('proof.png', ContentFile(output.getvalue()))
                request = APIRequestFactory().get('/api/admin/chat/', HTTP_HOST=HOST)
                request.account = account
                path = urlsplit(private_media_url(request, message.image))
            response = self.client.get(path.path + '?' + path.query)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(b''.join(response.streaming_content))
            self.assertEqual(self.client.get(path.path + '?store=shenglang').status_code, 400)
            self.assertEqual(self.client.get(path.path.replace('/private/', '/private/tampered')).status_code, 401)


@override_settings(LOGIN_OTP_REQUIRED=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class NeutralAdminSocketTest(TransactionTestCase):
    databases = {'default', 'store_b'}

    def test_console_tokens_route_the_neutral_socket_and_cannot_be_changed_by_store_hints(self):
        from channels.testing import WebsocketCommunicator
        from club_accounts.channels_middleware import ClubAccountTokenAuthMiddleware
        from orders.consumers import OrderConsumer
        first, second = resolve_store(), xinan_store()
        tokens = []
        for store in (first, second):
            with store_context(store):
                account = admin('neutral-ws', super_admin=True)
                credential(account)
                tokens.append(issue_account_tokens(account, otp_verified=True)[0])
        with store_context(second):
            boss = ClubAccount.objects.create_account(username='neutral-ws-boss', account_type='BOSS')
            boss_token = issue_account_tokens(boss)[0]
        application = ClubAccountTokenAuthMiddleware(OrderConsumer.as_asgi())

        async def run():
            for token in tokens:
                socket = WebsocketCommunicator(application, '/ws/orders/?token=' + token, headers=[(b'host', HOST.encode())])
                self.assertTrue((await socket.connect())[0])
                self.assertEqual(socket.scope.get('headers')[0][1], HOST.encode())
                await socket.disconnect()
            for token, hint in [(tokens[0], 'xinan'), (tokens[1], 'shenglang'), (boss_token, 'xinan')]:
                socket = WebsocketCommunicator(application, '/ws/orders/?token=' + token + '&store=' + hint,
                    headers=[(b'host', HOST.encode())])
                self.assertEqual(await socket.connect(), (False, 4401))
                await socket.disconnect()
        async_to_sync(run)()
