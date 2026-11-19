from unittest import skipUnless
from io import StringIO
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections as django_connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership, AdminRole

from .context import current_database, current_store, resolve_store, store_context
from .db import connection, connections, transaction
from .models import Store, StorePlatformGrant
from .permissions import may_manage_stores
from .provisioning import adapt_initial_branding


def store_b():
    return Store.objects.create(slug='branch-b', name='第二门店', database_alias='store_b',
                                status=Store.Status.ACTIVE, initialized_at=timezone.now())


def admin(username='store-admin', *, super_admin=False):
    user = get_user_model().objects.create_user(username=username, password='test-password', role='ADMIN')
    account = get_or_create_account_for_legacy_user(user)
    if not account.phone:
        account.phone = f'139{account.pk:08d}'
        account.save(update_fields=['phone'])
    role = AdminRole.objects.create(code=f'foundation_{account.pk}', name=f'基础测试 {account.pk}',
                                    permissions=['dashboard:view'])
    membership = AdminMembership.objects.create(user=user, account=account)
    membership.roles.add(role)
    if super_admin:
        super_role, _ = AdminRole.objects.get_or_create(code='super_admin', defaults={
            'name': '平台测试超级管理员', 'permissions': ['dashboard:view'],
        })
        membership.roles.add(super_role)
    return account


