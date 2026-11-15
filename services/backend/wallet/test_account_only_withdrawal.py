"""Real MySQL serialization of a provider with no historical Django user."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens
from wallet.models import Transaction, Wallet, WithdrawalAccount, WithdrawRequest


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class AccountOnlyWithdrawalConcurrencyTest(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_simultaneous_same_request_has_one_debit_one_freeze_and_recoverable_result(self):
        account = ClubAccount.objects.create_account(username='native-withdraw-race', account_type='PROVIDER')
        wallet = Wallet.objects.create(account=account, balance=50000)
        payee = WithdrawalAccount.objects.create(account=account, payee_method='WECHAT',
            payee_account='synthetic-no-external-transfer', payee_name='隔离测试陪玩')
        token, _ = issue_account_tokens(account)
        barrier = Barrier(2)
        payload = {'amount': 10000, 'withdrawal_account_id': payee.pk,
                   'expected_payee_version': payee.version, 'client_request_id': 'same-native-race-key'}

        def submit(_):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token, HTTP_X_STORE_SLUG='shenglang')
                barrier.wait(timeout=10)
                response = client.post('/api/wallet/withdraw/', payload, format='json')
                return response.data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(submit, range(2)))
        self.assertEqual(sorted(response['code'] for response in responses), [0, 409], responses)
        created = next(response for response in responses if response['code'] == 0)
        recovered = next(response for response in responses if response['code'] == 409)
        self.assertEqual(recovered['errorCode'], 'FUND_ALREADY_PROCESSED')
        self.assertEqual(created['data']['request']['id'], recovered['fundResult']['data']['request']['id'])
        wallet.refresh_from_db()
        self.assertIsNone(wallet.user_id)
        self.assertEqual((wallet.balance, wallet.frozen_amount), (40000, 10000))
        withdrawal = WithdrawRequest.objects.get(account=account)
        self.assertIsNone(withdrawal.user_id)
        self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='WITHDRAW').count(), 1)
