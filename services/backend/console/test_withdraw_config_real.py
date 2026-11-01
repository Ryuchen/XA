from uuid import uuid4

from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_escort_profile, make_user, make_wallet
from users.models import CustomUser


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class WithdrawConfigRealTest(APITestCase):
    def client_for(self, account):
        client = APIClient()
        token, _ = issue_account_tokens(account)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        return client

    def test_rule_changes_do_not_rewrite_real_withdrawal_snapshot(self):
        staff = make_console_user(['withdraw:view', 'withdraw:audit'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        admin = self.client_for(staff_account)
        config = '/api/admin/config/withdraw'
        first = admin.put(config, {'min_amount': 10000, 'tax_rate': 2}, format='json')
        self.assertEqual(first.data['code'], 0, first.data)
        provider = make_user(role=CustomUser.Role.PROVIDER)
        make_escort_profile(provider)
        account = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = account
        provider.escort_profile.save(update_fields=['account'])
        make_wallet(provider, balance=50000)
        submitted = self.client_for(account).post('/api/wallet/withdraw/', {
            'amount': 20000, 'payee_method': 'WECHAT', 'payee_account': 'LOCAL_TEST_ONLY',
            'payee_name': '隔离收款人', 'client_request_id': str(uuid4()),
        }, format='json')
        self.assertEqual(submitted.data['code'], 0, submitted.data)
        withdrawal_id = submitted.data['data']['request']['id']
        changed = admin.put(config, {'min_amount': 15000, 'tax_rate': 3}, format='json')
        self.assertEqual(changed.data['code'], 0, changed.data)
        self.assertEqual(admin.get(config).data['data'], {'min_amount': 15000, 'tax_rate': 3})
        details = admin.get(f'/api/admin/withdrawals/{withdrawal_id}/').data['data']
        self.assertEqual((details['status'], details['tax_rate'], details['tax_amount'], details['actual_amount']),
                         ('PENDING', 2, 400, 19600))
