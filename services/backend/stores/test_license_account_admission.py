from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from club_accounts.license_quota import LicenseAdmissionError
from club_accounts.models import ClubAccount
from club_accounts.tokens import account_for_claims, issue_account_tokens
from engagement.retirement import retire
from stores.context import resolve_store, store_context
from stores.license_codes import _activate_after_verified_store_auth, issue
from stores.models import PlatformAccount, PlatformRoleGrant
from stores.platform_auth import issue_tokens
from stores.tests_foundation import admin
from stores.tests_hosts import xinan_store


@override_settings(LICENSE_ACCOUNT_ADMISSION_ENABLED=True, LICENSE_NOTICE_DISPATCH_ENABLED=False)
class LicenseAccountAdmissionTests(TestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first, self.second = resolve_store(), xinan_store()
        actor = PlatformAccount.objects.create(username='quota-owner', phone='13800001111', is_phone_verified=True)
        PlatformRoleGrant.objects.create(account=actor, role='SUPER_ADMIN', scope_slug='*')
        raw, _ = issue_tokens(actor, 'SMS', actor.phone)
        self.platform_token = AccessToken(raw)
        with store_context(self.second):
            self.owner = admin('quota-admin', super_admin=True)
        now = timezone.now()
        self.license, code = issue(self.platform_token, store_slug=self.second.slug, tier='BASIC', kind='INITIAL',
            starts_at=now - timedelta(days=1), ends_at=now + timedelta(days=30), backdate_reason='隔离测试')
        _activate_after_verified_store_auth(store=self.second, actor_id=self.owner.pk, code=code)

    def fill(self, account_type, count):
        with override_settings(LICENSE_ACCOUNT_ADMISSION_ENABLED=False):
            ClubAccount.objects.bulk_create([
                ClubAccount(username=f'{account_type.lower()}-{i}', account_type=account_type,
                            account_no=f'{account_type}-{i}', is_active=i != 0, can_login=i != 1)
                for i in range(count)
            ])

    def test_quota_counts_disabled_accounts_and_isolated_categories(self):
        with store_context(self.second):
            self.fill('BOSS', 499)
            ClubAccount.objects.create_account(username='last-boss', account_type='BOSS')
            with self.assertRaisesMessage(LicenseAdmissionError, '上限'):
                ClubAccount.objects.create_account(username='over-limit', account_type='BOSS')
            self.assertFalse(ClubAccount.objects.filter(username='over-limit').exists())
            ClubAccount.objects.create_account(username='separate-provider', account_type='PROVIDER')
        with store_context(self.first):
            with self.assertRaisesMessage(LicenseAdmissionError, '授权'):
                ClubAccount.objects.create_account(username='unlicensed-store', account_type='BOSS')

    def test_expiry_blocks_new_accounts_but_retains_existing_session(self):
        with store_context(self.second):
            account = ClubAccount.objects.create_account(username='existing', account_type='BOSS')
            token, _ = issue_account_tokens(account)
            self.license.ends_at = timezone.now() - timedelta(days=8)
            self.license.starts_at = timezone.now() - timedelta(days=30)
            self.license.activated_at = timezone.now() - timedelta(days=29)
            self.license.save(update_fields=['starts_at', 'ends_at', 'activated_at'])
            self.assertEqual(account_for_claims(AccessToken(token)).pk, account.pk)
            with self.assertRaisesMessage(LicenseAdmissionError, '授权'):
                ClubAccount.objects.create_account(username='new-provider', account_type='PROVIDER')

    def test_grace_keeps_admission_but_provider_limit_still_applies(self):
        with store_context(self.second):
            self.license.ends_at = timezone.now() - timedelta(days=1)
            self.license.starts_at = timezone.now() - timedelta(days=30)
            self.license.activated_at = timezone.now() - timedelta(days=29)
            self.license.save(update_fields=['starts_at', 'ends_at', 'activated_at'])
            self.fill('PROVIDER', 499)
            ClubAccount.objects.create_account(username='last-provider', account_type='PROVIDER')
            with self.assertRaisesMessage(LicenseAdmissionError, '上限'):
                ClubAccount.objects.create_account(username='extra-provider', account_type='PROVIDER')
            ClubAccount.objects.create_account(username='boss-during-grace', account_type='BOSS')

    def test_retirement_releases_slot_reentry_checks_quota_and_old_token_stays_invalid(self):
        with store_context(self.second):
            account = ClubAccount.objects.create_account(username='returning-boss', account_type='BOSS')
            old, _ = issue_account_tokens(account)
            retire(account, self.owner, '老板确认退店')
            with self.assertRaises(TokenError):
                account_for_claims(AccessToken(old))
            self.fill('BOSS', 500)
            with self.assertRaisesMessage(LicenseAdmissionError, '上限'):
                issue_account_tokens(account)
            ClubAccount.objects.filter(username='boss-0').update(license_membership_active=False)
            new, _ = issue_account_tokens(account)
            self.assertEqual(account_for_claims(AccessToken(new)).pk, account.pk)
            with self.assertRaises(TokenError):
                account_for_claims(AccessToken(old))

    def test_bulk_creation_cannot_bypass_quota(self):
        with store_context(self.second):
            with self.assertRaisesMessage(LicenseAdmissionError, '逐个核验'):
                ClubAccount.objects.bulk_create([ClubAccount(username='bulk', account_type='BOSS', account_no='bulk')])
