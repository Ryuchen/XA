from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider
from wallet.models import IdempotencyKey, SystemConfig, Transaction, WithdrawRequest
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class WithdrawRuleConfigurationTest(APITestCase):
    def setUp(self):
        self.provider = make_provider()
        self.account = get_or_create_account_for_legacy_user(self.provider)
        self.wallet = get_wallet(account=self.account, user=self.provider)
        self.wallet.balance = 50000
        self.wallet.save(update_fields=['balance'])
        staff = make_console_user(['withdraw:view', 'withdraw:config'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        self.provider_client, self.admin = APIClient(), APIClient()
        self.provider_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
        self.admin.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        self.config_url, self.withdraw_url = '/api/admin/config/withdraw', '/api/wallet/withdraw/'

    def configure(self, minimum=1000, threshold=5000, rate=2, version=None):
        data = {'min_amount': minimum, 'balance_threshold': threshold, 'tax_rate': rate}
        if version is not None:
            data['expected_version'] = version
        return self.admin.put(self.config_url, data, format='json')

    def submit(self, amount, **extra):
        return self.provider_client.post(self.withdraw_url, {
            'amount': amount, 'payee_method': 'WECHAT', 'payee_account': 'qa-only',
            'payee_name': '隔离测试', **extra,
        }, format='json')

    def test_minimum_and_balance_threshold_are_independent_even_with_legacy_flag(self):
        SystemConfig.objects.create(key='club_income_rules_enabled', value='1')
        self.assertEqual(self.configure(minimum=10000, threshold=50000).data['code'], 0)
        quote = self.provider_client.get(self.withdraw_url, {'amount': 10000}).data['data']
        self.assertEqual((quote['min_amount'], quote['balance_threshold'], quote['can_withdraw']), (10000, 50000, False))
        self.assertEqual(self.submit(10000).data['code'], 400)
        self.wallet.balance = 50001
        self.wallet.save(update_fields=['balance'])
        self.assertEqual(self.submit(9999).data['code'], 400)
        self.assertEqual(self.submit(10000, client_request_id='strict-threshold').data['code'], 0)
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (40001, 10000))

    def test_zero_payout_is_visible_on_quote_but_never_freezes_or_claims_request(self):
        self.assertEqual(self.configure(minimum=0, threshold=0, rate=50).data['code'], 0)
        quote = self.provider_client.get(self.withdraw_url, {'amount': 1}).data['data']
        self.assertEqual((quote['tax_amount'], quote['actual_amount'], quote['payout_eligible']), (1, 0, False))
        result = self.submit(1, client_request_id='zero-payout')
        self.assertEqual(result.data['errorCode'], 'WITHDRAW_ZERO_PAYOUT')
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (50000, 0))
        self.assertFalse(WithdrawRequest.objects.exists())
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(IdempotencyKey.objects.filter(request_id='zero-payout').exists())
        self.assertEqual(self.configure(minimum=0, threshold=0, rate=0).data['code'], 0)
        self.assertEqual(self.submit(1, client_request_id='zero-payout').data['code'], 0)

    def test_quote_version_detects_changed_threshold_even_if_payout_amount_is_same(self):
        self.configure(minimum=1000, threshold=5000, rate=2)
        quote = self.provider_client.get(self.withdraw_url, {'amount': 10000}).data['data']
        self.configure(minimum=1000, threshold=6000, rate=2)
        result = self.submit(10000, client_request_id='changed-rule',
            expected_config_version=quote['config_version'], expected_tax_rate=quote['tax_rate'],
            expected_tax_amount=quote['tax_amount'], expected_actual_amount=quote['actual_amount'])
        self.assertEqual(result.data['errorCode'], 'WITHDRAW_QUOTE_CHANGED')
        self.assertFalse(WithdrawRequest.objects.exists())
        self.assertFalse(IdempotencyKey.objects.filter(request_id='changed-rule').exists())

    def test_stale_admin_version_and_invalid_values_cannot_overwrite_rules(self):
        initial = self.admin.get(self.config_url).data['data']
        self.assertEqual(self.configure(version=initial['version']).data['code'], 0)
        stale = self.configure(minimum=2000, version=initial['version'])
        self.assertEqual(stale.status_code, 409)
        for changes in ({'minimum': 1.5}, {'threshold': True}, {'rate': 2.5}, {'threshold': 2147483648}):
            self.assertEqual(self.configure(**changes).data['code'], 400)
        current = self.admin.get(self.config_url).data['data']
        self.assertEqual((current['min_amount'], current['balance_threshold'], current['tax_rate']), (1000, 5000, 2))
