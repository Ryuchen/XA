from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.db import DatabaseError, close_old_connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken
from zoneinfo import ZoneInfo

from .license_codes import LicenseCodeError, _activate_after_verified_store_auth, issue, void
from .models import PlatformAccount, PlatformAuditEvent, PlatformRoleGrant, Store, StoreLicenseActivationAudit, StoreLicenseCode, StoreLicenseContract
from .platform_auth import issue_tokens
from .license_status import status_for_store


class LicenseCodeLifecycleTests(TestCase):
    def setUp(self):
        self.store = Store.objects.first()
        self.other = Store.objects.create(slug='second-license-store', name='另一店', database_alias='second_license_db')
        self.actor = PlatformAccount.objects.create(username='license-owner', phone='13800000000', is_phone_verified=True)
        PlatformRoleGrant.objects.create(account=self.actor, role='SUPER_ADMIN', scope_slug='*')
        raw_token, _ = issue_tokens(self.actor, 'SMS', self.actor.phone)
        self.token = AccessToken(raw_token)
        self.now = timezone.now()
        self.start = self.now + timedelta(days=1)
        self.end = self.now + timedelta(days=366)

    def create_code(self, **overrides):
        args = {'store_slug': self.store.slug, 'tier': 'PRO', 'kind': 'INITIAL',
                'starts_at': self.start, 'ends_at': self.end}
        args.update(overrides)
        return issue(self.token, **args)

    def test_secret_is_one_time_and_activation_is_store_bound_and_idempotent(self):
        row, code = self.create_code()
        self.assertNotIn(code, str(row.__dict__))
        self.assertNotIn(code, str(list(PlatformAuditEvent.objects.values_list('detail', flat=True))))
        with self.assertRaises(LicenseCodeError):
            _activate_after_verified_store_auth(store=self.other, actor_id=1, code=code)
        first, created = _activate_after_verified_store_auth(store=self.store, actor_id=10, code=code)
        self.assertTrue(created)
        again, created = _activate_after_verified_store_auth(store=self.store, actor_id=11, code=code)
        self.assertFalse(created)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(again.activated_by_id, 10)
        self.assertEqual(StoreLicenseActivationAudit.objects.count(), 1)

    def test_end_boundary_and_late_activation_do_not_change_dates(self):
        row, code = self.create_code()
        with patch('stores.license_codes.timezone.now', return_value=self.start + timedelta(days=10)):
            activated, _ = _activate_after_verified_store_auth(store=self.store, actor_id=10, code=code)
        self.assertEqual(activated.ends_at, self.end)
        other_row, other_code = self.create_code(store_slug=self.other.slug)
        with patch('stores.license_codes.timezone.now', return_value=other_row.ends_at):
            with self.assertRaisesMessage(LicenseCodeError, '授权已结束'):
                _activate_after_verified_store_auth(store=self.other, actor_id=10, code=other_code)
        other_row.refresh_from_db()
        self.assertIsNone(other_row.activated_at)

    def test_void_future_activated_reissue_and_preserve_audit(self):
        row, code = self.create_code()
        _activate_after_verified_store_auth(store=self.store, actor_id=10, code=code)
        with self.assertRaisesMessage(LicenseCodeError, '现有待生效'):
            self.create_code()
        void(self.token, code_id=row.pk, reason='门店重新安排')
        cancelled, created = _activate_after_verified_store_auth(store=self.store, actor_id=10, code=code)
        self.assertFalse(created)
        self.assertIsNotNone(cancelled.voided_at)
        new_row, new_code = self.create_code()
        self.assertNotEqual(row.pk, new_row.pk)
        self.assertNotEqual(code, new_code)
        self.assertTrue(PlatformAuditEvent.objects.filter(action='license.code_voided', target_id=str(row.pk)).exists())

    def test_void_unused_code_rejects_activation(self):
        row, code = self.create_code()
        void(self.token, code_id=row.pk, reason='授权码丢失')
        with self.assertRaises(LicenseCodeError):
            _activate_after_verified_store_auth(store=self.store, actor_id=10, code=code)

    def test_expired_code_replay_remains_idempotent(self):
        row, code = self.create_code()
        _activate_after_verified_store_auth(store=self.store, actor_id=10, code=code)
        with patch('stores.license_codes.timezone.now', return_value=self.end + timedelta(days=1)):
            same, created = _activate_after_verified_store_auth(store=self.store, actor_id=10, code=code)
        self.assertFalse(created)
        self.assertEqual(row.pk, same.pk)

    def test_renewal_extends_without_changing_tier_and_change_keeps_expiry(self):
        row, code = self.create_code(starts_at=self.now - timedelta(days=1), backdate_reason='签约已生效')
        _activate_after_verified_store_auth(store=self.store, actor_id=10, code=code)
        with self.assertRaisesMessage(LicenseCodeError, '续期须延长'):
            self.create_code(kind='RENEW', tier='BASIC', starts_at=self.end, ends_at=self.end + timedelta(days=365))
        with self.assertRaisesMessage(LicenseCodeError, '续期须延长'):
            self.create_code(kind='RENEW', tier='PRO', starts_at=self.start, ends_at=self.end)
        with self.assertRaisesMessage(LicenseCodeError, '调档不改变'):
            self.create_code(kind='CHANGE', tier='ULTRA', starts_at=self.start, ends_at=self.end + timedelta(days=10))
        renewal, _ = self.create_code(kind='RENEW', tier='PRO', starts_at=self.end, ends_at=self.end + timedelta(days=365))
        self.assertEqual(renewal.starts_at, row.ends_at)

    def test_default_one_year_and_renewal_starts_at_existing_end(self):
        leap_day = datetime(2028, 2, 29, 8, 0, tzinfo=ZoneInfo('Asia/Shanghai'))
        with patch('stores.license_codes.timezone.now', return_value=leap_day):
            row, code = issue(self.token, store_slug=self.store.slug, tier='PRO', kind='INITIAL')
            self.assertEqual(row.starts_at, leap_day)
            self.assertEqual(row.ends_at, datetime(2029, 2, 28, 8, 0, tzinfo=ZoneInfo('Asia/Shanghai')))
            _activate_after_verified_store_auth(store=self.store, actor_id=10, code=code)
            renewal, _ = issue(self.token, store_slug=self.store.slug, tier='PRO', kind='RENEW')
        self.assertEqual(renewal.starts_at, row.ends_at)
        self.assertEqual(renewal.ends_at, datetime(2030, 2, 28, 8, 0, tzinfo=ZoneInfo('Asia/Shanghai')))

    def test_scheduled_activation_changes_tier_only_at_effective_time(self):
        current, code = self.create_code(starts_at=self.now - timedelta(days=1), backdate_reason='协议已生效')
        _activate_after_verified_store_auth(store=self.store, actor_id=10, code=code)
        scheduled, code = self.create_code(kind='CHANGE', tier='ULTRA', starts_at=self.start, ends_at=self.end)
        _activate_after_verified_store_auth(store=self.store, actor_id=10, code=code)
        before = status_for_store(self.store, now=self.start - timedelta(microseconds=1))
        self.assertEqual((before['status'], before['tier']), ('ACTIVE', 'PRO'))
        self.assertEqual(before['current']['id'], str(current.pk))
        self.assertEqual(before['scheduled']['id'], str(scheduled.pk))
        after = status_for_store(self.store, now=self.start)
        self.assertEqual((after['status'], after['tier']), ('ACTIVE', 'ULTRA'))
        self.assertIsNone(after['scheduled'])

    def test_backdated_change_takes_effect_on_activation_and_preserves_history(self):
        first, code = self.create_code(starts_at=self.now - timedelta(days=10), backdate_reason='初次授权')
        with patch('stores.license_codes.timezone.now', return_value=self.now):
            _activate_after_verified_store_auth(store=self.store, actor_id=10, code=code)
        switch_time = self.now + timedelta(seconds=10)
        with patch('stores.license_codes.timezone.now', return_value=switch_time):
            changed, code = self.create_code(kind='CHANGE', tier='ULTRA', starts_at=self.now - timedelta(days=20),
                ends_at=self.end, backdate_reason='补录调档')
            _activate_after_verified_store_auth(store=self.store, actor_id=10, code=code)
        prior = status_for_store(self.store, now=self.now + timedelta(seconds=1))
        self.assertEqual(prior['current']['id'], str(first.pk))
        latest = status_for_store(self.store, now=switch_time)
        self.assertEqual((latest['current']['id'], latest['tier']), (str(changed.pk), 'ULTRA'))

    def test_backdate_and_platform_permissions(self):
        with self.assertRaisesMessage(LicenseCodeError, '填写原因'):
            self.create_code(starts_at=self.now - timedelta(days=1))
        self.create_code(starts_at=self.now - timedelta(days=1), backdate_reason='合同已确认')
        finance = PlatformAccount.objects.create(username='license-finance', phone='13800000001', is_phone_verified=True)
        PlatformRoleGrant.objects.create(account=finance, role='FINANCE', scope_slug='*')
        raw_token, _ = issue_tokens(finance, 'SMS', finance.phone)
        token = AccessToken(raw_token)
        with self.assertRaises(ValueError):
            issue(token, store_slug=self.other.slug, tier='BASIC', kind='INITIAL', starts_at=self.start, ends_at=self.end)

    def test_audit_failure_rolls_back_issuance_and_activation(self):
        with patch('stores.license_codes.PlatformAuditEvent.objects.create', side_effect=DatabaseError):
            with self.assertRaises(DatabaseError):
                self.create_code()
        self.assertFalse(StoreLicenseCode.objects.exists())
        self.assertFalse(StoreLicenseContract.objects.exists())
        row, code = self.create_code()
        with patch('stores.license_codes.StoreLicenseActivationAudit.objects.create', side_effect=DatabaseError):
            with self.assertRaises(DatabaseError):
                _activate_after_verified_store_auth(store=self.store, actor_id=10, code=code)
        row.refresh_from_db()
        self.assertIsNone(row.activated_at)


