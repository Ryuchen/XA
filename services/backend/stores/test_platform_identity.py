from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from club_accounts.models import ClubAccount
from stores.context import resolve_store, store_context
from stores.models import PlatformAccount, PlatformAuditEvent, PlatformRoleGrant
from stores.platform_permissions import platform_may, platform_stores_for
from stores.tests_hosts import xinan_store


class PlatformIdentityTests(TestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first = resolve_store()
        self.second = xinan_store()
        self.platform = PlatformAccount(username='platform-finance', phone='13800001111')
        self.platform.set_password('test-strong-password')
        self.platform.save()

    def test_separate_account_password_and_store_scope(self):
        self.assertNotEqual(self.platform.password, 'test-strong-password')
        self.assertTrue(self.platform.check_password('test-strong-password'))
        PlatformRoleGrant.objects.create(account=self.platform, role='FINANCE', scope_slug='xinan')
        self.assertTrue(platform_may(self.platform, 'withdraw:final_review', store_slug='xinan'))
        self.assertFalse(platform_may(self.platform, 'withdraw:final_review', store_slug='shenglang'))
        self.assertFalse(platform_may(self.platform, 'withdraw:final_review'))
        self.assertFalse(platform_may(self.platform, 'stores:manage'))
        self.assertFalse(platform_may(self.platform, 'salary:configure', store_slug='unknown'))
        self.assertEqual(list(platform_stores_for(self.platform, 'withdraw:view').values_list('slug', flat=True)), ['xinan'])
        self.assertFalse(platform_stores_for(self.platform, 'stores:manage').exists())
        with store_context(self.second):
            local = ClubAccount.objects.create_account(username='platform-finance', account_type='STAFF')
            self.assertFalse(platform_may(local, 'withdraw:final_review', store_slug='xinan'))
            self.assertEqual(PlatformAccount.objects.get(pk=self.platform.pk)._state.db, 'default')

    def test_wildcard_grant_revocation_and_role_are_checked_each_time(self):
        grant = PlatformRoleGrant.objects.create(account=self.platform, role='SUPER_ADMIN', scope_slug='*')
        self.assertTrue(platform_may(self.platform, 'stores:manage'))
        self.assertEqual(set(platform_stores_for(self.platform, 'dashboard:view').values_list('slug', flat=True)), {'shenglang', 'xinan'})
        self.assertTrue(platform_may(self.platform, 'salary:configure', store_slug='xinan'))
        self.assertFalse(platform_may(self.platform, 'withdraw:record_payment', store_slug='xinan'))
        grant.is_active = False
        grant.save(update_fields=['is_active'])
        self.assertFalse(platform_may(self.platform, 'stores:manage'))
        grant.is_active = True
        grant.save(update_fields=['is_active'])
        PlatformAccount.objects.filter(pk=self.platform.pk).update(is_active=False)
        self.assertFalse(platform_may(self.platform, 'stores:manage'))

    def test_store_scoped_super_role_does_not_grant_global_platform_management(self):
        PlatformRoleGrant.objects.create(account=self.platform, role='SUPER_ADMIN', scope_slug='xinan')
        self.assertTrue(platform_may(self.platform, 'store_accounts:manage', store_slug='xinan'))
        self.assertFalse(platform_may(self.platform, 'platform_accounts:manage', store_slug='xinan'))
        self.assertFalse(platform_may(self.platform, 'stores:manage', store_slug='xinan'))

    def test_duplicate_scope_and_nonexistent_store_are_rejected(self):
        PlatformRoleGrant.objects.create(account=self.platform, role='FINANCE', scope_slug='xinan')
        with self.assertRaises(IntegrityError), transaction.atomic(using='default'):
            PlatformRoleGrant.objects.create(account=self.platform, role='FINANCE', scope_slug='xinan')
        with self.assertRaises(ValidationError):
            PlatformRoleGrant(account=self.platform, role='FINANCE', scope_slug='missing').full_clean()

    def test_audit_identifies_platform_account_and_target_store(self):
        with store_context(self.second):
            event = PlatformAuditEvent.objects.create(
                account=self.platform, action='salary:calculate', target_store_slug='xinan',
                target_type='payroll', target_id='2026-11', detail={'result': 'preview'},
            )
        self.assertEqual(event._state.db, 'default')
        self.assertEqual(event.account_id, self.platform.pk)
        self.assertEqual(event.target_store_slug, 'xinan')
