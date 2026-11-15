"""Platform A03: actual store writes, activation and independent authorization."""
from io import StringIO
import os
from unittest.mock import patch

from django.core.management import call_command
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog
from console.test_operating_workflow import seed_payroll_fixture
from wallet.models import Transaction, Wallet

from .context import resolve_store, store_context
from .models import Store, StorePlatformGrant
from .tests_foundation import admin


@override_settings(STORE_DATABASE_ALIASES=('store_b',), LOGIN_OTP_REQUIRED=False,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class PlatformStoreWriteLifecycleTest(TransactionTestCase):
    databases = {'default', 'store_b'}

    def ok(self, response):
        self.assertIn(response.status_code, (200, 201), getattr(response, 'data', response.content))
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data.get('data')

    def test_platform_registers_initializes_renames_disables_and_reopens_store_without_sharing_role_authority(self):
        original = resolve_store()
        owner = admin(username='platform-lifecycle-owner', super_admin=True)
        token, _ = issue_account_tokens(owner)
        platform = APIClient()
        platform.credentials(HTTP_X_STORE_SLUG=original.slug, HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(platform.get('/api/stores/manage/').status_code, 403)
        StorePlatformGrant.objects.create(account_id=owner.pk)
        self.ok(platform.get('/api/stores/manage/'))
        before = list(Wallet.objects.order_by('pk').values_list('pk', 'account_id', 'balance', 'frozen_amount'))

        registered = self.ok(platform.post('/api/stores/manage/', {
            'slug': 'platform-branch', 'name': '平台登记店', 'database_alias': 'store_b',
            'boss_url': 'https://boss.store.test/?campaign=qa#/home',
            'provider_url': 'https://provider.store.test/#/orders',
            'admin_url': 'https://admin.store.test/#/dashboard',
        }, format='json'))
        pk = registered['id']
        self.assertEqual(registered['status'], 'PROVISIONING')
        self.assertEqual(APIClient().get('/api/stores/current/?store=platform-branch').status_code, 403)
        self.assertEqual(platform.patch(f'/api/stores/manage/{pk}/', {'status': 'ACTIVE'}, format='json').status_code, 400)
        branch = Store.objects.get(pk=pk)
        with store_context(branch):
            # --keepdb test tables retain migration markers after flush; load
            # the same canonical migration-only configuration fixture again.
            seed_payroll_fixture()
        with patch.dict(os.environ, {'XA_TEST_PLATFORM_BRANCH_PASSWORD': 'Platform-Store-Fixture-Password-2026!'}):
            call_command('provision_store', slug=branch.slug, name=branch.name, database='store_b',
                         admin_username='platform-branch-owner', admin_phone='13800001112',
                         password_env='XA_TEST_PLATFORM_BRANCH_PASSWORD', create_database=True,
                         stdout=StringIO())
        branch.refresh_from_db()
        self.assertEqual(branch.status, Store.Status.CONFIGURING)
        with store_context(branch):
            self.assertFalse(ClubAccount.objects.filter(account_type__in=['BOSS', 'PROVIDER']).exists())
            # post_migrate creates the non-login platform bookkeeping wallet;
            # it must be empty and must not seed customer/provider funds.
            self.assertFalse(Wallet.objects.exclude(balance=0, frozen_amount=0,
                                                    total_recharge=0, total_gift=0).exists())
            self.assertFalse(Transaction.objects.exists())
        local = APIClient(HTTP_HOST='admin.esports.club')
        credentials = {'username': 'platform-branch-owner', 'password': 'Platform-Store-Fixture-Password-2026!'}
        with patch('users.password_login.send_verification_code') as sender:
            challenge = self.ok(local.post('/api/admin/auth/login', credentials, format='json'))
        login = self.ok(local.post('/api/admin/auth/login', {**credentials,
            'smsChallenge': challenge['smsChallenge'], 'smsCode': sender.call_args.args[1]}, format='json'))
        self.assertTrue(login['profile']['is_superuser'])
        local.credentials(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + login['token'])
        self.assertEqual(local.get('/api/stores/manage/').status_code, 403)
        self.assertFalse(self.ok(local.get('/api/stores/current/'))['can_manage_stores'])

        updated = self.ok(platform.patch(f'/api/stores/manage/{pk}/', {'name': '平台接口改名店'}, format='json'))
        self.assertEqual(updated['name'], '平台接口改名店')
        self.assertIn('campaign=qa&store=platform-branch#/home', updated['boss_url'])
        self.assertEqual(self.ok(local.get('/api/stores/current/'))['name'], '平台接口改名店')
        self.assertEqual(platform.patch(f'/api/stores/manage/{pk}/', {'status': 'ACTIVE'}, format='json').status_code, 400)
        self.assertEqual(platform.patch(f'/api/stores/manage/{pk}/', {'status': 'DISABLED'}, format='json').status_code, 400)
        # Exercise legacy already-open stores separately; the product must not
        # use this fixture transition to bypass its still-unimplemented opening gate.
        Store.objects.filter(pk=pk).update(status=Store.Status.ACTIVE)
        self.ok(platform.patch(f'/api/stores/manage/{pk}/', {'status': 'DISABLED'}, format='json'))
        self.assertEqual(local.get('/api/stores/current/').status_code, 401)
        self.assertEqual(self.ok(platform.get('/api/stores/current/'))['slug'], original.slug)
        self.ok(platform.patch(f'/api/stores/manage/{pk}/', {'status': 'ACTIVE'}, format='json'))
        self.assertEqual(self.ok(local.get('/api/stores/current/'))['status'], 'ACTIVE')
        self.assertEqual(platform.patch(f'/api/stores/manage/{original.pk}/', {'status': 'DISABLED'}, format='json').status_code, 400)
        self.assertTrue(resolve_store(original.slug).is_active)
        self.assertEqual(platform.patch(f'/api/stores/manage/{pk}/', {'database_alias': 'default'}, format='json').status_code, 400)
        self.assertEqual(platform.delete(f'/api/stores/manage/{pk}/').status_code, 405)
        self.assertEqual(Store.objects.get(pk=pk).database_alias, 'store_b')
        self.assertEqual(list(Wallet.objects.order_by('pk').values_list('pk', 'account_id', 'balance', 'frozen_amount')), before)
        self.assertEqual(AdminAuditLog.objects.filter(resource='stores', method='POST').count(), 1)
        self.assertEqual(AdminAuditLog.objects.filter(resource='stores', method='PATCH', object_id=str(pk)).count(), 3)
        with store_context(branch):
            self.assertFalse(AdminAuditLog.objects.filter(resource='stores').exists())
        StorePlatformGrant.objects.filter(account_id=owner.pk).update(is_active=False)
        self.assertEqual(platform.get('/api/stores/manage/').status_code, 403)
