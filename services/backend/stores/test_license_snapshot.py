from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError
from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone

from club_accounts.license_quota import LicenseAdmissionUnavailable
from club_accounts.models import ClubAccount, LicenseStatusSnapshot
from stores.context import store_context
from stores.license_benefits import check_new_benefits
from stores.license_codes import _activate_after_verified_store_auth, issue
from stores.license_features import change_feature
from stores.license_snapshot import business_status
from stores.test_license_account_admission import LicenseAccountAdmissionTests


@override_settings(LICENSE_SNAPSHOT_CACHE_ENABLED=True, LICENSE_BENEFIT_ENFORCEMENT_ENABLED=True,
                   LICENSE_ACCOUNT_ADMISSION_ENABLED=True, LICENSE_NOTICE_DISPATCH_ENABLED=False,
                   PLATFORM_IDENTITY_ENABLED=True)
class LicenseSnapshotTests(TestCase):
    databases = {'default', 'store_b'}
    setUp = LicenseAccountAdmissionTests.setUp

    def refresh_snapshot(self):
        with self.captureOnCommitCallbacks(using='default', execute=True), self.captureOnCommitCallbacks(using='store_b', execute=True):
            with store_context(self.second):
                return business_status(self.second)

    def offline(self, *, now=None):
        from contextlib import nullcontext
        clock = patch('django.utils.timezone.now', return_value=now) if now else nullcontext()
        with store_context(self.second), patch('stores.license_status.status_for_store',
                                               side_effect=DatabaseError('control unavailable')), clock:
            return business_status(self.second)

    def test_cached_authorization_allows_eligible_account_and_recomputes_deadlines(self):
        online = self.refresh_snapshot()
        self.assertEqual((online['status'], online['tier'], online['license_source']), ('ACTIVE', 'BASIC', 'ONLINE'))
        cached = self.offline()
        self.assertEqual((cached['status'], cached['tier'], cached['license_source']), ('ACTIVE', 'BASIC', 'CACHE'))
        with store_context(self.second), patch('stores.license_status.status_for_store',
                                               side_effect=DatabaseError('control unavailable')):
            account = ClubAccount.objects.create_account(username='cached-boss', account_type='BOSS')
            self.assertEqual(account._state.db, 'store_b')
            self.assertEqual(check_new_benefits('VIP_GROWTH')[0], 'DENY')
        end = self.license.ends_at
        self.assertEqual(self.offline(now=end)['status'], 'GRACE')
        self.assertTrue(self.offline(now=end + timedelta(days=7) - timedelta(microseconds=1))['allow_new_accounts'])
        expired = self.offline(now=end + timedelta(days=7))
        self.assertEqual(expired['status'], 'EXPIRED')
        self.assertFalse(expired['allow_new_accounts'])
        self.assertFalse(expired['allow_new_benefits'])

    def test_future_activated_tier_switches_at_its_cached_effective_time(self):
        start = timezone.now() + timedelta(days=2)
        row, code = issue(self.platform_token, store_slug=self.second.slug, tier='PRO', kind='CHANGE',
                          starts_at=start, ends_at=self.license.ends_at)
        _activate_after_verified_store_auth(store=self.second, actor_id=self.owner.pk, code=code)
        self.refresh_snapshot()
        before = self.offline(now=start - timedelta(microseconds=1))
        after = self.offline(now=start)
        self.assertEqual((before['tier'], before['feature_modes']['VIP_GROWTH']), ('BASIC', 'READ_ONLY'))
        self.assertEqual((after['tier'], after['feature_modes']['VIP_GROWTH']), ('PRO', 'FULL'))
        self.assertEqual(after['current']['id'], str(row.pk))

    def test_changed_tier_feature_is_cached_only_after_control_verification(self):
        self.refresh_snapshot()
        change_feature(self.platform_token, tier='BASIC', feature='GIFTS', mode='FULL')
        self.assertEqual(self.offline()['feature_modes']['GIFTS'], 'OFF')
        refreshed = self.refresh_snapshot()
        self.assertEqual(refreshed['feature_modes']['GIFTS'], 'FULL')
        self.assertEqual(self.offline()['feature_modes']['GIFTS'], 'FULL')

    def test_tampered_snapshot_fails_closed_for_new_accounts_and_benefits(self):
        self.refresh_snapshot()
        with store_context(self.second):
            row = LicenseStatusSnapshot.objects.get(pk=1)
            payload = dict(row.payload, grace_days=999999999)
            LicenseStatusSnapshot.objects.filter(pk=1).update(payload=payload)
            with patch('stores.license_status.status_for_store', side_effect=DatabaseError('offline')):
                self.assertEqual(check_new_benefits()[0], 'UNKNOWN')
                with self.assertRaises(LicenseAdmissionUnavailable):
                    ClubAccount.objects.create_account(username='tampered-denied', account_type='BOSS')
            self.assertFalse(ClubAccount.objects.filter(username='tampered-denied').exists())

    def test_signed_snapshot_cannot_be_copied_to_another_store(self):
        self.refresh_snapshot()
        with store_context(self.second):
            saved = LicenseStatusSnapshot.objects.values('store_slug', 'payload', 'signature', 'checked_at').get(pk=1)
        with store_context(self.first):
            LicenseStatusSnapshot.objects.update_or_create(pk=1, defaults=saved)
            with patch('stores.license_status.status_for_store', side_effect=DatabaseError('offline')):
                self.assertEqual(check_new_benefits()[0], 'UNKNOWN')

    def test_rolled_back_business_transaction_does_not_publish_snapshot(self):
        from stores.db import transaction
        with self.captureOnCommitCallbacks(using='default', execute=True), self.captureOnCommitCallbacks(using='store_b', execute=True):
            with store_context(self.second), transaction.atomic():
                self.assertEqual(business_status(self.second)['status'], 'ACTIVE')
                transaction.set_rollback(True)
        with store_context(self.second):
            row = LicenseStatusSnapshot.objects.filter(pk=1).first()
            self.assertTrue(row is None or not row.signature)
        with self.assertRaises(DatabaseError):
            self.offline()

    def test_out_of_order_commit_callbacks_cannot_replace_newer_verified_terms(self):
        with self.captureOnCommitCallbacks(using='default', execute=False) as directory_callbacks, self.captureOnCommitCallbacks(using='store_b', execute=False) as tenant_callbacks:
            with store_context(self.second):
                self.assertEqual(business_status(self.second)['feature_modes']['GIFTS'], 'OFF')
            change_feature(self.platform_token, tier='BASIC', feature='GIFTS', mode='FULL')
            with store_context(self.second):
                self.assertEqual(business_status(self.second)['feature_modes']['GIFTS'], 'FULL')
        self.assertEqual(len(directory_callbacks), 2)
        self.assertEqual(len(tenant_callbacks), 2)
        tenant_callbacks[1]()
        directory_callbacks[1]()
        tenant_callbacks[0]()
        directory_callbacks[0]()
        self.assertEqual(self.offline()['feature_modes']['GIFTS'], 'FULL')

    def test_mutating_returned_evidence_cannot_change_the_pending_signed_snapshot(self):
        with self.captureOnCommitCallbacks(using='default', execute=True), self.captureOnCommitCallbacks(using='store_b', execute=True):
            with store_context(self.second):
                result = business_status(self.second)
                result['verified_snapshot']['payload']['grace_days'] = 999999999
        self.assertEqual(self.offline()['grace_days'], 7)

    def test_control_rollback_cannot_publish_uncommitted_license(self):
        from django.db import transaction
        with self.captureOnCommitCallbacks(using='default', execute=True), self.captureOnCommitCallbacks(using='store_b', execute=True):
            with transaction.atomic(using='default'):
                with store_context(self.second):
                    self.assertEqual(business_status(self.second)['status'], 'ACTIVE')
                transaction.set_rollback(True, using='default')
        with store_context(self.second):
            row = LicenseStatusSnapshot.objects.filter(pk=1).first()
            self.assertTrue(row is None or not row.signature)
        with self.assertRaises(DatabaseError):
            self.offline()

    def test_snapshot_waits_for_control_commit_after_tenant_commit(self):
        with self.captureOnCommitCallbacks(using='default', execute=False) as control, self.captureOnCommitCallbacks(using='store_b', execute=False) as tenant:
            with store_context(self.second):
                business_status(self.second)
        tenant[0]()
        with self.assertRaises(DatabaseError):
            self.offline()
        control[0]()
        self.assertEqual(self.offline()['license_source'], 'CACHE')

    def test_snapshot_waits_for_tenant_commit_after_control_commit(self):
        with self.captureOnCommitCallbacks(using='default', execute=False) as control, self.captureOnCommitCallbacks(using='store_b', execute=False) as tenant:
            with store_context(self.second):
                business_status(self.second)
        control[0]()
        with self.assertRaises(DatabaseError):
            self.offline()
        tenant[0]()
        self.assertEqual(self.offline()['license_source'], 'CACHE')

    def test_secret_rotation_requires_an_explicit_fallback_key(self):
        self.refresh_snapshot()
        old_key = settings.SECRET_KEY
        with override_settings(SECRET_KEY='rotated-test-key', SECRET_KEY_FALLBACKS=[old_key]):
            self.assertEqual(self.offline()['license_source'], 'CACHE')
        with override_settings(SECRET_KEY='rotated-test-key', SECRET_KEY_FALLBACKS=[]):
            with self.assertRaises(DatabaseError):
                self.offline()

    @override_settings(BOSS_FUNDING_SELECTION_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                       KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, ALLOWED_HOSTS=['*'])
    def test_cached_active_terms_allow_real_order_confirmation_during_control_outage(self):
        from rest_framework.test import APIClient
        from club_accounts.tokens import issue_account_tokens
        from engagement.models import LicenseConsumptionPointsDecision, PointLedger
        from orders.models import Order
        from orders.tests.test_service_funding_api import ServiceFundingApiTest
        self.refresh_snapshot()
        with store_context(self.second):
            ServiceFundingApiTest.setUp(self)
            self.boss = APIClient(HTTP_HOST='xa.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
            payload = ServiceFundingApiTest.payload(self, gift_balance_amount=200, compensation_balance_amount=100)
            created = self.boss.post('/api/orders/create/', payload, format='json')
            self.assertEqual(created.data['code'], 0, created.data)
            order = Order.objects.get(pk=created.data['data']['id'])
            with patch('stores.license_status.status_for_store', side_effect=DatabaseError('control unavailable')):
                self.assertTrue(ServiceFundingApiTest.mature(self, order))
            ledger = PointLedger.objects.get(order=order, kind='CONSUMPTION')
            decision = LicenseConsumptionPointsDecision.objects.get(order=order)
            self.assertEqual(ledger.amount, 1050)
            self.assertEqual((decision.outcome, decision.license_evidence['license_source']), ('ALLOW', 'CACHE'))
            self.assertEqual(decision.license_evidence['verified_snapshot']['payload']['store_slug'], self.second.slug)
            self.assertEqual(len(decision.license_evidence['verified_snapshot']['signature']), 64)
