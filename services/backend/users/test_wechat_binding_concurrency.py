"""A verified phone must not bind to two bosses through concurrent requests."""
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.db import close_old_connections, connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_user
from users.views import _assert_phone_available


@override_settings(WECHAT_MOCK_LOGIN=False, WECHAT_MINIAPP_APPID='test-appid',
                   WECHAT_MINIAPP_SECRET='test-secret', LEGACY_FORCE_AUTH_COMPAT=False)
@skipUnlessDBFeature('has_select_for_update')
class WechatPhoneConcurrencyTest(TransactionTestCase):
    def race(self, first_path, second_path):
        accounts = [get_or_create_account_for_legacy_user(make_user(
            openid='wx-' + code, is_openid_bound=True, phone='',
        )) for code in ('first', 'second')]
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        first_checked = threading.Event()
        resume_first = threading.Event()
        second_done = threading.Event()

        def check(account, phone):
            _assert_phone_available(account, phone)
            if account.pk == accounts[0].pk:
                first_checked.set()
                if not resume_first.wait(5):
                    raise AssertionError('First phone check was not released')

        def send(path, index):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[index])
                return client.post(path, {'code': ('first', 'second')[index],
                                         'phoneCode': 'verified-phone'}, format='json').data
            finally:
                connections.close_all()
                if index == 1:
                    second_done.set()

        with patch('users.views._wechat_code2session', side_effect=lambda code: 'wx-' + code), \
                patch('users.views._wechat_get_phone', return_value='13800009999'), \
                patch('users.views._assert_phone_available', side_effect=check), \
                ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(send, first_path, 0)
            try:
                self.assertTrue(first_checked.wait(5), 'First request never reached phone ownership check')
                second = workers.submit(send, second_path, 1)
                # A correctly serialized request waits until the first commits.
                second_done.wait(0.5)
            finally:
                resume_first.set()
            results = [first.result(timeout=10), second.result(timeout=10)]
        outcomes = [(item['code'], item.get('errorCode')) for item in results]
        self.assertEqual(sum(item['code'] == 0 for item in results), 1, outcomes)
        self.assertEqual(sum(item.get('errorCode') == 'ACCOUNT_CONFLICT' for item in results), 1, outcomes)
        self.assertEqual(ClubAccount.objects.filter(phone='13800009999', is_phone_verified=True).count(), 1)

    def test_two_logged_in_bosses_cannot_bind_the_same_verified_phone(self):
        self.race('/api/users/wechat-bind/', '/api/users/wechat-bind/')

    def test_login_and_binding_share_the_same_phone_ownership_gate(self):
        self.race('/api/users/wechat-login/', '/api/users/wechat-bind/')
