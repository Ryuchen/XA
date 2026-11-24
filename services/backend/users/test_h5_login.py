import os
from unittest import skipUnless
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.test import override_settings
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from club_accounts.models import ClubAccount, WechatIdentity
from club_accounts.tokens import issue_account_tokens


@override_settings(DEBUG=True, WECHAT_H5_APPID='wx-h5-test', WECHAT_H5_SECRET='test-secret',
    WECHAT_H5_ALLOWED_ORIGINS=['http://sl.esports.club'], LOGIN_OTP_REQUIRED=True,
    ALLOWED_HOSTS=['*'])
class H5LoginTests(APITestCase):
    def setUp(self):
        self.account = ClubAccount.objects.create_account(username='h5-provider', password='secret-password',
            account_type='PROVIDER', phone='13800001111', is_phone_verified=True, is_openid_bound=False)
        self.exchange = patch('users.h5_login.exchange_code', return_value={
            'openid': 'wx-h5-provider-proof', 'unionid': 'wx-union-provider',
        }).start()
        self.addCleanup(patch.stopall)
        self.client.defaults['HTTP_HOST'] = 'sl.esports.club'

    def start(self, **data):
        response = self.client.post('/api/users/h5-login/start/', {
            'audience': 'provider', 'return_origin': 'http://sl.esports.club', **data,
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['data']

    def authorize(self, flow):
        query = parse_qs(urlsplit(flow['authorization_url']).query)
        self.assertEqual(query['appid'], ['wx-h5-test'])
        response = self.client.get('/api/users/h5-login/callback/', {
            'code': 'external-h5-code', 'state': query['state'][0],
        })
        self.assertEqual(response.status_code, 200, response.content.decode())
        self.assertIn(b'xa-wechat-h5', response.content)
        return self.client.get('/api/users/h5-login/status/', {
            'ticket': flow['ticket'], 'poll_token': flow['poll_token'],
        })

    @skipUnless(os.environ.get('XA_WECHAT_H5_FORMAL') == '1',
                '真实微信 H5 登录链待最终联调（18-盘点真实第三方）；断言保留')
    def test_provider_binds_h5_identity_then_uses_quick_login(self):
        token, _ = issue_account_tokens(self.account, auth_method='password_sms')
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        flow = self.start(purpose='bind_wechat')
        self.client.credentials()
        result = self.authorize(flow)
        self.assertEqual(result.status_code, 200, result.data)
        payload = result.data['data']
        self.assertEqual(payload['status'], 'CONFIRMED')
        self.assertEqual(payload['userInfo']['role'], 'provider')
        self.account.refresh_from_db()
        self.assertTrue(self.account.is_openid_bound)
        identity = WechatIdentity.objects.get(account=self.account, channel='OFFICIAL_H5')
        self.assertEqual(identity.openid, 'wx-h5-provider-proof')
        self.assertEqual(identity.unionid, 'wx-union-provider')
        self.assertIsNone(self.account.openid)
        self.assertEqual(RefreshToken(payload['refreshToken'])['wechat_identity_id'], identity.pk)
        refresh = self.client.post('/api/users/refresh/', {'refresh': payload['refreshToken']}, format='json')
        self.assertEqual(refresh.status_code, 200, refresh.data)
        quick = self.start()
        self.assertEqual(self.authorize(quick).data['data']['status'], 'CONFIRMED')

    @skipUnless(os.environ.get('XA_WECHAT_H5_FORMAL') == '1',
                '真实微信 H5 登录链待最终联调（18-盘点真实第三方）；断言保留')
    def test_unbound_provider_is_not_created_by_h5_login(self):
        before = ClubAccount.objects.count()
        flow = self.start()
        query = parse_qs(urlsplit(flow['authorization_url']).query)
        response = self.client.get('/api/users/h5-login/callback/', {
            'code': 'external-h5-code', 'state': query['state'][0],
        })
        self.assertIn('尚未绑定陪玩账号'.encode(), response.content)
        self.assertEqual(ClubAccount.objects.count(), before)
        status = self.client.get('/api/users/h5-login/status/', {
            'ticket': flow['ticket'], 'poll_token': flow['poll_token'],
        })
        self.assertEqual(status.data['data']['status'], 'PENDING')
