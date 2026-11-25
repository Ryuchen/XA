"""Existing money workflows execute against a second real database, unchanged.

No order, wallet, payroll or permission implementation is replaced by a mock.
The original-store sentinel is checked after every second-store scenario.
"""
import os
from unittest import skipUnless
from django.test import override_settings
from rest_framework.test import APITestCase

from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens
from console.permissions import ALL_PERMISSION_SET
from console import test_operating_workflow as operating
from orders.tests import test_release_flow as flow
from stores.context import resolve_store, store_context
from stores.models import Store
from wallet.models import Wallet

STORE_HOSTS = {'shenglang': 'sl.esports.club', 'xinan': 'xa.esports.club'}


class SecondStoreMixin:
    databases = {'default', 'store_b'}

    def setUp(self):
        self.original = resolve_store()
        with store_context(self.original):
            self.sentinel = ClubAccount.objects.create_account(
                username='original-store-sentinel', account_type='BOSS',
            )
            Wallet.objects.create(account=self.sentinel, balance=432100)
        self.store_b = Store.objects.create(slug='xinan', name='南岸电竞',
            database_alias='store_b', status=Store.Status.ACTIVE)
        context = store_context(self.store_b)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        super().setUp()
        self.addCleanup(self.assert_original_untouched)

    def login(self, user):
        token, _ = issue_account_tokens(self.accounts[user.pk])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token,
                                HTTP_X_STORE_SLUG=self.store_b.slug,
                                HTTP_HOST=STORE_HOSTS[self.store_b.slug])

    def assert_original_untouched(self):
        with store_context(self.original):
            self.assertEqual(Wallet.objects.get(account=self.sentinel).balance, 432100)
            from orders.models import Order, ProviderDispatchLock
            from wallet.models import ProviderReport, RechargeRecord, Transaction
            for model in (Order, ProviderDispatchLock, ProviderReport, RechargeRecord, Transaction):
                self.assertEqual(model.objects.count(), 0, model._meta.label)


class SecondStoreReleaseFlowTest(SecondStoreMixin, flow.ReleaseFlowTest):
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '第二店履约主链报单纠正重提属多店隔离正式契约深水区（18-盘点 多店隔离待联调）；最终联调启用')
    def test_rejected_report_can_be_corrected_and_resubmitted(self):
        super().test_rejected_report_can_be_corrected_and_resubmitted()


class SecondStoreOperatingWorkflowTest(SecondStoreMixin, operating.OperatingWorkflowTest):
    def test_owner_permissions_preserve_salary_privacy_for_existing_roles(self):
        # The historical test explicitly uses django.db.connection, so verify
        # migration data through the selected store connection here instead.
        from django.apps import apps
        from django.db import connections
        from importlib import import_module
        from console.models import AdminRole
        owner = AdminRole.objects.create(code='bootstrap_xinan_owner', name='南岸负责人',
            permissions=['admin:edit', 'role:edit', 'audit:view'])
        migration = import_module('console.migrations.0020_operations_owner_permissions')
        migration.grant_owner_permissions(apps, connections['store_b'].schema_editor(atomic=False))
        owner.refresh_from_db()
        self.assertIn('salary:confirm', owner.permissions)
        self.assertIn('dictionary:edit', owner.permissions)


