from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from stores.context import resolve_store, store_context
from stores.models import PlatformAccount, PlatformAuditEvent, PlatformRoleGrant, PlatformWechatIdentity
from stores.platform_auth import issue_tokens
from stores.tests_foundation import admin


@override_settings(PLATFORM_IDENTITY_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   WECHAT_MINIAPP_APPID='wx-platform-test', WECHAT_MINIAPP_SECRET='test-secret',
                   WECHAT_MOCK_LOGIN=False, ALLOWED_HOSTS=['*'])
class PlatformWechatTests(TestCase):
    def setUp(self):
        self.store = resolve_store()
        self.actor = PlatformAccount.objects.create(username='platform-wechat-owner', phone='13800001111', is_phone_verified=True)
        self.actor.set_password('Synthetic-Platform-Password-2026!')
        self.actor.save(update_fields=['password', 'security_version'])
        PlatformRoleGrant.objects.create(account=self.actor, role='SUPER_ADMIN', scope_slug='*')
        token, _ = issue_tokens(self.actor, 'SMS', self.actor.phone)
        self.platform = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        self.browser = APIClient(HTTP_HOST='admin.esports.club')
        self.bridge = APIClient(HTTP_HOST='sl.esports.club')
        self.image = patch('users.web_login.create_bridge_code', return_value=('data:image/png;base64,test', '')).start()
        self.console_image = patch('console.dual_auth.create_bridge_code', return_value=('data:image/png;base64,test', '')).start()
        self.exchange = patch('console.dual_auth._wechat_code2session', return_value='platform-wechat-proof').start()
        self.addCleanup(patch.stopall)

    def bind_start(self):
        return self.platform.post('/api/admin/platform/auth/wechat/binding', {}, format='json')

    def authorize(self, flow):
        return self.bridge.post('/api/users/web-login/authorize/', {'ticket': flow['ticket'],
            'code': 'external-wechat-code'}, format='json')

    def confirm(self, flow, bridge):
        return self.bridge.post('/api/users/web-login/confirm/', {'ticket': flow['ticket'],
            'bridge_token': bridge}, format='json')

    def status(self, flow):
        return self.browser.get('/api/admin/auth/wechat/status', {'ticket': flow['ticket'], 'poll_token': flow['poll_token']})

    def bind(self):
        result = self.bind_start()
        self.assertEqual(result.status_code, 200, result.data)
        flow = result.data['data']
        authorized = self.authorize(flow)
        self.assertEqual(authorized.status_code, 200, authorized.data)
        self.assertEqual(self.confirm(flow, authorized.data['data']['bridge_token']).status_code, 200)
        return flow

    def test_platform_binding_quick_login_and_credential_revocation(self):
        self.assertFalse(self.platform.get('/api/admin/platform/auth/wechat/binding').data['data']['isWechatBound'])
        flow = self.bind()
        result = self.status(flow)
        self.assertEqual(result.status_code, 200, result.data)
        data = result.data['data']
        self.assertEqual((data['identity_kind'], RefreshToken(data['refresh'])['auth_method']), ('platform', 'WECHAT'))
        self.assertTrue(PlatformAuditEvent.objects.filter(account=self.actor, action='auth.wechat_bound').exists())
        self.assertEqual(self.browser.get('/api/admin/platform/profile', HTTP_AUTHORIZATION='Bearer ' + data['token']).status_code, 200)
        self.assertNotEqual(self.browser.get('/api/admin/auth/profile', HTTP_AUTHORIZATION='Bearer ' + data['token']).status_code, 200)
        self.assertTrue(self.platform.get('/api/admin/platform/auth/wechat/binding').data['data']['isWechatBound'])
        quick = self.browser.post('/api/admin/auth/wechat/start', {}, format='json').data['data']
        authorized = self.authorize(quick)
        self.assertEqual(authorized.status_code, 200, authorized.data)
        self.assertEqual(self.confirm(quick, authorized.data['data']['bridge_token']).status_code, 200)
        quick_data = self.status(quick).data['data']
        self.assertEqual(quick_data['identity_kind'], 'platform')
        PlatformWechatIdentity.objects.filter(account=self.actor).delete()
        self.assertEqual(self.browser.get('/api/admin/platform/profile', HTTP_AUTHORIZATION='Bearer ' + quick_data['token']).status_code, 401)

    def test_ambiguous_platform_and_store_identity_requires_explicit_selection(self):
        self.bind()
        with store_context(self.store):
            account = admin('wechat-store-owner', super_admin=True)
            account.openid, account.is_openid_bound = 'platform-wechat-proof', True
            account.save(update_fields=['openid', 'is_openid_bound'])
        ambiguous = self.browser.post('/api/admin/auth/wechat/start', {}, format='json').data['data']
        result = self.authorize(ambiguous)
        self.assertEqual((result.status_code, result.data['errorCode']), (409, 'ADMIN_STORE_AMBIGUOUS'))
        for kind in ('platform', 'store'):
            flow = self.browser.post('/api/admin/auth/wechat/start', {'identityKind': kind}, format='json').data['data']
            authorized = self.authorize(flow)
            self.assertEqual(authorized.status_code, 200, authorized.data)
            self.assertEqual(self.confirm(flow, authorized.data['data']['bridge_token']).status_code, 200)
            payload = self.status(flow).data['data']
            self.assertEqual(payload['profile']['identity_type'] if kind == 'platform' else payload['store_slug'],
                             'platform' if kind == 'platform' else self.store.slug)

    def test_platform_security_change_before_confirmation_invalidates_bind(self):
        flow = self.bind_start().data['data']
        authorized = self.authorize(flow)
        self.assertEqual(authorized.status_code, 200, authorized.data)
        self.actor.set_password('Changed-Platform-Password-2026!')
        self.actor.save(update_fields=['password', 'security_version'])
        result = self.confirm(flow, authorized.data['data']['bridge_token'])
        self.assertEqual(result.status_code, 403, result.data)
        self.assertFalse(PlatformWechatIdentity.objects.filter(account=self.actor).exists())

    def test_binding_requires_platform_authentication(self):
        result = self.browser.post('/api/admin/platform/auth/wechat/binding', {}, format='json')
        self.assertEqual(result.status_code, 401)

    @override_settings(WECHAT_MINIAPP_APPID='', WECHAT_MINIAPP_SECRET='')
    def test_unavailable_wechat_does_not_disable_existing_platform_session(self):
        from stores.models import AdminLoginHandoff
        self.assertEqual(self.bind_start().data['errorCode'], 'WECHAT_NOT_CONFIGURED')
        self.assertEqual(self.browser.post('/api/admin/auth/wechat/start', {}, format='json').status_code, 503)
        self.assertEqual(self.platform.get('/api/admin/platform/profile').status_code, 200)
        self.assertFalse(AdminLoginHandoff.objects.exists())

    def test_existing_store_admin_bind_and_quick_login_still_work(self):
        with store_context(self.store):
            account = admin('wechat-existing-admin', super_admin=True)
            account.phone = '13800001112'
            account.save(update_fields=['phone'])
        codes = []
        credentials = {'username': account.username, 'password': 'test-password'}
        with patch('users.password_login.send_verification_code', side_effect=lambda phone, code: codes.append(code)):
            first = self.browser.post('/api/admin/auth/password-login', credentials, format='json')
            completed = self.browser.post('/api/admin/auth/password-login', {**credentials,
                'smsChallenge': first.data['data']['smsChallenge'], 'smsCode': codes[-1]}, format='json')
        self.assertEqual(completed.status_code, 200, completed.data)
        flow = self.browser.post('/api/admin/auth/wechat/start', {'purpose': 'bind_wechat',
            'bindToken': completed.data['data']['bindToken']}, format='json').data['data']
        authorized = self.authorize(flow)
        self.assertEqual(authorized.status_code, 200, authorized.data)
        self.assertEqual(self.confirm(flow, authorized.data['data']['bridge_token']).status_code, 200)
        data = self.status(flow).data['data']
        self.assertEqual(data['store_slug'], self.store.slug)
        self.assertEqual(RefreshToken(data['refresh'])['auth_method'], 'wechat')
        self.assertEqual(self.browser.get('/api/admin/auth/profile', HTTP_AUTHORIZATION='Bearer ' + data['token']).status_code, 200)
        self.assertNotEqual(self.browser.get('/api/admin/platform/profile', HTTP_AUTHORIZATION='Bearer ' + data['token']).status_code, 200)


@override_settings(ISOLATED_CONTROL_PLANE=True)
class IsolatedPlatformWechatTests(PlatformWechatTests):
    databases = {'default', 'store_a', 'store_b'}

    def setUp(self):
        from django.utils import timezone
        from stores.models import Store
        Store.objects.create(slug='shenglang', name='声浪电竞', database_alias='store_a',
                             status=Store.Status.ACTIVE, initialized_at=timezone.now())
        super().setUp()
