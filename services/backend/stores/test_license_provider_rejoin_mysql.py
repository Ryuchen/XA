"""A real last-seat race between provider reentry and new registration."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from club_accounts.license_quota import LicenseAdmissionError
from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens
from common.mysql_test_support import require_disposable_mysql
from stores.context import resolve_store, store_context
from stores.license_codes import issue, _activate_after_verified_store_auth
from stores.models import PlatformAccount, PlatformRoleGrant
from stores.platform_auth import issue_tokens
from stores.tests_foundation import admin


@skipUnless(connection.vendor == 'mysql', 'Requires isolated MySQL row locks')
@override_settings(LICENSE_ACCOUNT_ADMISSION_ENABLED=True, LICENSE_NOTICE_DISPATCH_ENABLED=False)
class ProviderReentryQuotaMySQLTest(TransactionTestCase):
    def test_registration_and_reentry_compete_for_last_slot(self):
        require_disposable_mysql(connection)
        store = resolve_store()
        actor = PlatformAccount.objects.create(username='quota-race-owner', phone='13800001111', is_phone_verified=True)
        PlatformRoleGrant.objects.create(account=actor, role='SUPER_ADMIN', scope_slug='*')
        access, _ = issue_tokens(actor, 'SMS', actor.phone)
        with store_context(store):
            owner = admin('quota-race-admin', super_admin=True)
        now = timezone.now()
        _, code = issue(AccessToken(access), store_slug=store.slug, tier='BASIC', kind='INITIAL',
                        starts_at=now-timedelta(days=1), ends_at=now+timedelta(days=30), backdate_reason='隔离测试')
        _activate_after_verified_store_auth(store=store, actor_id=owner.pk, code=code)
        with store_context(store):
            returning = ClubAccount.objects.create_account(username='quota-race-returning', account_type='PROVIDER')
            # Fixture simulates completed explicit cancellation; no actual
            # cancellation entry point is claimed by this test.
            ClubAccount.objects.filter(pk=returning.pk).update(license_membership_active=False)
            with override_settings(LICENSE_ACCOUNT_ADMISSION_ENABLED=False):
                ClubAccount.objects.bulk_create([
                    ClubAccount(username=f'quota-race-{i}', account_type='PROVIDER', account_no=f'QR-{i}')
                    for i in range(499)
                ])
        start = Barrier(2)

        def attempt(kind):
            close_old_connections()
            try:
                with store_context(store):
                    start.wait(timeout=10)
                    try:
                        if kind == 'rejoin':
                            account = ClubAccount.objects.get(pk=returning.pk)
                            issue_account_tokens(account)
                        else:
                            ClubAccount.objects.create_account(username='quota-race-new', account_type='PROVIDER')
                    except LicenseAdmissionError as exc:
                        return str(exc)
                    return 'success'
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ('rejoin', 'register')))
        self.assertEqual(results.count('success'), 1, results)
        self.assertIn('上限', next(row for row in results if row != 'success'))
        with store_context(store):
            self.assertEqual(ClubAccount.objects.filter(account_type='PROVIDER', license_membership_active=True).count(), 500)
