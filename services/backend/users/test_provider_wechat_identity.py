from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.test import override_settings
from rest_framework.test import APITestCase

from club_accounts.models import ClubAccount, WechatIdentity
from club_accounts.tokens import issue_account_tokens


@override_settings(DEBUG=True, WECHAT_MOCK_LOGIN=False, WECHAT_MINIAPP_APPID='wx-mini-test',
    WECHAT_MINIAPP_SECRET='test-secret', WECHAT_H5_APPID='wx-h5-test',
    WECHAT_H5_SECRET='test-secret', WECHAT_H5_ALLOWED_ORIGINS=['http://sl.esports.club'],
    ALLOWED_HOSTS=['*'])
class ProviderWechatIdentityTests(APITestCase):
    def setUp(self):
        self.client.defaults['HTTP_HOST'] = 'sl.esports.club'
        self.account = ClubAccount.objects.create_account(username='cross-provider', password='test-password',
            account_type='PROVIDER', phone='13800001111', is_phone_verified=True)
        self.token, _ = issue_account_tokens(self.account, auth_method='password_sms')
        patcher = patch('users.web_login.create_bridge_code', return_value=('data:image/png;base64,test', ''))
        patcher.start()
        self.addCleanup(patcher.stop)

    def start_authenticated(self, path, payload):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + self.token)
        response = self.client.post(path, payload, format='json')
        self.client.credentials()
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['data']

    def bind_mini(self, unionid, openid='mini-1'):
        flow = self.start_authenticated('/api/users/web-login/start/',
            {'audience': 'provider', 'purpose': 'bind_wechat'})
        proof = {'openid': openid}
        if unionid is not None:
            proof['unionid'] = unionid
        with patch('users.views._wechat_http_get', return_value=proof):
            response = self.client.post('/api/users/web-login/authorize/',
                {'ticket': flow['ticket'], 'code': 'wechat-code', 'unionid': 'forged-client-value'}, format='json')
        if response.status_code != 200:
            return response
        return self.client.post('/api/users/web-login/confirm/', {
            'ticket': flow['ticket'], 'bridge_token': response.data['data']['bridge_token'],
        }, format='json')

    def bind_h5(self, unionid, openid='h5-1'):
        flow = self.start_authenticated('/api/users/h5-login/start/', {
            'audience': 'provider', 'purpose': 'bind_wechat', 'return_origin': 'http://sl.esports.club',
        })
        state = parse_qs(urlsplit(flow['authorization_url']).query)['state'][0]
        with patch('users.h5_login.exchange_code', return_value={'openid': openid, 'unionid': unionid}):
            return self.client.get('/api/users/h5-login/callback/', {'code': 'wechat-code', 'state': state})

    def test_mini_first_requires_matching_unionid_and_allows_matching_identity(self):
        self.assertEqual(self.bind_mini('same').status_code, 200)
        self.assertIn('暂无法核验'.encode(), self.bind_h5('').content)
        self.assertIn('不一致'.encode(), self.bind_h5('different').content)
        self.assertFalse(WechatIdentity.objects.filter(account=self.account, channel='OFFICIAL_H5').exists())
        self.assertIn('微信授权已完成'.encode(), self.bind_h5('same').content)
        self.assertEqual(WechatIdentity.objects.filter(account=self.account).count(), 2)

    def test_h5_first_requires_matching_unionid_and_rejects_rebinding(self):
        self.assertIn('微信授权已完成'.encode(), self.bind_h5('same').content)
        self.assertEqual(self.bind_mini(None).status_code, 409)
        self.assertEqual(self.bind_mini('different').status_code, 409)
        self.assertEqual(self.bind_mini('same').status_code, 200)
        self.assertEqual(self.bind_mini('same').status_code, 200)
        self.assertIn('已绑定其他微信'.encode(), self.bind_h5('other', openid='h5-2').content)
        self.assertEqual(self.bind_mini('same', openid='mini-2').status_code, 409)
        self.assertEqual(WechatIdentity.objects.filter(account=self.account).count(), 2)

    def test_missing_unionid_is_not_invented_from_client_input(self):
        self.assertEqual(self.bind_mini(None).status_code, 200)
        identity = WechatIdentity.objects.get(account=self.account, channel='MINIAPP')
        self.assertEqual(identity.unionid, '')
        self.assertIn('暂无法核验'.encode(), self.bind_h5('same').content)

    def test_same_unionid_cannot_bind_another_account_across_channels(self):
        self.assertEqual(self.bind_mini('same').status_code, 200)
        other = ClubAccount.objects.create_account(username='other-provider', password='test-password',
            account_type='PROVIDER', phone='13800001112', is_phone_verified=True)
        self.token, _ = issue_account_tokens(other, auth_method='password_sms')
        self.assertIn('已绑定其他账号'.encode(), self.bind_h5('same').content)
        self.assertFalse(WechatIdentity.objects.filter(account=other).exists())
