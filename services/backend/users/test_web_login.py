from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from users.models import WebLoginSession
from wallet.models import Wallet


@override_settings(WECHAT_MINIAPP_APPID='wx-bridge-test', WECHAT_MINIAPP_SECRET='test-only-secret', WECHAT_MOCK_LOGIN=False)
class WebLoginTests(APITestCase):
    """Real views, models, wallet and tokens; only external WeChat/SMS transport is isolated."""
    base = '/api/users/web-login/'

    def setUp(self):
        self.qr = patch('users.web_login.create_bridge_code', return_value=('data:image/png;base64,test', '')).start()
        self.weixin = patch('users.web_login._wechat_code2session', return_value='wx-boss-proof').start()
        self.phone = patch('users.web_login._wechat_get_phone', return_value='13800001111').start()
        self.sent_codes = []
        self.sms = patch('users.web_login.send_verification_code', side_effect=lambda phone, code: self.sent_codes.append((phone, code))).start()
        self.addCleanup(patch.stopall)

    def start(self, **body):
        response = self.client.post(self.base + 'start/', body, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data['data']

    def authorize(self, request, phone=False):
        body = {'ticket': request['ticket'], 'code': 'real-external-code'}
        if phone:
            body['phoneCode'] = 'real-external-phone-code'
        response = self.client.post(self.base + 'authorize/', body, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        return {**request, **response.data['data']}

    def confirm(self, request, **body):
        return self.client.post(self.base + 'confirm/', {
            'ticket': request['ticket'], 'bridge_token': request.get('bridge_token', ''), **body,
        }, format='json')

    def status(self, request):
        return self.client.get(self.base + 'status/', {'ticket': request['ticket'], 'poll_token': request['poll_token']})

    def send(self, request):
        return self.client.post(self.base + 'sms/', {'ticket': request['ticket'], 'bridge_token': request.get('bridge_token', '')}, format='json')

    def existing(self, **data):
        defaults = {'username': 'existing-boss', 'openid': 'wx-boss-proof', 'phone': '13800001111',
            'role': 'CUSTOMER', 'is_phone_verified': True, 'is_openid_bound': True}
        user = get_user_model().objects.create_user(password=None, **{**defaults, **data})
        account = get_or_create_account_for_legacy_user(user)
        return user, account

    def test_new_boss_requires_wechat_phone_sms_and_explicit_confirmation(self):
        request = self.start()
        self.assertEqual(self.status(request).data['data']['status'], 'PENDING')
        request = self.authorize(request)
        self.assertTrue(request['requiresPhoneAuthorization'])
        self.assertEqual(self.confirm(request).status_code, 400)
        request = self.authorize(request, phone=True)
        self.assertTrue(request['requiresSms'])
        self.assertEqual(self.send(request).data['code'], 0)
        phone, code = self.sent_codes[-1]
        self.assertEqual(phone, '13800001111')
        self.assertNotIn(code, str(self.status(request).data))
        self.assertEqual(self.confirm(request, smsCode=code).data['code'], 0)
        result = self.status(request).data['data']
        self.assertEqual(result['status'], 'CONFIRMED')
        self.assertEqual(result['userInfo']['role'], 'customer')
        account = ClubAccount.objects.get(pk=result['userInfo']['id'])
        self.assertTrue(account.is_phone_verified)
        self.assertEqual(account.openid, 'wx-boss-proof')
        self.assertFalse(account.has_usable_password())
        self.assertTrue(Wallet.objects.filter(account=account).exists())
        refresh = self.client.post('/api/users/refresh/', {'refresh': result['refreshToken']}, format='json')
        self.assertEqual(refresh.data['code'], 0)
        before = ClubAccount.objects.count()
        self.assertEqual(self.confirm(request, smsCode=code).data['code'], 0)
        self.assertEqual(ClubAccount.objects.count(), before)

    def test_existing_wechat_boss_preserves_wallet_and_account_identity(self):
        user, account = self.existing()
        wallet = Wallet.objects.get(user=user)
        wallet.balance = 12340
        wallet.save(update_fields=['balance'])
        request = self.authorize(self.start())
        self.assertTrue(request['canConfirm'])
        self.assertEqual(self.confirm(request).data['code'], 0)
        self.assertEqual(self.status(request).data['data']['userInfo']['id'], str(account.pk))
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 12340)
        self.sms.assert_not_called()

    def test_verified_phone_links_unique_unbound_legacy_boss_without_moving_assets(self):
        user, account = self.existing(openid=None, is_openid_bound=False, is_phone_verified=False)
        wallet = Wallet.objects.get(user=user)
        request = self.authorize(self.start(), phone=True)
        self.send(request)
        self.assertEqual(self.confirm(request, smsCode=self.sent_codes[-1][1]).data['code'], 0)
        self.assertEqual(self.status(request).data['data']['userInfo']['id'], str(account.pk))
        self.assertEqual(Wallet.objects.get(user=user).pk, wallet.pk)

    def test_poll_requires_browser_secret_and_bridge_confirmation_requires_wechat_secret(self):
        self.existing()
        request = self.authorize(self.start())
        invalid = {**request, 'poll_token': 'not-the-browser'}
        self.assertEqual(self.status(invalid).status_code, 403)
        self.assertEqual(self.confirm({**request, 'bridge_token': ''}).status_code, 403)
        self.assertEqual(self.status(request).data['data']['status'], 'PENDING')

    def test_sms_cannot_be_sent_without_wechat_phone_proof_or_bridge_secret(self):
        request = self.start()
        self.assertEqual(self.send(request).status_code, 403)
        request = self.authorize(request)
        self.assertEqual(self.send(request).status_code, 400)
        request = self.authorize(request, phone=True)
        self.assertEqual(self.send({**request, 'bridge_token': ''}).status_code, 403)
        self.sms.assert_not_called()

    def test_phone_is_never_taken_from_browser_input(self):
        request = self.start()
        response = self.client.post(self.base + 'authorize/', {'ticket': request['ticket'], 'code': 'x', 'phone': '13800002222'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.weixin.assert_not_called()
        self.phone.assert_not_called()

    def test_bad_sms_attempts_and_cooldown_are_persisted(self):
        request = self.authorize(self.start(), phone=True)
        self.assertEqual(self.send(request).status_code, 200)
        self.assertEqual(self.send(request).status_code, 429)
        for _ in range(5):
            self.assertEqual(self.confirm(request, smsCode='wrong-code').data['errorCode'], 'SMS_CODE_INVALID')
        self.assertEqual(self.confirm(request, smsCode=self.sent_codes[-1][1]).data['errorCode'], 'SMS_CODE_EXPIRED')
        self.assertEqual(WebLoginSession.objects.get(ticket=request['ticket']).sms_attempts, 5)

    def test_expired_qr_cannot_authorize_or_confirm(self):
        request = self.start()
        WebLoginSession.objects.filter(ticket=request['ticket']).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.status(request).data['data']['status'], 'EXPIRED')
        response = self.client.post(self.base + 'authorize/', {'ticket': request['ticket'], 'code': 'x'}, format='json')
        self.assertEqual(response.status_code, 410)
        self.weixin.assert_not_called()

    def test_provider_wechat_cannot_enter_boss_and_existing_role_is_unchanged(self):
        _, account = self.existing(role='PROVIDER')
        request = self.start()
        response = self.client.post(self.base + 'authorize/', {'ticket': request['ticket'], 'code': 'x'}, format='json')
        self.assertEqual(response.status_code, 403)
        account.refresh_from_db()
        self.assertEqual(account.account_type, 'PROVIDER')

    def test_phone_bind_is_bound_to_current_browser_account(self):
        _, account = self.existing()
        token, _ = issue_account_tokens(account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        request = self.start(purpose='bind_phone')
        self.client.credentials()
        self.weixin.return_value = 'another-wechat'
        response = self.client.post(self.base + 'authorize/', {'ticket': request['ticket'], 'code': 'x'}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(WebLoginSession.objects.get(ticket=request['ticket']).confirmed_account_id)

    def test_another_wechat_cannot_take_over_authorized_ticket(self):
        request = self.authorize(self.start())
        self.weixin.return_value = 'different-wechat'
        response = self.client.post(self.base + 'authorize/', {'ticket': request['ticket'], 'code': 'x'}, format='json')
        self.assertEqual(response.status_code, 409)

    @override_settings(WECHAT_MINIAPP_APPID='', WECHAT_MINIAPP_SECRET='')
    def test_missing_platform_configuration_cannot_return_fake_qr(self):
        before = WebLoginSession.objects.count()
        response = self.client.post(self.base + 'start/', {}, format='json')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(WebLoginSession.objects.count(), before)
        self.qr.assert_not_called()
