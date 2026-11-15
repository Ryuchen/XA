from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_provider
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from wallet.models import SystemConfig
from wallet.services import get_wallet


@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False)
class WithdrawRulesStoreIsolationTest(TestCase):
    databases = {'default', 'store_b'}

    def test_quotes_and_submit_use_their_own_store_rules(self):
        stores = [(resolve_store(), 'sl.esports.club', 1000, 5000, 2),
                  (xinan_store(), 'xa.esports.club', 3000, 12000, 5)]
        tokens = []
        for store, _, minimum, threshold, rate in stores:
            with store_context(store):
                for key, value in [('min_withdraw_amount', minimum), ('withdraw_balance_threshold', threshold),
                                   ('withdraw_tax_rate', rate)]:
                    SystemConfig.objects.update_or_create(key=key, defaults={'value': str(value)})
                provider = make_provider()
                account = get_or_create_account_for_legacy_user(provider)
                wallet = get_wallet(account=account, user=provider)
                wallet.balance = 20000
                wallet.save(update_fields=['balance'])
                tokens.append(issue_account_tokens(account)[0])
        client = APIClient()
        versions = []
        for (_, host, minimum, threshold, rate), token in zip(stores, tokens):
            data = client.get('/api/wallet/withdraw/', {'amount': 4000}, HTTP_HOST=host,
                              HTTP_AUTHORIZATION='Bearer ' + token).data['data']
            self.assertEqual((data['min_amount'], data['balance_threshold'], data['tax_rate']),
                             (minimum, threshold, rate))
            versions.append(data['config_version'])
        self.assertNotEqual(versions[0], versions[1])
        mismatch = client.post('/api/wallet/withdraw/', {
            'amount': 4000, 'payee_method': 'WECHAT', 'payee_account': 'qa-only',
            'payee_name': '隔离测试', 'client_request_id': 'different-store-version',
            'expected_config_version': versions[0],
        }, format='json', HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + tokens[1])
        self.assertEqual(mismatch.data['errorCode'], 'WITHDRAW_QUOTE_CHANGED')
        with store_context(stores[1][0]):
            self.assertEqual(get_wallet(user=provider).balance, 20000)