@skipUnlessDBFeature('has_select_for_update')
class LicenseCodeRaceTests(TransactionTestCase):
    def test_activation_and_void_serialize_on_store(self):
        store = Store.objects.first()
        actor = PlatformAccount.objects.create(username='void-activate-race', phone='13800000004', is_phone_verified=True)
        PlatformRoleGrant.objects.create(account=actor, role='SUPER_ADMIN', scope_slug='*')
        raw_token, _ = issue_tokens(actor, 'SMS', actor.phone)
        now = timezone.now()
        row, code = issue(AccessToken(raw_token), store_slug=store.slug, tier='BASIC', kind='INITIAL',
                          starts_at=now + timedelta(days=1), ends_at=now + timedelta(days=366))
        barrier = Barrier(2, timeout=15)

        def attempt(action):
            close_old_connections()
            try:
                barrier.wait()
                if action == 'activate':
                    try:
                        _activate_after_verified_store_auth(store=store, actor_id=10, code=code)
                        return 'activated'
                    except LicenseCodeError:
                        return 'already_voided'
                void(AccessToken(raw_token), code_id=row.pk, reason='平台取消计划')
                return 'voided'
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, ['activate', 'void']))
        self.assertIn(outcomes[0], ('activated', 'already_voided'))
        self.assertEqual(outcomes[1], 'voided')
        row.refresh_from_db()
        self.assertIsNotNone(row.voided_at)
        self.assertEqual(StoreLicenseActivationAudit.objects.filter(license_code=row).count(),
                         1 if outcomes[0] == 'activated' else 0)

    def test_concurrent_issue_serializes_pending_slot(self):
        store = Store.objects.first()
        actor = PlatformAccount.objects.create(username='race-owner', phone='13800000002', is_phone_verified=True)
        PlatformRoleGrant.objects.create(account=actor, role='SUPER_ADMIN', scope_slug='*')
        raw_token, _ = issue_tokens(actor, 'SMS', actor.phone)
        now = timezone.now()
        barrier = Barrier(2, timeout=15)

        def attempt():
            close_old_connections()
            try:
                barrier.wait()
                issue(AccessToken(raw_token), store_slug=store.slug, tier='PRO', kind='INITIAL',
                      starts_at=now + timedelta(days=1), ends_at=now + timedelta(days=366))
                return 'issued'
            except LicenseCodeError:
                return 'rejected'
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: attempt(), range(2)))
        self.assertCountEqual(outcomes, ['issued', 'rejected'])
        self.assertEqual(StoreLicenseCode.objects.count(), 1)

    def test_concurrent_activation_produces_one_audit(self):
        store = Store.objects.first()
        actor = PlatformAccount.objects.create(username='activate-race-owner', phone='13800000003', is_phone_verified=True)
        PlatformRoleGrant.objects.create(account=actor, role='SUPER_ADMIN', scope_slug='*')
        raw_token, _ = issue_tokens(actor, 'SMS', actor.phone)
        now = timezone.now()
        row, code = issue(AccessToken(raw_token), store_slug=store.slug, tier='BASIC', kind='INITIAL',
                          starts_at=now + timedelta(days=1), ends_at=now + timedelta(days=366))
        barrier = Barrier(2, timeout=15)

        def attempt(actor_id):
            close_old_connections()
            try:
                barrier.wait()
                _row, created = _activate_after_verified_store_auth(store=store, actor_id=actor_id, code=code)
                return created
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, [10, 11]))
        self.assertCountEqual(outcomes, [True, False])
        self.assertEqual(StoreLicenseActivationAudit.objects.filter(license_code=row).count(), 1)