class StoreContextTests(TestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.a = resolve_store()
        self.b = store_b()

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_nested_context_routes_and_cleans_up(self):
        self.assertIsNone(current_store())
        with store_context(self.b):
            self.assertEqual(current_database(), 'store_b')
            self.assertIs(connection.cursor().db, django_connections['store_b'])
            self.assertIs(connections['default'], django_connections['store_b'])
            with store_context(self.a):
                self.assertEqual(current_database(), 'default')
            self.assertEqual(current_database(), 'store_b')
        self.assertEqual(current_database(), 'default')
        self.assertIsNone(current_store())

    def test_directory_always_lives_on_default_database(self):
        with store_context(self.b):
            self.assertEqual(Store.objects.count(), 2)
            self.b.name = '门店乙'
            self.b.save()
            self.assertEqual(self.b._state.db, 'default')
        self.assertEqual(Store.objects.get(pk=self.b.pk).name, '门店乙')
        self.assertNotIn('stores_store', django_connections['store_b'].introspection.table_names())
        with self.assertRaisesMessage(ValueError, '仅允许访问平台数据库'):
            Store.objects.using('store_b').count()

    def test_business_accounts_can_overlap_and_never_leak(self):
        a = ClubAccount.objects.create_account(username='shared-login', account_type='BOSS', nickname='店甲老板')
        with store_context(self.b):
            b = ClubAccount.objects.create_account(username='shared-login', account_type='BOSS', nickname='店乙老板')
            self.assertEqual(ClubAccount.objects.get(username='shared-login').nickname, '店乙老板')
            self.assertEqual(b._state.db, 'store_b')
            with self.assertRaisesMessage(ValueError, '禁止跨门店'):
                a.save()
        self.assertEqual(ClubAccount.objects.get(username='shared-login').nickname, '店甲老板')


class StoreMiddlewareTests(TestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.b = store_b()
        self.client = APIClient()

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_current_store_and_public_directory_boundary(self):
        self.b.boss_url = 'https://boss.example.com/?campaign=x#/orders'
        self.b.save()
        response = self.client.get('/api/stores/current/', HTTP_X_STORE_SLUG=self.b.slug)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['name'], '第二门店')
        self.assertIn('store=branch-b#/orders', response.data['data']['boss_url'])
        self.assertFalse(response.data['data']['can_manage_stores'])
        self.assertNotIn('database_alias', response.data['data'])
        self.assertIsNone(current_store())
        self.assertEqual(self.client.get('/api/stores/current/').data['data']['slug'], 'shenglang')

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_unknown_disabled_and_conflicting_store_fail_closed(self):
        self.assertEqual(self.client.get('/api/stores/current/?store=unknown').status_code, 404)
        self.assertEqual(self.client.get('/api/stores/current/?store=branch-b', HTTP_X_STORE_SLUG='shenglang').status_code, 400)
        self.assertEqual(self.client.get('/api/stores/current/?store=branch-b&store=shenglang').status_code, 400)
        self.b.status = Store.Status.DISABLED
        self.b.save()
        self.assertEqual(self.client.get('/api/stores/current/?store=branch-b').status_code, 403)
        self.assertIsNone(current_store())

    def test_platform_permission_is_not_a_tenant_admin_role(self):
        original = admin(super_admin=True)
        with store_context(self.b):
            branch_admin = admin(super_admin=True)
            self.assertFalse(may_manage_stores(branch_admin))
        token, _ = issue_account_tokens(original)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(self.client.get('/api/stores/manage/').status_code, 403)
        StorePlatformGrant.objects.create(account_id=original.pk)
        self.assertEqual(self.client.get('/api/stores/manage/').status_code, 200)
        self.assertTrue(self.client.get('/api/stores/current/').data['data']['can_manage_stores'])
        with store_context(self.b):
            self.assertFalse(may_manage_stores(branch_admin))

    @override_settings(STORE_DATABASE_ALIASES=('store_b',))
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_store_registration_stays_unavailable_until_initialized(self):
        # Reserve the configured isolated DB through the real platform endpoint.
        self.b.delete()
        account = admin(super_admin=True)
        StorePlatformGrant.objects.create(account_id=account.pk)
        token, _ = issue_account_tokens(account)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        response = self.client.post('/api/stores/manage/', {
            'slug': 'new-branch', 'name': '新门店', 'database_alias': 'store_b',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        store_id = response.data['data']['id']
        self.assertEqual(response.data['data']['status'], 'PROVISIONING')
        self.assertEqual(self.client.patch(f'/api/stores/manage/{store_id}/', {'status': 'ACTIVE'}, format='json').status_code, 400)
        self.client.credentials()
        self.assertEqual(self.client.get('/api/stores/current/?store=new-branch').status_code, 403)


class StoreInitialBrandingTests(TestCase):
    databases = {'default', 'store_b'}

    def test_first_initialization_changes_only_seed_display_and_never_money_or_codes(self):
        from console.dictionary_models import BusinessDictionaryItem
        from console.payroll_models import SalaryPlan
        from console.test_operating_workflow import seed_payroll_fixture
        seed_payroll_fixture()
        branch = store_b()
        branch.initialized_at = None
        branch.name = '南岸电竞'
        branch.save()
        original = BusinessDictionaryItem.objects.get(code='EXAMINER_SHENGLANG')
        with store_context(branch):
            seed_payroll_fixture()
            position = BusinessDictionaryItem.objects.get(code='EXAMINER_SHENGLANG')
            plan = SalaryPlan.objects.get(position=position)
            before = (plan.exam_fee_fen, plan.exam_commission_bps, plan.base_fen)
            customer = ClubAccount.objects.create_account(username='branding-sentinel', account_type='BOSS', nickname='声浪历史昵称')
        adapt_initial_branding(branch)
        original.refresh_from_db()
        self.assertEqual(original.name, '考官（声浪）')
        with store_context(branch):
            position.refresh_from_db()
            plan.refresh_from_db()
            customer.refresh_from_db()
            self.assertEqual(position.name, '考官（南岸电竞）')
            self.assertEqual(position.code, 'EXAMINER_SHENGLANG')
            self.assertEqual(plan.name, '考官（南岸电竞）薪资标准')
            self.assertEqual((plan.exam_fee_fen, plan.exam_commission_bps, plan.base_fen), before)
            self.assertEqual(customer.nickname, '声浪历史昵称')
            position.name = '门店自行维护的声浪称呼'
            position.save()
        branch.initialized_at = timezone.now()
        branch.save()
        adapt_initial_branding(branch)
        with store_context(branch):
            position.refresh_from_db()
            self.assertEqual(position.name, '门店自行维护的声浪称呼')


class StoreMaintenanceCommandTests(TestCase):
    databases = {'default', 'store_b'}

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_real_maintenance_command_reads_only_selected_store(self):
        from wallet.models import Wallet
        branch = store_b()
        original = ClubAccount.objects.create_account(username='maintenance-original', account_type='BOSS')
        Wallet.objects.create(account=original, balance=10101)
        with store_context(branch):
            account = ClubAccount.objects.create_account(username='maintenance-branch', account_type='BOSS')
            Wallet.objects.create(account=account, balance=20202)
        output = StringIO()
        call_command('store_command', '--store', branch.slug, 'verify_business_account_relations', stdout=output)
        self.assertIn('钱包余额 20202', output.getvalue())
        self.assertNotIn('钱包余额 10101', output.getvalue())
        self.assertIsNone(current_store())

    def test_maintenance_rejects_platform_and_unadapted_builtin_commands(self):
        store_b()
        for name in ('migrate', 'flush', 'provision_store', 'grant_store_platform', 'createsuperuser', 'dumpdata', 'shell'):
            with self.subTest(command=name), self.assertRaises(CommandError):
                call_command('store_command', '--store', 'branch-b', name, stdout=StringIO())


class StoreTransactionTests(TransactionTestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.b = store_b()

    def test_selected_store_transaction_rolls_back_and_callbacks_do_not_run(self):
        called = []
        with store_context(self.b):
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    ClubAccount.objects.create_account(username='rollback-only', account_type='BOSS')
                    transaction.on_commit(lambda: called.append(current_database()))
                    raise RuntimeError('abort')
            self.assertFalse(ClubAccount.objects.filter(username='rollback-only').exists())
            self.assertEqual(called, [])
            with transaction.atomic():
                ClubAccount.objects.create_account(username='committed', account_type='BOSS')
                transaction.on_commit(lambda: called.append(current_database()))
            self.assertEqual(called, ['store_b'])
        self.assertFalse(ClubAccount.objects.filter(username='committed').exists())

    def test_both_atomic_decorator_styles_resolve_store_at_invocation(self):
        @transaction.atomic
        def bare():
            self.assertTrue(django_connections[current_database()].in_atomic_block)
            return current_database()

        @transaction.atomic()
        def parentheses():
            self.assertTrue(django_connections[current_database()].in_atomic_block)
            return current_database()

        with store_context(self.b):
            self.assertEqual(bare(), 'store_b')
            self.assertEqual(parentheses(), 'store_b')
        self.assertEqual(bare(), 'default')


class StoreProvisionTests(TransactionTestCase):
    databases = {'default', 'store_b'}

    def test_upgrade_preserves_disabled_store_and_leaves_uninitialized_registration_unavailable(self):
        branch = store_b()
        branch.status = Store.Status.DISABLED
        branch.save(update_fields=['status'])
        pending = Store.objects.create(slug='pending', name='尚未建库门店',
                                       database_alias='not_provisioned')
        call_command('migrate_stores', stdout=StringIO(), verbosity=0)
        branch.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(branch.status, Store.Status.DISABLED)
        self.assertEqual(pending.status, Store.Status.PROVISIONING)
        self.assertIsNone(pending.initialized_at)

    @override_settings(STORE_DATABASE_ALIASES=('store_b',))
    def test_real_provision_creates_independent_staff_login_and_requires_separate_platform_grant(self):
        with patch.dict(os.environ, {'XA_TEST_STORE_ADMIN_PASSWORD': 'isolation-test-Passphrase-2026'}):
            call_command('provision_store', slug='provisioned-b', name='新建电竞门店', database='store_b',
                         admin_username='new-branch-admin', admin_phone='13800001111',
                         password_env='XA_TEST_STORE_ADMIN_PASSWORD', create_database=True, stdout=StringIO())
        branch = Store.objects.get(slug='provisioned-b')
        self.assertEqual(branch.status, Store.Status.CONFIGURING)
        self.assertIsNotNone(branch.initialized_at)
        self.assertFalse(ClubAccount.objects.filter(username='new-branch-admin').exists())
        client = APIClient(HTTP_HOST='admin.esports.club')
        credentials = {'username': 'new-branch-admin', 'password': 'isolation-test-Passphrase-2026'}
        with patch('users.password_login.send_verification_code') as sender:
            challenge = client.post('/api/admin/auth/login', credentials, format='json')
        self.assertTrue(challenge.data['data']['requiresSms'])
        response = client.post('/api/admin/auth/login', {**credentials,
            'smsChallenge': challenge.data['data']['smsChallenge'], 'smsCode': sender.call_args.args[1]},
            format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['code'], 0, response.data)
        token = response.data['data']['token']
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}', HTTP_HOST='admin.esports.club')
        self.assertEqual(client.get('/api/stores/manage/').status_code, 403)
        self.assertFalse(client.get('/api/stores/current/').data['data']['can_manage_stores'])

    @override_settings(STORE_DATABASE_ALIASES=('store_b',))
    def test_new_store_without_staff_is_not_marked_open(self):
        with self.assertRaisesMessage(CommandError, '尚无可登录后台管理员'):
            call_command('provision_store', slug='no-staff-b', name='等待人员初始化',
                         database='store_b', create_database=True, stdout=StringIO())
        branch = Store.objects.get(slug='no-staff-b')
        self.assertEqual(branch.status, Store.Status.PROVISIONING)
        self.assertIsNone(branch.initialized_at)
