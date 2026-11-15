"""Real store-local UnionID ownership race; only WeChat transport is substituted."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from users import wechat_identity
from users.web_login_models import ProviderWechatUnionOwner
from wallet.test_deposit_lifecycle_mysql import require_isolated


@skipUnless(connection.vendor == 'mysql' and 'store_b' in connections, 'Requires isolated two-store MySQL')
@override_settings(DEBUG=True, WECHAT_MOCK_LOGIN=False, WECHAT_MINIAPP_APPID='wx-mini-test',
    WECHAT_MINIAPP_SECRET='secret', WECHAT_H5_APPID='wx-h5-test', WECHAT_H5_SECRET='secret',
    WECHAT_H5_ALLOWED_ORIGINS=['http://xa.esports.club', 'http://sl.esports.club'], ALLOWED_HOSTS=['*'])
class ProviderWechatIdentityMySQLTests(TransactionTestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        require_isolated(self)
        self.store = xinan_store()
        self.other_store = resolve_store()
        with store_context(self.store):
            self.first = ClubAccount.objects.create_account(username='cross-mini', password='password',
                account_type='PROVIDER', phone='13800001111', is_phone_verified=True)
            self.second = ClubAccount.objects.create_account(username='cross-h5', password='password',
                account_type='PROVIDER', phone='13800001112', is_phone_verified=True)
            self.token1, _ = issue_account_tokens(self.first, auth_method='password_sms')
            self.token2, _ = issue_account_tokens(self.second, auth_method='password_sms')

    def start(self, path, data, token):
        client = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        response = client.post(path, data, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['data']

    def test_two_channels_compete_for_one_union_owner(self):
        with patch('users.web_login.create_bridge_code', return_value=('data:image/png;base64,test', '')):
            mini = self.start('/api/users/web-login/start/', {'audience': 'provider', 'purpose': 'bind_wechat'}, self.token1)
        h5 = self.start('/api/users/h5-login/start/', {'audience': 'provider', 'purpose': 'bind_wechat',
            'return_origin': 'http://xa.esports.club'}, self.token2)
        state = parse_qs(urlsplit(h5['authorization_url']).query)['state'][0]
        with patch('users.views._wechat_http_get', return_value={'openid': 'mini-proof', 'unionid': 'same-union'}):
            authorization = APIClient(HTTP_HOST='xa.esports.club').post('/api/users/web-login/authorize/',
                {'ticket': mini['ticket'], 'code': 'code'}, format='json')
        self.assertEqual(authorization.status_code, 200, authorization.data)
        barrier = Barrier(2)
        claim = wechat_identity.claim_provider_unionid

        def synchronize(account, unionid):
            barrier.wait(timeout=15)
            return claim(account, unionid)

        def run_mini():
            close_old_connections()
            try:
                return APIClient(HTTP_HOST='xa.esports.club').post('/api/users/web-login/confirm/', {
                    'ticket': mini['ticket'], 'bridge_token': authorization.data['data']['bridge_token'],
                }, format='json')
            finally:
                close_old_connections()

        def run_h5():
            close_old_connections()
            try:
                return APIClient(HTTP_HOST='xa.esports.club').get('/api/users/h5-login/callback/',
                    {'state': state, 'code': 'code'})
            finally:
                close_old_connections()

        with patch('users.wechat_identity.claim_provider_unionid', side_effect=synchronize), \
                patch('users.h5_login.exchange_code', return_value={'openid': 'h5-proof', 'unionid': 'same-union'}):
            with ThreadPoolExecutor(max_workers=2) as pool:
                a, b = pool.submit(run_mini), pool.submit(run_h5)
                mini_response, h5_response = a.result(timeout=45), b.result(timeout=45)
        h5_success = '微信授权已完成'.encode() in h5_response.content
        self.assertEqual(int(mini_response.status_code == 200) + int(h5_success), 1)
        with store_context(self.store):
            self.assertEqual(ProviderWechatUnionOwner.objects.filter(unionid='same-union').count(), 1)
        with store_context(self.other_store):
            self.assertFalse(ProviderWechatUnionOwner.objects.filter(unionid='same-union').exists())
            other = ClubAccount.objects.create_account(username='other-store-provider', password='password',
                account_type='PROVIDER', phone='13800001113', is_phone_verified=True)
            other_token, _ = issue_account_tokens(other, auth_method='password_sms')
        client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + other_token)
        flow = client.post('/api/users/h5-login/start/', {'audience': 'provider', 'purpose': 'bind_wechat',
            'return_origin': 'http://sl.esports.club'}, format='json')
        self.assertEqual(flow.status_code, 200, flow.data)
        other_state = parse_qs(urlsplit(flow.data['data']['authorization_url']).query)['state'][0]
        with patch('users.h5_login.exchange_code', return_value={'openid': 'h5-proof', 'unionid': 'same-union'}):
            result = APIClient(HTTP_HOST='sl.esports.club').get('/api/users/h5-login/callback/',
                {'state': other_state, 'code': 'code'})
        self.assertIn('微信授权已完成'.encode(), result.content)
        with store_context(self.other_store):
            self.assertEqual(ProviderWechatUnionOwner.objects.get(unionid='same-union').account_id, other.pk)
