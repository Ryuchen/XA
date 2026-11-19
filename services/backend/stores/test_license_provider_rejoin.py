"""The provider quota boundary for a future explicit cancellation flow.

There is deliberately no cancellation endpoint in this test: marking the
account is setup, not proof that the financial lifecycle is ready to close.
"""
import os
from unittest import skipUnless
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from club_accounts.license_quota import LicenseAdmissionError, LicenseAdmissionUnavailable
from club_accounts.models import ClubAccount, ProviderLifecycleLock
from club_accounts.tokens import account_for_claims, issue_account_tokens
from orders.tests.factories import make_user
from stores import test_license_account_admission as admission_fixture
from stores.context import store_context
from users.models import EscortProfile
from wallet import test_deposit_clearance as deposit_fixture


@override_settings(LICENSE_ACCOUNT_ADMISSION_ENABLED=True, LICENSE_NOTICE_DISPATCH_ENABLED=False,
                   LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class ProviderQuotaReentryTest(TestCase):
    databases = {'default', 'store_b'}
    setUp = admission_fixture.LicenseAccountAdmissionTests.setUp
    fill = admission_fixture.LicenseAccountAdmissionTests.fill
    _base = deposit_fixture.DepositClearanceRefundApiTest._base

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_retired_provider_requires_a_free_slot_and_old_tokens_stay_invalid(self):
        with store_context(self.second):
            provider = ClubAccount.objects.create_account(username='returning-provider', account_type='PROVIDER')
            old, _ = issue_account_tokens(provider)
            # Simulate an eventual completed explicit cancellation; the actual
            # account-closing operation and its financial checks are not built.
            ClubAccount.objects.filter(pk=provider.pk).update(license_membership_active=False)
            provider.refresh_from_db()
            with self.assertRaises(TokenError):
                account_for_claims(AccessToken(old))
            self.fill('PROVIDER', 500)
            with self.assertRaisesMessage(LicenseAdmissionError, '上限'):
                issue_account_tokens(provider)
            provider.refresh_from_db()
            self.assertFalse(provider.license_membership_active)
            ClubAccount.objects.filter(username='provider-0').update(license_membership_active=False)
            new, _ = issue_account_tokens(provider)
            self.assertEqual(account_for_claims(AccessToken(new)).pk, provider.pk)
            with self.assertRaises(TokenError):
                account_for_claims(AccessToken(old))
            with store_context(self.first), self.assertRaises(TokenError):
                account_for_claims(AccessToken(new))

    def test_offline_and_zero_deposit_do_not_release_provider_slot(self):
        with store_context(self.second):
            self.fill('PROVIDER', 499)
            provider = ClubAccount.objects.create_account(username='offline-provider', account_type='PROVIDER')
            user = make_user(role='PROVIDER')
            profile = EscortProfile.objects.create(user=user, account=provider, is_verified=True, deposit_paid=100)
            token, _ = issue_account_tokens(provider)
            client = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
            response = client.post('/api/users/escorts/status/', {'status': 'OFFLINE'}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            # Isolate the quota effect of a zero deposit; the actual cash
            # deposit-refund workflow has separate integration tests.
            profile.deposit_paid = 0
            profile.save(update_fields=['deposit_paid'])
            provider.refresh_from_db()
            self.assertTrue(provider.license_membership_active)
            with self.assertRaisesMessage(LicenseAdmissionError, '上限'):
                ClubAccount.objects.create_account(username='over-provider-limit', account_type='PROVIDER')

    def test_reentry_rejects_missing_lifecycle_row(self):
        with store_context(self.second):
            provider = ClubAccount.objects.create_account(username='missing-barrier-provider', account_type='PROVIDER')
            ClubAccount.objects.filter(pk=provider.pk).update(license_membership_active=False)
            ProviderLifecycleLock.objects.filter(account=provider).delete()
            provider.refresh_from_db()
            with self.assertRaises(LicenseAdmissionUnavailable):
                issue_account_tokens(provider)
            provider.refresh_from_db()
            self.assertFalse(provider.license_membership_active)

    def test_real_deposit_refund_keeps_provider_slot(self):
        with store_context(self.second):
            self.client = APIClient(HTTP_HOST='admin.esports.club')
            deposit_fixture.DepositClearanceRefundApiTest.setUp(self)
            deposit_fixture.DepositClearanceRefundApiTest.prepare_closed_order(self, 7)
            self.fill('PROVIDER', 499)
            response = deposit_fixture.DepositClearanceRefundApiTest.post_refund(self, 'license-quota-deposit')
            self.assertEqual(response.data['code'], 0, response.data)
            self.account.refresh_from_db()
            self.assertTrue(self.account.license_membership_active)
            with self.assertRaisesMessage(LicenseAdmissionError, '上限'):
                ClubAccount.objects.create_account(username='over-limit-after-deposit', account_type='PROVIDER')
