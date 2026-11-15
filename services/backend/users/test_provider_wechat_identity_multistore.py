from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from users.web_login_models import ProviderWechatUnionOwner


@override_settings(DEBUG=True, WECHAT_H5_APPID='wx-h5-test', WECHAT_H5_SECRET='test-secret',
    WECHAT_H5_ALLOWED_ORIGINS=['http://sl.esports.club', 'http://xa.esports.club'], ALLOWED_HOSTS=['*'])
class ProviderWechatIdentityStoreTests(TransactionTestCase):
    databases = {'default', 'store_b'}

    def test_same_wechat_independently_bound_in_each_store(self):
        for store, host in ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club')):
            with store_context(store):
                account = ClubAccount.objects.create_account(username='same-name', password='test-password',
                    account_type='PROVIDER', phone='13800001111', is_phone_verified=True)
                token, _ = issue_account_tokens(account, auth_method='password_sms')
            client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token)
            flow = client.post('/api/users/h5-login/start/', {'audience': 'provider', 'purpose': 'bind_wechat',
                'return_origin': 'http://' + host}, format='json')
            self.assertEqual(flow.status_code, 200, flow.data)
            state = parse_qs(urlsplit(flow.data['data']['authorization_url']).query)['state'][0]
            with patch('users.h5_login.exchange_code', return_value={'openid': 'same-openid', 'unionid': 'same-union'}):
                result = APIClient(HTTP_HOST=host).get('/api/users/h5-login/callback/', {'state': state, 'code': 'code'})
            self.assertIn('微信授权已完成'.encode(), result.content)
            with store_context(store):
                self.assertEqual(ProviderWechatUnionOwner.objects.get(unionid='same-union').account_id, account.pk)
