"""钱包查找通过真实权限、接口和数据库验证。"""
from django.test import override_settings
from rest_framework.test import APITestCase
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_user, make_provider
from wallet.models import Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class WalletKeywordSearchTest(APITestCase):
    def setUp(self):
        staff = make_console_user(['wallet:view'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account
        staff.admin_membership.save(update_fields=['account'])
        token, _ = issue_account_tokens(account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.boss = make_user(username='qa_alpha_wallet', nickname='同名客户', phone='13800123456')
        self.other = make_user(username='qa_beta_wallet', nickname='同名客户', phone='13900987654')
        self.provider = make_provider()
        self.provider.username = 'qa_provider_wallet'
        self.provider.nickname = '同名客户'
        self.provider.save(update_fields=['username', 'nickname'])
        self.boss_wallet = self.wallet_for(self.boss)
        self.other_wallet = self.wallet_for(self.other)
        self.provider_wallet = self.wallet_for(self.provider)

    def wallet_for(self, user):
        account = get_or_create_account_for_legacy_user(user)
        wallet, _ = Wallet.objects.get_or_create(user=user)
        wallet.account = account
        wallet.save(update_fields=['account'])
        return wallet

    def ids(self, **filters):
        response = self.client.get('/api/admin/wallets/', filters)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['code'], 0)
        return {row['id'] for row in response.data['data']['list']}

    def test_keyword_matches_identity_fields_and_preserves_account_type(self):
        for term in ('qa_alpha', '1380012', self.boss_wallet.account.account_no):
            with self.subTest(term=term):
                self.assertEqual(self.ids(keyword=term), {self.boss_wallet.id})
        self.assertEqual(self.ids(keyword='同名客户'), {self.boss_wallet.id, self.other_wallet.id})
        self.assertEqual(self.ids(account_type='PROVIDER', keyword='同名客户'), {self.provider_wallet.id})
        self.assertEqual(self.ids(account_type='PROVIDER', keyword='qa_alpha'), set())

    def test_advanced_filters_remain_combined_with_keyword(self):
        self.assertEqual(self.ids(keyword='同名客户', phone='1380012'), {self.boss_wallet.id})
        self.assertEqual(self.ids(keyword='qa_alpha', phone='1390098'), set())
        self.assertEqual(self.ids(boss_no=self.other_wallet.account.account_no, nickname='同名'), {self.other_wallet.id})
        self.assertEqual(self.ids(keyword='  qa_alpha  '), {self.boss_wallet.id})

    def test_legacy_wallet_number_without_linked_account(self):
        self.other_wallet.account = None
        self.other_wallet.save(update_fields=['account'])
        self.other.boss_no = 'XALEGACY987'
        self.other.save(update_fields=['boss_no'])
        self.assertEqual(self.ids(keyword='XALEGACY987'), {self.other_wallet.id})
