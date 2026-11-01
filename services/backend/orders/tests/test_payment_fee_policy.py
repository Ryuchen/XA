from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import PaymentFeePolicy, PaymentFeePolicyHistory
from orders.tests.factories import make_console_user

URL = '/api/admin/config/payment-fee/'


def payload(revision=0, **changes):
    return {'revision': revision, 'merchant_id': '1234567890', 'appid': 'wx1234567890abcdef',
            'rate_percent': '0.6', 'fee_basis': 'PAYMENT_TOTAL', 'rounding': 'UP', **changes}


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class PaymentFeePolicyTest(APITestCase):
    def setUp(self):
        self.users = [make_console_user(perms) for perms in ([], ['payment_fee:view'],
            ['payment_fee:edit'], ['payment_fee:view', 'payment_fee:edit'])]
        self.accounts = []
        for user in self.users:
            account = get_or_create_account_for_legacy_user(user)
            user.admin_membership.account = account
            user.admin_membership.save(update_fields=['account'])
            self.accounts.append(account)

    def use(self, index):
        token, _ = issue_account_tokens(self.accounts[index])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def test_permissions_versions_history_and_preview(self):
        self.use(0)
        self.assertEqual(self.client.get(URL).status_code, 403)
        self.use(1)
        self.assertEqual(self.client.get(URL).data['data'], {'revision': 0, 'configured': False})
        self.assertEqual(self.client.put(URL, payload(), format='json').status_code, 403)
        self.use(2)
        self.assertEqual(self.client.put(URL, payload(), format='json').status_code, 403)
        self.use(3)
        self.assertEqual(self.client.post(URL + 'preview/', {'service_fen': 10000}, format='json').status_code, 409)
        first = self.client.put(URL, payload(), format='json')
        self.assertEqual(first.data['data']['rate_percent'], '0.6000')
        self.assertEqual(first.data['data']['revision'], 1)
        preview = self.client.post(URL + 'preview/', {'service_fen': 10000}, format='json').data['data']
        self.assertEqual(preview, {'revision': 1, 'service_fen': 10000, 'fee_fen': 61, 'total_fen': 10061})
        self.assertEqual(self.client.put(URL, payload(), format='json').status_code, 409)
        self.assertEqual(self.client.put(URL, payload(1, rate_percent='0.25'), format='json').data['data']['revision'], 2)
        history = self.client.get(URL + 'history/?page=1&page_size=1').data['data']
        self.assertEqual(history['total'], 2)
        self.assertEqual(history['list'][0]['rate_percent'], '0.2500')
        self.assertEqual(PaymentFeePolicyHistory.objects.get(revision=1).rate_millionths, 6000)

    def test_bad_inputs_and_atomic_history(self):
        self.use(3)
        for bad in ('-1', '01', '0.12345', '101', 'NaN', 0.6, None):
            self.assertEqual(self.client.put(URL, payload(rate_percent=bad), format='json').status_code, 400, bad)
        for change in ({'merchant_id': 'bad'}, {'appid': ''}, {'revision': True},
                       {'fee_basis': 'PAYMENT_TOTAL', 'rate_percent': '100'}, {'rounding': ''}, {'secret': 'x'}):
            self.assertEqual(self.client.put(URL, payload(**change), format='json').status_code, 400, change)
        with patch('orders.payment_fee_policy_views.PaymentFeePolicyHistory.objects.create', side_effect=RuntimeError('history failure')):
            with self.assertRaises(RuntimeError):
                self.client.put(URL, payload(), format='json')
        self.assertFalse(PaymentFeePolicy.objects.exists())
        self.assertFalse(PaymentFeePolicyHistory.objects.exists())
        self.assertEqual(self.client.put(URL, payload(rate_percent='0', fee_basis='SERVICE_AMOUNT'), format='json').status_code, 200)
        self.assertEqual(self.client.post(URL + 'preview/', {'service_fen': 999}, format='json').data['data']['fee_fen'], 0)
        self.assertEqual(self.client.post(URL + 'preview/', {'service_fen': True}, format='json').status_code, 400)


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class PaymentFeePolicyConcurrencyTest(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_writers_cannot_overwrite_same_revision(self):
        user = make_console_user(['payment_fee:view', 'payment_fee:edit'])
        account = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = account
        user.admin_membership.save(update_fields=['account'])
        token, _ = issue_account_tokens(account)
        barrier = Barrier(2)

        def put(rate):
            close_old_connections()
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
            barrier.wait(timeout=10)
            try:
                return client.put(URL, payload(rate_percent=rate), format='json').status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(put, ['0.6', '0.5']))
        self.assertEqual(sorted(results), [200, 409])
        self.assertEqual(PaymentFeePolicyHistory.objects.count(), 1)
