from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from club_accounts.models import WechatIdentity
from stores.context import store_context
from stores.context import resolve_store
from stores.tests_foundation import admin


@override_settings(DEBUG=True, WECHAT_H5_APPID='wx-h5-test', WECHAT_H5_SECRET='test-secret',
    WECHAT_H5_ALLOWED_ORIGINS=['http://admin.esports.club'], LOGIN_OTP_REQUIRED=True,
    LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class ConsoleH5DualAuthTests(TestCase):
    def setUp(self):
        self.store = resolve_store()
        with store_context(self.store):
            self.account = admin('h5-console-admin', super_admin=True)
            self.account.phone = '13800001111'
            self.account.openid = None
            self.account.is_openid_bound = False
            self.account.is_phone_verified = False
            self.account.save(update_fields=['phone', 'openid', 'is_openid_bound', 'is_phone_verified'])
        self.client = APIClient()
        self.client.defaults['HTTP_HOST'] = 'admin.esports.club'
        self.codes = []
        self.sms = patch('users.password_login.send_verification_code',
            side_effect=lambda phone, code: self.codes.append((phone, code))).start()
        self.exchange = patch('users.h5_login.exchange_code', return_value={
            'openid': 'wx-h5-console-proof', 'unionid': 'wx-union-console',
        }).start()
        self.addCleanup(patch.stopall)

    def password(self, **data):
        return self.client.post('/api/admin/auth/password-login', {
            'username': self.account.username, 'password': 'test-password', **data,
        }, format='json')

    def start_h5(self, **data):
        response = self.client.post('/api/admin/auth/h5-login/start', {
            'return_origin': 'http://admin.esports.club', **data,
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['data']

    def authorize(self, flow):
        state = parse_qs(urlsplit(flow['authorization_url']).query)['state'][0]
        response = self.client.get('/api/admin/auth/h5-login/callback', {
            'code': 'external-h5-code', 'state': state,
        })
        self.assertEqual(response.status_code, 200, response.content.decode())
        return self.client.get('/api/admin/auth/h5-login/status', {
            'ticket': flow['ticket'], 'poll_token': flow['poll_token'],
        })

    def test_first_password_sms_login_allows_optional_h5_binding(self):
        draft = self.password().data['data']
        self.assertTrue(draft['requiresSms'])
        self.assertNotIn('token', draft)
        initialized = self.password(smsChallenge=draft['smsChallenge'], smsCode=self.codes[-1][1]).data['data']
        self.assertTrue(initialized['token'])
        self.assertEqual(RefreshToken(initialized['refresh'])['auth_method'], 'password_sms')
        self.assertFalse(initialized['profile']['isWechatBound'])
        profile = self.client.get('/api/admin/auth/profile', HTTP_AUTHORIZATION='Bearer ' + initialized['token'])
        self.assertEqual(profile.status_code, 200, profile.data)
        flow = self.start_h5(purpose='bind_wechat', bindToken=initialized['bindToken'])
        result = self.authorize(flow)
        self.assertEqual(result.status_code, 200, result.data)
        payload = result.data['data']
        self.assertEqual(payload['status'], 'CONFIRMED')
        self.assertEqual(payload['store_slug'], self.store.slug)
        self.assertEqual(RefreshToken(payload['refresh'])['auth_method'], 'wechat')
        identity = WechatIdentity.objects.get(account=self.account, channel='OFFICIAL_H5')
        self.assertEqual(identity.openid, 'wx-h5-console-proof')
        self.assertEqual(identity.unionid, 'wx-union-console')
        self.account.refresh_from_db()
        self.assertTrue(self.account.is_openid_bound)
        self.assertTrue(self.account.is_phone_verified)
        profile = self.client.get('/api/admin/auth/profile', HTTP_AUTHORIZATION='Bearer ' + payload['token'])
        self.assertEqual(profile.status_code, 200, profile.data)
        quick = self.start_h5()
        self.assertEqual(self.authorize(quick).data['data']['status'], 'CONFIRMED')

        # The next SMS login occurs after the account-wide send cooldown.
        from datetime import timedelta
        from django.utils import timezone
        from users.models import PasswordLoginChallenge
        with store_context(self.store):
            PasswordLoginChallenge.objects.filter(account=self.account).update(sms_sent_at=timezone.now() - timedelta(seconds=61))
        second = self.password().data['data']
        self.assertTrue(second['requiresSms'])
        completed = self.password(smsChallenge=second['smsChallenge'], smsCode=self.codes[-1][1])
        self.assertEqual(completed.status_code, 200, completed.data)
        self.assertNotIn('requiresWechatBinding', completed.data['data'])
        self.assertTrue(completed.data['data']['token'])

    def test_bound_otp_defaults_to_authenticator_and_allows_sms_fallback(self):
        from django.utils import timezone
        from club_accounts.models import OtpCredential
        from club_accounts.otp import encrypt_secret, hotp
        secret = 'JBSWY3DPEHPK3PXP'
        with store_context(self.store):
            OtpCredential.objects.create(account=self.account, secret_ciphertext=encrypt_secret(secret))
        draft = self.password().data['data']
        self.assertTrue(draft['requiresOTP'])
        self.assertEqual(self.codes, [])
        code = hotp(secret, int(timezone.now().timestamp()) // 30)
        result = self.password(otpChallenge=draft['otpChallenge'], otpCode=code)
        self.assertEqual(result.data['code'], 0, result.data)
        self.assertEqual(RefreshToken(result.data['data']['refresh'])['auth_method'], 'password_otp')
        sms = self.password(verificationMethod='sms').data['data']
        self.assertTrue(sms['requiresSms'])
        finished = self.password(smsChallenge=sms['smsChallenge'], smsCode=self.codes[-1][1])
        self.assertEqual(RefreshToken(finished.data['data']['refresh'])['auth_method'], 'password_sms')

    def test_console_otp_enrollment_uses_authenticated_store_account(self):
        from django.utils import timezone
        from club_accounts.models import OtpCredential
        from club_accounts.otp import hotp
        draft = self.password().data['data']
        result = self.password(smsChallenge=draft['smsChallenge'], smsCode=self.codes[-1][1]).data['data']
        auth = {'HTTP_AUTHORIZATION': 'Bearer ' + result['token']}
        start = self.client.post('/api/admin/auth/otp-setup', {}, format='json', **auth).data['data']
        code = hotp(start['otpSecret'], int(timezone.now().timestamp()) // 30)
        complete = self.client.post('/api/admin/auth/otp-setup', {
            'otpChallenge': start['otpChallenge'], 'otpCode': code,
        }, format='json', **auth)
        self.assertTrue(complete.data['data']['isOtpBound'])
        with store_context(self.store):
            self.assertTrue(OtpCredential.objects.filter(account=self.account).exists())
        next_login = self.password().data['data']
        self.assertTrue(next_login['requiresOTP'])
