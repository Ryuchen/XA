from datetime import timedelta
from django.db import transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from orders.tests.factories import make_provider, make_console_user
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership
from wallet.club_rules import DEPOSIT_PERCENT_KEY, deposit_rules
from wallet.income import hold_provider_income, release_due_income
from wallet.models import Wallet, Transaction, IncomeHold, SystemConfig, get_platform_wallet


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class DepositRatioTests(APITestCase):
    def setUp(self):
        SystemConfig.objects.create(key='club_income_rules_enabled', value='1')
        self.user = make_provider(deposit_mode='EARNINGS', deposit_paid=0)
        self.profile = self.user.escort_profile
        self.wallet = self.user.wallet
        self.wallet.balance = 0
        self.wallet.save(update_fields=['balance'])

    def hold(self, amount):
        with transaction.atomic():
            self.wallet.refresh_from_db()
            before = self.wallet.balance
            self.wallet.balance += amount
            self.wallet.save(update_fields=['balance'])
            income = Transaction.objects.create(wallet=self.wallet, amount=amount, tx_type='INCOME',
                balance_before=before, balance_after=self.wallet.balance)
            self.profile.refresh_from_db()
            return hold_provider_income(income, profile=self.profile, hold_hours=72)

    def admin(self, perms):
        user = make_console_user(perms=perms)
        account = get_or_create_account_for_legacy_user(user)
        AdminMembership.objects.filter(user=user).update(account=account)
        token, _ = issue_account_tokens(account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer '+token)

    def test_default_double_target_and_six_four_split_conserve_every_cent(self):
        self.assertEqual(deposit_rules()['earnings_target'], 2 * deposit_rules()['cash_target'])
        hold = self.hold(10001)
        self.assertEqual((hold.deposit_amount, hold.amount),(6000,4001))
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance,self.wallet.frozen_amount,self.profile.deposit_paid),(0,4001,6000))
        self.assertEqual(get_platform_wallet().balance,6000)

    def test_configuration_affects_future_income_preserves_existing_holds(self):
        self.admin(['deposit_rule:view','deposit_rule:edit'])
        old = self.hold(10000)
        response = self.client.put('/api/admin/config/deposit-rules',{'deposit_percent':25},format='json')
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(response.data['data']['settlement_percent'],75)
        new = self.hold(10000); old.refresh_from_db()
        self.assertEqual((old.deposit_amount,old.amount,new.deposit_amount,new.amount),(6000,4000,2500,7500))
        release_due_income(now=timezone.now()+timedelta(hours=73))
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance,self.wallet.frozen_amount),(11500,0))

    def test_remaining_deposit_cap_and_completed_target_route_all_income_to_settlement(self):
        self.profile.deposit_paid = 39950
        self.profile.save(update_fields=['deposit_paid'])
        first = self.hold(1000)
        self.assertEqual((first.deposit_amount,first.amount),(50,950))
        second = self.hold(1000)
        self.assertEqual((second.deposit_amount,second.amount),(0,1000))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.deposit_paid,40000)

    def test_zero_and_full_percent_edges_and_cash_mode_do_not_lose_money(self):
        SystemConfig.objects.create(key=DEPOSIT_PERCENT_KEY,value='0')
        hold = self.hold(10000)
        self.assertEqual((hold.deposit_amount,hold.amount),(0,10000))
        SystemConfig.objects.filter(key=DEPOSIT_PERCENT_KEY).update(value='100')
        self.assertIsNone(self.hold(10000))
        self.assertEqual(IncomeHold.objects.count(),1)
        self.profile.deposit_mode='CASH'
        self.profile.save(update_fields=['deposit_mode'])
        hold=self.hold(10000)
        self.assertEqual((hold.deposit_amount,hold.amount),(0,10000))
        self.wallet.refresh_from_db();self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance,self.wallet.frozen_amount,self.profile.deposit_paid),(0,20000,10000))

    def test_config_permissions_and_invalid_values(self):
        url='/api/admin/config/deposit-rules'
        self.assertIn(self.client.get(url).status_code,(401,403))
        self.admin(['deposit_rule:view'])
        self.assertEqual(self.client.get(url).status_code,200)
        self.assertEqual(self.client.put(url,{'deposit_percent':10},format='json').status_code,403)
        self.admin(['deposit_rule:edit'])
        for value in [-1,101,'invalid',25.5,None]:
            self.assertEqual(self.client.put(url,{'deposit_percent':value},format='json').status_code,400)
        self.assertFalse(SystemConfig.objects.filter(key=DEPOSIT_PERCENT_KEY).exists())

    def test_public_rules_and_corrupt_config_fall_back_safely(self):
        SystemConfig.objects.create(key=DEPOSIT_PERCENT_KEY,value='not-a-ratio')
        response=self.client.get('/api/provider-registration/rules/')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data['data']['deposit_percent'],60)
        self.assertFalse(any('secret' in key for key in response.data['data']))


class DepositRulePermissionMigrationTests(APITestCase):
    def test_existing_full_owners_gain_new_features_without_expanding_other_staff(self):
        from importlib import import_module
        from django.apps import apps
        from console.models import AdminRole
        owner=AdminRole.objects.create(name='原初始化管理',code='bootstrap_test',permissions=['role:edit','admin:edit','audit:view'])
        restricted=AdminRole.objects.create(name='受限初始化管理',code='bootstrap_limited',permissions=['escort:view'])
        reviewer=AdminRole.objects.create(name='普通运营',code='restricted_operator',permissions=['escort:view','report:audit'])
        module=import_module('console.migrations.0010_registration_deposit_permissions')
        module.grant_owner_permissions(apps,None)
        owner.refresh_from_db();restricted.refresh_from_db();reviewer.refresh_from_db()
        self.assertTrue(module.FEATURE_PERMISSIONS.issubset(set(owner.permissions)))
        self.assertEqual(restricted.permissions,['escort:view'])
        self.assertEqual(reviewer.permissions,['escort:view','report:audit'])
