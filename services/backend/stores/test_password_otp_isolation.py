from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from club_accounts.models import OtpCredential
from club_accounts.otp import encrypt_secret, hotp
from stores.context import resolve_store, store_context
from stores.tests_foundation import admin
from stores.tests_hosts import xinan_store


@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False)
class PasswordOtpIsolationTests(TestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.client = APIClient(HTTP_HOST='admin.esports.club')
        self.codes = []
        sender = patch('users.password_login.send_verification_code',
                       side_effect=lambda phone, code: self.codes.append((phone, code)))
        sender.start()
        self.addCleanup(sender.stop)
        self.secret = 'JBSWY3DPEHPK3PXP'
        for store in (resolve_store(), xinan_store()):
            with store_context(store):
                account = admin('same-otp-account')
                account.phone = '13800001111'
                account.save(update_fields=['phone'])
                OtpCredential.objects.create(account=account, secret_ciphertext=encrypt_secret(self.secret))

    def login(self, username, **extra):
        return self.client.post('/api/admin/auth/password-login', {
            'username': username, 'password': 'test-password', **extra,
        }, format='json')

    def test_signed_otp_challenge_is_store_bound_and_sms_fallback_remains_available(self):
        ambiguous = self.login('same-otp-account')
        self.assertEqual(ambiguous.status_code, 409)
        draft = self.login('xinan/same-otp-account').data['data']
        self.assertTrue(draft['requiresOTP'])
        code = hotp(self.secret, int(timezone.now().timestamp()) // 30)
        foreign = self.login('shenglang/same-otp-account', otpChallenge=draft['otpChallenge'], otpCode=code)
        self.assertEqual(foreign.status_code, 400)
        completed = self.login('xinan/same-otp-account', otpChallenge=draft['otpChallenge'], otpCode=code)
        self.assertEqual(completed.data['code'], 0, completed.data)
        self.assertEqual(RefreshToken(completed.data['data']['refresh'])['store_slug'], 'xinan')
        self.assertEqual(RefreshToken(completed.data['data']['refresh'])['auth_method'], 'password_otp')
        self.assertEqual(self.login('xinan/same-otp-account', otpChallenge=draft['otpChallenge'], otpCode=code).data['code'], 400)
        sms = self.login('xinan/same-otp-account', verificationMethod='sms').data['data']
        self.assertTrue(sms['requiresSms'])
        finished = self.login('xinan/same-otp-account', smsChallenge=sms['smsChallenge'], smsCode=self.codes[-1][1])
        self.assertEqual(RefreshToken(finished.data['data']['refresh'])['store_slug'], 'xinan')

    def test_sms_cooldown_is_scoped_to_the_authenticated_store(self):
        first = self.login('shenglang/same-otp-account', verificationMethod='sms')
        self.assertEqual(first.status_code, 200, first.data)
        blocked = self.login('shenglang/same-otp-account', verificationMethod='sms')
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.data['errorCode'], 'SMS_RATE_LIMITED')
        second = self.login('xinan/same-otp-account', verificationMethod='sms')
        self.assertEqual(second.status_code, 200, second.data)
        self.assertTrue(second.data['data']['requiresSms'])
        self.assertEqual(len(self.codes), 2)
