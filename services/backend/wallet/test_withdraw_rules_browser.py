import os
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider
from wallet.models import SystemConfig, WithdrawalAccount, WithdrawRequest
from wallet.services import get_wallet


@skipUnless(os.environ.get('XA_WITHDRAW_RULES_BROWSER') == '1', 'Requires isolated ASGI and provider Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class WithdrawalRulesBrowserTest(LiveServerTestCase):
    def test_zero_payout_then_updated_rule_on_provider_page(self):
        for key, value in [('min_withdraw_amount', '0'), ('withdraw_balance_threshold', '0'), ('withdraw_tax_rate', '100')]:
            SystemConfig.objects.update_or_create(key=key, defaults={'value':value})
        provider, staff = make_provider(), make_console_user(['withdraw:view', 'withdraw:config'])
        account, admin = [get_or_create_account_for_legacy_user(user) for user in (provider, staff)]
        link_legacy_relations(provider, account); link_legacy_relations(staff, admin)
        staff.admin_membership.account = admin
        staff.admin_membership.save(update_fields=['account'])
        wallet = get_wallet(account=account, user=provider)
        wallet.balance = 10000
        wallet.save(update_fields=['balance'])
        payee = WithdrawalAccount.objects.create(account=account, payee_method='WECHAT',
                                                  payee_account='qa-only', payee_name='隔离测试')
        provider_client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        staff_client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(admin)[0])
        quote = provider_client.get('/api/wallet/withdraw/', {'amount': 1000})
        self.assertEqual(quote.data['code'], 0, quote.data)
        self.assertFalse(quote.data['data']['payout_eligible'])
        blocked = provider_client.post('/api/wallet/withdraw/', {'amount': 1000, 'withdrawal_account_id': payee.pk,
            'expected_payee_version': payee.version, 'client_request_id': 'withdraw-rule-zero'}, format='json')
        self.assertEqual(blocked.data['code'], 400, blocked.data)
        self.assertEqual(blocked.data['errorCode'], 'WITHDRAW_ZERO_PAYOUT')
        updated = staff_client.put('/api/admin/config/withdraw', {'min_amount': 0, 'balance_threshold': 0, 'tax_rate': 2}, format='json')
        self.assertEqual(updated.data['code'], 0, updated.data)
        created = provider_client.post('/api/wallet/withdraw/', {'amount': 1000, 'withdrawal_account_id': payee.pk,
            'expected_payee_version': payee.version, 'client_request_id': 'withdraw-rule-live'}, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        wallet.refresh_from_db()
        request = WithdrawRequest.objects.get(account=account)
        self.assertEqual((request.amount, request.tax_rate, request.actual_amount), (1000, 2, 980))
        self.assertEqual((wallet.balance, wallet.frozen_amount), (9000, 1000))