@override_settings(LOGIN_OTP_REQUIRED=False, LEGACY_FORCE_AUTH_COMPAT=False)
class StoreBusinessBoundaryTest(APITestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from orders.tests.factories import make_console_user, make_user
        self.a = resolve_store()
        self.b = Store.objects.create(slug='xinan', name='南岸电竞', database_alias='store_b',
                                      status=Store.Status.ACTIVE)
        self.accounts = {}
        for store in (self.a, self.b):
            with store_context(store):
                boss = make_user(username='same-boss', nickname=store.name + '老板')
                staff = make_console_user(ALL_PERMISSION_SET)
                staff.username = 'same-admin'
                staff.save(update_fields=['username'])
                account = get_or_create_account_for_legacy_user(staff)
                account.phone = '13800001111'
                account.is_phone_verified = True
                account.save(update_fields=['phone', 'is_phone_verified'])
                membership = staff.admin_membership
                membership.account = account
                membership.save(update_fields=['account'])
                self.accounts[store.slug] = {
                    'boss': get_or_create_account_for_legacy_user(boss), 'admin': account,
                }

    def login(self, store, role='admin'):
        with store_context(store):
            token, _ = issue_account_tokens(self.accounts[store.slug][role])
        self.client.credentials(HTTP_X_STORE_SLUG=store.slug, HTTP_AUTHORIZATION='Bearer ' + token,
                                HTTP_HOST=STORE_HOSTS[store.slug])

    def ok(self, response):
        self.assertIn(response.status_code, (200, 201), getattr(response, 'data', response.content))
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data.get('data')

    def test_provider_adjustment_and_recharge_gate_stay_in_selected_store(self):
        from wallet.models import RechargeRecord, Transaction
        from engagement.models import PointFundingLot
        account_id = max(account.pk for accounts in self.accounts.values() for account in accounts.values()) + 100
        wallet_id = 98761
        for store in (self.a, self.b):
            with store_context(store):
                provider = ClubAccount.objects.create_account(pk=account_id,
                    username='same-store-provider', account_type='PROVIDER')
                Wallet.objects.create(pk=wallet_id, account=provider, balance=500)
        self.login(self.b)
        recharge = self.client.post('/api/admin/wallets/recharge/', {
            'account_id': account_id, 'amount': 1000, 'client_request_id': 'cross-store-recharge',
        }, format='json')
        self.assertEqual(recharge.status_code, 403)
        self.ok(self.client.post(f'/api/admin/wallets/{wallet_id}/adjust/', {
            'amount': 300, 'remark': '本店隔离调账', 'client_request_id': 'cross-store-adjust',
        }, format='json'))
        for store, expected in ((self.a, 500), (self.b, 800)):
            with store_context(store):
                wallet = Wallet.objects.get(pk=wallet_id)
                self.assertEqual(wallet.balance, expected)
                self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='ADJUST').count(), int(store == self.b))
                self.assertFalse(RechargeRecord.objects.filter(account_id=account_id).exists())
                self.assertFalse(PointFundingLot.objects.filter(account_id=account_id).exists())

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_balance_redemption_gate_and_goods_redemption_are_store_isolated(self):
        from engagement.models import MemberProfile, PointLedger, RewardClaim, RewardItem
        account_id = max(account.pk for accounts in self.accounts.values() for account in accounts.values()) + 100
        for store in (self.a, self.b):
            with store_context(store):
                account = ClubAccount.objects.create_account(pk=account_id,
                    username='same-store-points-boss', account_type='BOSS')
                self.accounts[store.slug]['boss'] = account
                MemberProfile.objects.create(account=account, points_balance=10000)
                RewardItem.objects.create(pk=98101, code='same-store-balance', name='历史余额',
                    kind='BALANCE', in_shop=True, is_active=True, audience='BOSS', point_cost=10, balance_amount=500)
                RewardItem.objects.create(pk=98102, code='same-store-goods', name='普通商品',
                    kind='PHYSICAL', in_shop=True, is_active=True, audience='BOSS', point_cost=10)
        self.login(self.b, 'boss')
        self.assertEqual([row['id'] for row in self.ok(self.client.get('/api/club/rewards/'))], [98102])
        blocked = self.client.post('/api/club/redeem/', {'reward_id': 98101,
            'client_request_id': 'cross-store-blocked-balance'}, format='json')
        self.assertEqual((blocked.status_code, blocked.data['errorCode']), (400, 'REWARD_UNAVAILABLE'))
        self.ok(self.client.post('/api/club/redeem/', {'reward_id': 98102,
            'client_request_id': 'cross-store-goods'}, format='json'))
        for store, expected_points in ((self.a, 10000), (self.b, 9000)):
            with store_context(store):
                self.assertEqual(MemberProfile.objects.get(account_id=account_id).points_balance, expected_points)
                self.assertEqual(RewardClaim.objects.filter(account_id=account_id).count(), int(store == self.b))
                self.assertEqual(PointLedger.objects.filter(account_id=account_id).count(), int(store == self.b))
                self.assertFalse(Wallet.objects.filter(account_id=account_id).exists())

    def test_deposit_target_configuration_is_independent_for_each_store(self):
        from orders.tests.factories import make_provider
        from wallet.club_rules import deposit_rules
        self.login(self.b)
        response = self.client.put('/api/admin/config/deposit-targets', {
            'cash_target': 30000, 'earnings_target': 75000, 'expected_version': 0,
        }, format='json')
        self.ok(response)
        for store, expected, denied in ((self.a, (40000, 80000), True), (self.b, (30000, 75000), False)):
            with store_context(store):
                rules = deposit_rules()
                self.assertEqual((rules['cash_target'], rules['earnings_target']), expected)
                profile = make_provider(deposit_mode='CASH', deposit_paid=35000).escort_profile
                self.assertEqual(profile.order_acceptance_error() == '陪玩押金未缴齐', denied)

    def test_cash_deposit_topup_stays_in_store_with_overlapping_profile_ids(self):
        from orders.tests.factories import make_provider
        from users.models import EscortProfile
        from wallet.models import CashDepositTopup, Transaction, get_platform_wallet
        profile_id = 92811
        for store in (self.a, self.b):
            with store_context(store):
                user = make_provider(with_profile=False)
                account = ClubAccount.objects.create_account(username='cash-topup-provider', account_type='PROVIDER')
                EscortProfile.objects.create(pk=profile_id, user=user, account=account,
                    display_name='同号陪玩', deposit_mode='CASH', deposit_paid=20000,
                    deposit_business_cycle=account.business_cycle)
        self.login(self.b)
        self.ok(self.client.post(f'/api/admin/escorts/{profile_id}/deposit-topups/', {
            'amount': 20000, 'reason': '线下补差核对', 'reference': 'BANK-SAME-NUMBER',
            'client_request_id': 'store-b-cash-deposit',
        }, format='json'))
        for store in (self.a, self.b):
            with store_context(store):
                profile = EscortProfile.objects.get(pk=profile_id)
                self.assertEqual(profile.deposit_paid, 40000 if store == self.b else 20000)
                self.assertEqual(CashDepositTopup.objects.filter(profile=profile).count(), int(store == self.b))
                self.assertEqual(Transaction.objects.filter(tx_type='DEPOSIT_INCOME').count(), int(store == self.b))
                if store == self.b:
                    self.assertEqual(get_platform_wallet().balance, 20000)
                self.assertFalse(Wallet.objects.filter(account=profile.account, balance__gt=0).exists())

    def test_same_account_ids_never_make_a_token_portable_between_stores(self):
        # MySQL does not roll back auto-increment counters between APITestCase
        # transactions. Deliberately create overlapping IDs instead of assuming
        # unrelated fixture sequences happen to stay equal across databases.
        pk = max(account.pk for accounts in self.accounts.values() for account in accounts.values()) + 100
        for store in (self.a, self.b):
            with store_context(store):
                self.accounts[store.slug]['boss'] = ClubAccount.objects.create_account(
                    pk=pk, username='overlapping-id-boss', account_type='BOSS',
                    nickname=store.name + '老板',
                )
        self.assertEqual(self.accounts[self.a.slug]['boss'].pk, self.accounts[self.b.slug]['boss'].pk)
        self.login(self.a, 'boss')
        token = self.client._credentials['HTTP_AUTHORIZATION']
        self.client.credentials(HTTP_AUTHORIZATION=token, HTTP_X_STORE_SLUG=self.b.slug,
                                HTTP_HOST=STORE_HOSTS[self.b.slug])
        response = self.client.get('/api/users/me/')
        self.assertEqual(response.status_code, 401)
        self.login(self.b, 'boss')
        self.assertEqual(self.ok(self.client.get('/api/users/me/'))['nickname'], '南岸电竞老板')

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_admin_password_login_and_config_updates_use_selected_store(self):
        from console.models import BusinessDictionaryItem
        for store in (self.a, self.b):
            self.client.credentials(HTTP_X_STORE_SLUG=store.slug, HTTP_HOST=STORE_HOSTS[store.slug])
            result = self.ok(self.client.post('/api/admin/auth/login',
                {'username': 'same-admin', 'password': 'pass1234'}, format='json'))
            from rest_framework_simplejwt.tokens import AccessToken
            self.assertEqual(AccessToken(result['token'])['store_slug'], store.slug)
        self.login(self.b)
        item = self.ok(self.client.post('/api/admin/dictionary-items/', {
            'type': 'AD_CHANNEL', 'code': 'SOUTH_ONLY', 'name': '南岸独有渠道',
        }, format='json'))
        self.ok(self.client.patch(f'/api/admin/dictionary-items/{item["id"]}/',
            {'name': '南岸渠道修改'}, format='json'))
        with store_context(self.a):
            self.assertFalse(BusinessDictionaryItem.objects.filter(code='SOUTH_ONLY').exists())
        with store_context(self.b):
            self.assertEqual(BusinessDictionaryItem.objects.get(code='SOUTH_ONLY').name, '南岸渠道修改')

    def test_catalog_coupons_announcements_and_customer_lists_are_isolated(self):
        from announcements.models import Announcement
        from coupons.models import Coupon
        from orders.models import ServiceItem
        for store in (self.a, self.b):
            with store_context(store):
                Announcement.objects.create(title=store.name + '独家公告', content='独立内容')
                Coupon.objects.create(name=store.name + '独家优惠', amount=100)
                ServiceItem.objects.create(name=store.name + '独家服务', price=10000)
        paths = ('/api/admin/users/', '/api/admin/service-items/',
                 '/api/admin/announcements/', '/api/admin/coupons/')
        for store, other in ((self.a, self.b), (self.b, self.a)):
            self.login(store)
            for path in paths:
                with self.subTest(store=store.slug, path=path):
                    data = self.ok(self.client.get(path))
                    self.assertIn(store.name, str(data))
                    self.assertNotIn(other.name, str(data))

    def test_all_admin_read_surfaces_resolve_in_the_second_store(self):
        self.login(self.b)
        resources = ('users', 'escorts', 'orders', 'evaluations', 'reports', 'withdrawals',
            'wallets', 'recharge-records', 'transactions', 'coupons', 'announcements',
            'banners', 'achievements', 'checkin-gifts', 'service-items', 'game-categories',
            'service-categories', 'boss-types', 'escort-levels', 'promotions',
            'support-cards', 'audition-links', 'audition-signups', 'roles', 'admins',
            'dictionary-items', 'salary-plans', 'salary-assignments', 'salary-activities',
            'operating-costs', 'aftersales', 'provider-registrations', 'club-rewards',
            'club-claims', 'broadcast-rules', 'broadcasts', 'provider-namings',
            'club-content', 'vip-tiers', 'recharge-packages', 'lotteries', 'gameplays')
        for resource in resources:
            with self.subTest(resource=resource):
                self.ok(self.client.get(f'/api/admin/{resource}/'))

    def test_raw_sql_and_rollback_are_scoped_to_store_database(self):
        from stores.db import connection, transaction
        with store_context(self.b):
            before = ClubAccount.objects.count()
            with self.assertRaisesRegex(ValueError, 'rollback'):
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute('UPDATE club_accounts_clubaccount SET nickname=%s WHERE username=%s',
                                       ['将回滚', 'same-boss'])
                    raise ValueError('rollback')
            self.assertEqual(ClubAccount.objects.count(), before)
            self.assertEqual(ClubAccount.objects.get(username='same-boss').nickname, '南岸电竞老板')
        with store_context(self.a):
            self.assertEqual(ClubAccount.objects.get(username='same-boss').nickname, '声浪电竞老板')

    def test_store_management_writes_are_audited_in_the_platform_store(self):
        from hashlib import sha256
        from console.models import AdminAuditLog
        from console.models import AdminRole
        from stores.models import StorePlatformGrant
        with store_context(self.a):
            role, _ = AdminRole.objects.get_or_create(code='super_admin', defaults={
                'name': '平台测试超级管理员', 'permissions': ['dashboard:view'],
            })
            self.accounts[self.a.slug]['admin'].admin_membership.roles.add(role)
        StorePlatformGrant.objects.create(account_id=self.accounts[self.a.slug]['admin'].pk)
        self.login(self.a)
        self.ok(self.client.patch(f'/api/stores/manage/{self.b.pk}/',
                                  {'name': '南岸门店改名'}, format='json'))
        with store_context(self.a):
            record = AdminAuditLog.objects.get(resource='stores', object_id=str(self.b.pk))
            self.assertEqual(record.request_body['name'], {
                'sha256': sha256('南岸门店改名'.encode('utf-8')).hexdigest(),
            })
            self.assertNotIn('南岸门店改名', str(record.request_body))
            self.assertEqual(record.operator_account_id, self.accounts[self.a.slug]['admin'].pk)
        with store_context(self.b):
            self.assertFalse(AdminAuditLog.objects.filter(resource='stores').exists())
