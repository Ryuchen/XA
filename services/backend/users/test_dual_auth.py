from unittest.mock import patch

from django.test import override_settings
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens
from users.models import WebLoginSession


@override_settings(WECHAT_MOCK_LOGIN=False, LOGIN_OTP_REQUIRED=True,
    WECHAT_MINIAPP_APPID='wx-dual-test', WECHAT_MINIAPP_SECRET='test-secret')
class DualUserAuthenticationTests(APITestCase):
    def setUp(self):
        self.codes = []
        self.sms = patch('users.password_login.send_verification_code',
            side_effect=lambda phone, code: self.codes.append((phone, code))).start()
        self.qr = patch('users.web_login.create_bridge_code', return_value=('data:image/png;base64,test', '')).start()
        self.openid = patch('users.web_login._wechat_code2session_proof',
            return_value={'openid': 'wx-provider-proof', 'unionid': ''}).start()
        self.addCleanup(patch.stopall)
        self.account = ClubAccount.objects.create_account(username='dual-provider', password='secret-password',
            account_type='PROVIDER', phone='13800001111', is_phone_verified=False, is_openid_bound=False)

    def login(self, **data):
        return self.client.post('/api/users/password-login/', {
            'audience': 'provider', 'username': self.account.username, 'password': 'secret-password', **data,
        }, format='json')

    def test_password_sms_is_mandatory_and_token_uses_the_new_auth_method(self):
        first = self.login()
        self.assertEqual(first.status_code, 200, first.data)
        draft = first.data['data']
        self.assertTrue(draft['requiresSms'])
        self.assertNotIn('token', draft)
        self.assertEqual(draft['maskedPhone'], '138****1111')
        self.assertEqual(self.codes[0][0], '13800001111')
        wrong = self.login(smsChallenge=draft['smsChallenge'], smsCode='000000')
        self.assertEqual(wrong.status_code, 400)
        completed = self.login(smsChallenge=draft['smsChallenge'], smsCode=self.codes[-1][1])
        self.assertEqual(completed.status_code, 200, completed.data)
        result = completed.data['data']
        self.assertEqual(result['userInfo']['role'], 'provider')
        self.assertTrue(result['userInfo']['isPhoneVerified'])
        self.assertEqual(RefreshToken(result['refreshToken'])['auth_method'], 'password_sms')
        replay = self.login(smsChallenge=draft['smsChallenge'], smsCode=self.codes[-1][1])
        self.assertEqual(replay.status_code, 400)
        refresh = self.client.post('/api/users/refresh/', {'refresh': result['refreshToken']}, format='json')
        self.assertEqual(refresh.status_code, 200, refresh.data)

    def test_password_login_requires_a_phone_and_never_auto_creates_provider(self):
        self.account.phone = None
        self.account.save(update_fields=['phone'])
        response = self.login()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['errorCode'], 'PHONE_REQUIRED')
        self.assertFalse(ClubAccount.objects.filter(username='unknown-provider').exists())
        self.client.post('/api/users/password-login/', {'audience': 'provider',
            'username': 'unknown-provider', 'password': 'secret-password'}, format='json')
        self.assertFalse(ClubAccount.objects.filter(username='unknown-provider').exists())

    def test_wrong_sms_attempts_persist_and_lock_the_challenge(self):
        from users.models import PasswordLoginChallenge
        draft = self.login().data['data']
        actual = self.codes[-1][1]
        wrong = '000000' if actual != '000000' else '999999'
        for _ in range(5):
            response = self.login(smsChallenge=draft['smsChallenge'], smsCode=wrong)
            self.assertEqual(response.status_code, 400)
        challenge = PasswordLoginChallenge.objects.get(pk=draft['smsChallenge'])
        self.assertEqual(challenge.sms_attempts, 5)
        self.assertFalse(challenge.consumed)
        response = self.login(smsChallenge=draft['smsChallenge'], smsCode=actual)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['errorCode'], 'SMS_CODE_EXPIRED')

    def test_new_login_request_cannot_bypass_sms_cooldown(self):
        from datetime import timedelta
        from django.utils import timezone
        from users.models import PasswordLoginChallenge
        first = self.login().data['data']
        old_code = self.codes[-1][1]
        blocked = self.login()
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.data['errorCode'], 'SMS_RATE_LIMITED')
        self.assertEqual(len(self.codes), 1)
        PasswordLoginChallenge.objects.filter(pk=first['smsChallenge']).update(sms_sent_at=timezone.now()-timedelta(seconds=61))
        second = self.login().data['data']
        self.assertNotEqual(first['smsChallenge'], second['smsChallenge'])
        self.assertEqual(len(self.codes), 2)
        self.assertEqual(self.login(smsChallenge=first['smsChallenge'], smsCode=old_code).status_code, 400)
        self.assertEqual(self.login(smsChallenge=second['smsChallenge'], smsCode=self.codes[-1][1]).status_code, 200)

    def test_uncertain_sms_delivery_retains_cooldown(self):
        from users.models import PasswordLoginChallenge
        from users.sms_transport import SmsUnavailable
        self.sms.side_effect = SmsUnavailable('发送结果不明')
        failed = self.login()
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(PasswordLoginChallenge.objects.filter(account=self.account).count(), 1)
        blocked = self.login()
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.data['errorCode'], 'SMS_RATE_LIMITED')
        self.assertEqual(self.sms.call_count, 1)

    def test_resend_checks_changed_phone_before_sending(self):
        from datetime import timedelta
        from django.utils import timezone
        from users.models import PasswordLoginChallenge
        draft = self.login().data['data']
        PasswordLoginChallenge.objects.filter(pk=draft['smsChallenge']).update(sms_sent_at=timezone.now()-timedelta(seconds=61))
        self.account.phone = '13800002222'
        self.account.save(update_fields=['phone'])
        response = self.login(smsChallenge=draft['smsChallenge'], resend=True)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['errorCode'], 'SMS_CODE_EXPIRED')
        self.assertEqual(len(self.codes), 1)

    def test_uncertain_resend_preserves_new_code_and_cooldown(self):
        from datetime import timedelta
        from django.utils import timezone
        from users.models import PasswordLoginChallenge
        from users.sms_transport import SmsUnavailable
        draft = self.login().data['data']
        challenge = PasswordLoginChallenge.objects.get(pk=draft['smsChallenge'])
        PasswordLoginChallenge.objects.filter(pk=challenge.pk).update(sms_sent_at=timezone.now()-timedelta(seconds=61))
        self.sms.side_effect = SmsUnavailable('发送结果不明')
        self.assertEqual(self.login(smsChallenge=draft['smsChallenge'], resend=True).status_code, 503)
        challenge.refresh_from_db()
        self.assertTrue(challenge.sms_hash)
        self.assertGreater(challenge.sms_sent_at, timezone.now()-timedelta(seconds=10))
        self.assertEqual(self.login(smsChallenge=draft['smsChallenge'], resend=True).status_code, 429)

    def test_sms_sent_to_old_phone_cannot_verify_changed_phone(self):
        draft = self.login().data['data']
        self.account.phone = '13800002222'
        self.account.save(update_fields=['phone'])
        response = self.login(smsChallenge=draft['smsChallenge'], smsCode=self.codes[-1][1])
        self.assertEqual(response.status_code, 400)
        self.account.refresh_from_db()
        self.assertFalse(self.account.is_phone_verified)

    def test_bound_otp_is_default_and_sms_is_an_explicit_fallback(self):
        from django.utils import timezone
        from club_accounts.models import OtpCredential
        from club_accounts.otp import encrypt_secret, hotp
        secret = 'JBSWY3DPEHPK3PXP'
        credential = OtpCredential.objects.create(account=self.account, secret_ciphertext=encrypt_secret(secret))
        first = self.login().data['data']
        self.assertTrue(first['requiresOTP'])
        self.assertFalse(first['requiresOTPSetup'])
        self.assertEqual(self.codes, [])
        code = hotp(secret, int(timezone.now().timestamp()) // 30)
        completed = self.login(otpChallenge=first['otpChallenge'], otpCode=code)
        self.assertEqual(completed.data['code'], 0, completed.data)
        self.assertEqual(RefreshToken(completed.data['data']['refreshToken'])['auth_method'], 'password_otp')
        replay = self.login(otpChallenge=first['otpChallenge'], otpCode=code)
        self.assertEqual(replay.data['code'], 400)
        draft = self.login(verificationMethod='sms').data['data']
        self.assertTrue(draft['requiresSms'])
        sms = self.login(smsChallenge=draft['smsChallenge'], smsCode=self.codes[-1][1])
        self.assertEqual(RefreshToken(sms.data['data']['refreshToken'])['auth_method'], 'password_sms')
        credential.delete()
        expired = self.client.post('/api/users/refresh/', {'refresh': completed.data['data']['refreshToken']}, format='json')
        self.assertEqual(expired.status_code, 401)

    def test_logged_in_account_can_enroll_otp_without_replacing_an_existing_credential(self):
        from django.utils import timezone
        from club_accounts.models import OtpCredential
        from club_accounts.otp import hotp
        self.account.is_phone_verified = True
        self.account.save(update_fields=['is_phone_verified'])
        token, _ = issue_account_tokens(self.account, auth_method='password_sms')
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertFalse(self.client.get('/api/users/otp-setup/').data['data']['isOtpBound'])
        draft = self.client.post('/api/users/otp-setup/', {}, format='json').data['data']
        self.assertTrue(draft['requiresOTPSetup'])
        code = hotp(draft['otpSecret'], int(timezone.now().timestamp()) // 30)
        result = self.client.post('/api/users/otp-setup/', {
            'otpChallenge': draft['otpChallenge'], 'otpCode': code,
        }, format='json')
        self.assertTrue(result.data['data']['isOtpBound'])
        self.assertEqual(len(result.data['data']['recoveryCodes']), 8)
        original = OtpCredential.objects.get(account=self.account).secret_ciphertext
        again = self.client.post('/api/users/otp-setup/', {}, format='json')
        self.assertEqual(again.data['code'], 400)
        self.assertEqual(OtpCredential.objects.get(account=self.account).secret_ciphertext, original)
        self.client.credentials()
        self.assertEqual(self.client.get('/api/users/otp-setup/').status_code, 401)

    def test_boss_with_otp_uses_authenticator_by_default(self):
        from django.utils import timezone
        from club_accounts.models import OtpCredential
        from club_accounts.otp import encrypt_secret, hotp
        boss = ClubAccount.objects.create_account(username='boss-otp', password='boss-password',
            account_type='BOSS', phone='13800002222', is_phone_verified=True)
        secret = 'JBSWY3DPEHPK3PXP'
        OtpCredential.objects.create(account=boss, secret_ciphertext=encrypt_secret(secret))
        payload = {'audience': 'boss', 'username': boss.username, 'password': 'boss-password'}
        draft = self.client.post('/api/users/password-login/', payload, format='json').data['data']
        self.assertTrue(draft['requiresOTP'])
        code = hotp(secret, int(timezone.now().timestamp()) // 30)
        response = self.client.post('/api/users/password-login/', {
            **payload, 'otpChallenge': draft['otpChallenge'], 'otpCode': code,
        }, format='json')
        self.assertEqual(response.data['data']['userInfo']['role'], 'customer')
        self.assertEqual(RefreshToken(response.data['data']['refreshToken'])['auth_method'], 'password_otp')

    def start(self, **data):
        response = self.client.post('/api/users/web-login/start/', data, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['data']

    def authorize(self, flow):
        response = self.client.post('/api/users/web-login/authorize/', {
            'ticket': flow['ticket'], 'code': 'external-code',
        }, format='json')
        return response, {**flow, **response.data.get('data', {})}

    def test_provider_wechat_never_creates_an_account(self):
        flow = self.start(audience='provider')
        response, _ = self.authorize(flow)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['errorCode'], 'WECHAT_ACCOUNT_UNBOUND')
        self.assertFalse(ClubAccount.objects.filter(openid='wx-provider-proof').exists())

    def test_authenticated_provider_can_bind_wechat_then_use_quick_login(self):
        self.account.is_phone_verified = True
        self.account.save(update_fields=['is_phone_verified'])
        token, _ = issue_account_tokens(self.account, auth_method='password_sms')
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        flow = self.start(audience='provider', purpose='bind_wechat')
        self.client.credentials()
        response, flow = self.authorize(flow)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(flow['canConfirm'])
        confirmed = self.client.post('/api/users/web-login/confirm/', {
            'ticket': flow['ticket'], 'bridge_token': flow['bridge_token'],
        }, format='json')
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        self.account.refresh_from_db()
        self.assertEqual(self.account.openid, 'wx-provider-proof')
        self.assertTrue(self.account.is_openid_bound)
        result = self.client.get('/api/users/web-login/status/', {
            'ticket': flow['ticket'], 'poll_token': flow['poll_token'],
        }).data['data']
        self.assertEqual(result['userInfo']['role'], 'provider')
        self.assertEqual(RefreshToken(result['refreshToken'])['auth_method'], 'wechat')
        quick = self.start(audience='provider')
        response, quick = self.authorize(quick)
        self.assertEqual(response.status_code, 200, response.data)
        self.client.post('/api/users/web-login/confirm/', {
            'ticket': quick['ticket'], 'bridge_token': quick['bridge_token'],
        }, format='json')
        self.assertEqual(self.client.get('/api/users/web-login/status/', {
            'ticket': quick['ticket'], 'poll_token': quick['poll_token'],
        }).data['data']['status'], 'CONFIRMED')

    def test_phone_verified_boss_can_set_password_for_browser_login(self):
        boss = ClubAccount.objects.create_account(username='wx_boss_without_password', password=None,
            account_type='BOSS', phone='13800002222', is_phone_verified=True,
            openid='wx-boss-existing', is_openid_bound=True)
        token, _ = issue_account_tokens(boss)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        response = self.client.post('/api/users/password/', {
            'username': 'boss-browser-login', 'password': 'browser-password',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        boss.refresh_from_db()
        self.assertEqual(boss.username, 'boss-browser-login')
        self.assertTrue(boss.check_password('browser-password'))
        self.client.credentials()
        draft = self.client.post('/api/users/password-login/', {
            'audience': 'boss', 'username': 'boss-browser-login', 'password': 'browser-password',
        }, format='json').data['data']
        self.assertTrue(draft['requiresSms'])
        completed = self.client.post('/api/users/password-login/', {
            'audience': 'boss', 'username': 'boss-browser-login', 'password': 'browser-password',
            'smsChallenge': draft['smsChallenge'], 'smsCode': self.codes[-1][1],
        }, format='json')
        self.assertEqual(completed.data['data']['userInfo']['role'], 'customer')
