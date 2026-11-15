"""门店押金目标配置与实际接单资格使用同一口径。"""
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider
from wallet.club_rules import DEPOSIT_TARGET_KEY, deposit_rules
from wallet.models import SystemConfig


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, PROVIDER_WECHAT_GATE_ENABLED=False)
class DepositTargetsTest(APITestCase):
    def setUp(self):
        staff = make_console_user(['deposit_rule:view', 'deposit_rule:edit'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account
        staff.admin_membership.save(update_fields=['account'])
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        self.provider = make_provider(deposit_mode='CASH', deposit_paid=20000, deposit_required=20000)

    def test_defaults_and_current_target_are_applied_even_to_legacy_profile(self):
        profile = self.provider.escort_profile
        self.assertEqual((deposit_rules()['cash_target'], deposit_rules()['earnings_target']), (40000, 80000))
        self.assertEqual(profile.effective_deposit_required, 40000)
        self.assertEqual(profile.order_acceptance_error(club_rules_enabled=False), '陪玩押金未缴齐')
        profile.deposit_paid = 40000
        profile.save(update_fields=['deposit_paid'])
        self.assertNotEqual(profile.order_acceptance_error(club_rules_enabled=True), '陪玩押金未缴齐')
        profile.deposit_mode = 'EARNINGS'
        self.assertEqual(profile.effective_deposit_required, 80000)

    def test_config_save_conflict_and_original_paid_amount(self):
        before = self.client.get('/api/admin/config/deposit-rules').data['data']
        self.assertEqual(before['version'], 0)
        payload = {'cash_target': 50000, 'earnings_target': 90000, 'expected_version': 0}
        result = self.client.put('/api/admin/config/deposit-targets', payload, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        self.assertEqual((result.data['data']['cash_target'], result.data['data']['earnings_target'],
                          result.data['data']['version']), (50000, 90000, 1))
        self.assertEqual(self.client.put('/api/admin/config/deposit-targets', payload, format='json').status_code, 409)
        profile = self.provider.escort_profile
        profile.refresh_from_db()
        self.assertEqual((profile.deposit_paid, profile.deposit_required, profile.effective_deposit_required),
                         (20000, 20000, 50000))

    def test_invalid_config_and_unprivileged_write_leave_rules_unchanged(self):
        for value in (0, -1, 'bad', 1.5, True, 2147483648):
            response = self.client.put('/api/admin/config/deposit-targets', {
                'cash_target': value, 'earnings_target': 80000, 'expected_version': 0}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(SystemConfig.objects.filter(key=DEPOSIT_TARGET_KEY).exists())
        staff = make_console_user(['deposit_rule:view'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account
        staff.admin_membership.save(update_fields=['account'])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        denied = self.client.put('/api/admin/config/deposit-targets', {
            'cash_target': 40000, 'earnings_target': 80000, 'expected_version': 0}, format='json')
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(SystemConfig.objects.filter(key=DEPOSIT_TARGET_KEY).exists())

    def test_staff_cannot_replace_store_target_with_profile_value(self):
        from users.models import EscortProfile
        staff = make_console_user(['escort:view', 'escort:edit'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account
        staff.admin_membership.save(update_fields=['account'])
        provider_account = get_or_create_account_for_legacy_user(self.provider)
        profile = self.provider.escort_profile
        profile.account = provider_account
        profile.save(update_fields=['account'])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        rejected = self.client.patch(f'/api/admin/escorts/{profile.pk}/', {'deposit_required': 1}, format='json')
        self.assertEqual(rejected.status_code, 400, rejected.data)
        profile.refresh_from_db()
        self.assertEqual((profile.deposit_required, profile.effective_deposit_required), (20000, 40000))
        created = self.client.post('/api/admin/escorts/', {'username': 'new-target-provider',
            'password': 'test-pass-only', 'display_name': '目标测试', 'phone': '13900003001',
            'deposit_mode': 'CASH', 'deposit_required': 1}, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        new_profile = EscortProfile.objects.get(account__username='new-target-provider')
        self.assertEqual((new_profile.deposit_required, new_profile.effective_deposit_required), (40000, 40000))
