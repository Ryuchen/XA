from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from club_accounts.tokens import issue_account_tokens
from site_messages.models import LicenseNoticeDelivery, Message
from .context import resolve_store, store_context
from .license_codes import _activate_after_verified_store_auth, issue, void
from .license_notices import collect_store_notice, deliver_notice, drain_notices
from .models import (LicenseNotice, PlatformAccount, PlatformLicenseNotice, PlatformRoleGrant,
                     Store, StoreLicenseActivatorGrant, StoreProvisioningClaim)
from .platform_auth import issue_tokens
from .tests_foundation import admin
from .tests_hosts import xinan_store


@override_settings(PLATFORM_IDENTITY_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   LICENSE_NOTICE_DISPATCH_ENABLED=False, ALLOWED_HOSTS=['*'])
class LicenseNoticeTests(TestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first, self.second = resolve_store(), xinan_store()
        self.platform = PlatformAccount.objects.create(username='notices-owner', phone='13800001111', is_phone_verified=True)
        PlatformRoleGrant.objects.create(account=self.platform, role='SUPER_ADMIN', scope_slug='*')
        raw, _ = issue_tokens(self.platform, 'SMS', self.platform.phone)
        self.token = AccessToken(raw)
        self.client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + raw)
        with store_context(self.second):
            self.owner = admin('notice-owner', super_admin=True)
            self.delegate = admin('notice-delegate')
            self.staff = admin('notice-ordinary')
        StoreLicenseActivatorGrant.objects.create(store=self.second, account_id=self.delegate.pk,
            account_no=self.delegate.account_no, is_active=True, updated_by=self.platform)
        self.now = timezone.now()

    def create_active(self):
        row, code = issue(self.token, store_slug=self.second.slug, tier='PRO', kind='INITIAL',
            starts_at=self.now - timedelta(days=1), ends_at=self.now + timedelta(days=5), backdate_reason='测试')
        _activate_after_verified_store_auth(store=self.second, actor_id=self.owner.pk, code=code)
        return row

    def test_late_activation_is_one_current_reminder_and_tenant_receivers_are_isolated(self):
        row = self.create_active()
        event = LicenseNotice.objects.get(license_code=row)
        self.assertEqual(event.kind, 'D7')
        self.assertIsNone(event.platform_delivered_at)
        deliver_notice(event.pk)
        deliver_notice(event.pk)
        self.assertEqual(PlatformLicenseNotice.objects.filter(notice=event, account=self.platform).count(), 1)
        with store_context(self.second):
            self.assertEqual(LicenseNoticeDelivery.objects.filter(notice_id=event.pk).count(), 2)
            self.assertEqual(set(Message.objects.filter(title='门店授权即将到期').values_list('recipient_account_id', flat=True)),
                             {self.owner.pk, self.delegate.pk})
        with store_context(self.first):
            self.assertFalse(Message.objects.filter(title='门店授权即将到期').exists())
        self.assertEqual(self.client.get('/api/admin/platform/license-notices').data['data']['unread'], 1)
        delivery = PlatformLicenseNotice.objects.get(notice=event, account=self.platform)
        self.assertEqual(self.client.post(f'/api/admin/platform/license-notices/{delivery.pk}/read', {}, format='json').status_code, 200)
        self.assertEqual(self.client.get('/api/admin/platform/license-notices').data['data']['unread'], 0)
        later = collect_store_notice(self.second, now=row.ends_at - timedelta(hours=23))
        self.assertEqual(later.kind, 'D1')
        self.assertFalse(LicenseNotice.objects.filter(kind='D30').exists())

    def test_platform_read_receipt_is_scoped_to_own_account(self):
        other = PlatformAccount.objects.create(username='another-platform-owner', phone='13800002222', is_phone_verified=True)
        PlatformRoleGrant.objects.create(account=other, role='SUPER_ADMIN', scope_slug='*')
        raw, _ = issue_tokens(other, 'SMS', other.phone)
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + raw)
        row = self.create_active()
        event = LicenseNotice.objects.get(license_code=row)
        deliver_notice(event.pk)
        first = PlatformLicenseNotice.objects.get(notice=event, account=self.platform)
        second = PlatformLicenseNotice.objects.get(notice=event, account=other)
        self.assertEqual(client.post(f'/api/admin/platform/license-notices/{first.pk}/read', {}, format='json').status_code, 404)
        self.assertEqual(client.post(f'/api/admin/platform/license-notices/{second.pk}/read', {}, format='json').status_code, 200)
        first.refresh_from_db()
        self.assertFalse(first.is_read)

    def test_uninterrupted_renewal_suppresses_and_cancellation_resumes_reminder(self):
        row = self.create_active()
        original = LicenseNotice.objects.get(license_code=row, kind='D7')
        next_row, code = issue(self.token, store_slug=self.second.slug, tier='PRO', kind='RENEW',
            starts_at=row.ends_at, ends_at=row.ends_at + timedelta(days=365))
        _activate_after_verified_store_auth(store=self.second, actor_id=self.owner.pk, code=code)
        deliver_notice(original.pk)
        self.assertFalse(PlatformLicenseNotice.objects.exists())
        self.assertIsNone(collect_store_notice(self.second))
        void(self.token, code_id=next_row.pk, reason='重新安排')
        cancel = LicenseNotice.objects.get(license_code=next_row, kind='CANCEL')
        deliver_notice(cancel.pk)
        deliver_notice(original.pk)
        with store_context(self.second):
            self.assertEqual(Message.objects.filter(title='待生效授权已取消').count(), 2)
            self.assertEqual(Message.objects.filter(title='门店授权即将到期').count(), 2)

    def test_renewal_with_a_gap_does_not_suppress_current_expiry(self):
        row = self.create_active()
        event = LicenseNotice.objects.get(license_code=row, kind='D7')
        next_row, code = issue(self.token, store_slug=self.second.slug, tier='PRO', kind='RENEW',
            starts_at=row.ends_at + timedelta(days=1), ends_at=row.ends_at + timedelta(days=366))
        _activate_after_verified_store_auth(store=self.second, actor_id=self.owner.pk, code=code)
        deliver_notice(event.pk)
        self.assertTrue(PlatformLicenseNotice.objects.filter(notice=event, account=self.platform).exists())

    def test_tenant_failure_does_not_lose_platform_notice_and_retry_is_idempotent(self):
        row = self.create_active()
        event = LicenseNotice.objects.get(license_code=row)
        with patch('site_messages.models.LicenseNoticeDelivery.objects.get_or_create', side_effect=DatabaseError('test failure')):
            drain_notices()
        event.refresh_from_db()
        self.assertIsNotNone(event.platform_delivered_at)
        self.assertIsNone(event.store_delivered_at)
        self.assertEqual(PlatformLicenseNotice.objects.filter(notice=event).count(), 1)
        with patch('stores.license_notices.timezone.now', return_value=self.now + timedelta(seconds=61)):
            drain_notices()
        event.refresh_from_db()
        self.assertIsNotNone(event.store_delivered_at)
        with store_context(self.second):
            self.assertEqual(LicenseNoticeDelivery.objects.filter(notice_id=event.pk).count(), 2)

    def test_retry_after_tenant_commit_and_directory_failure_does_not_duplicate(self):
        row = self.create_active()
        event = LicenseNotice.objects.get(license_code=row)
        original_save = LicenseNotice.save

        def fail_directory_marker(instance, *args, **kwargs):
            if kwargs.get('update_fields') == ['store_delivered_at']:
                raise DatabaseError('directory marker failure')
            return original_save(instance, *args, **kwargs)

        with patch.object(LicenseNotice, 'save', fail_directory_marker):
            with self.assertRaises(DatabaseError):
                deliver_notice(event.pk)
        with store_context(self.second):
            self.assertEqual(LicenseNoticeDelivery.objects.filter(notice_id=event.pk).count(), 2)
            self.assertEqual(Message.objects.filter(title='门店授权即将到期').count(), 2)
        event.refresh_from_db()
        self.assertIsNone(event.store_delivered_at)
        deliver_notice(event.pk)
        with store_context(self.second):
            self.assertEqual(LicenseNoticeDelivery.objects.filter(notice_id=event.pk).count(), 2)
            self.assertEqual(Message.objects.filter(title='门店授权即将到期').count(), 2)

    def test_delayed_worker_discards_stale_notice(self):
        row = self.create_active()
        event = LicenseNotice.objects.get(license_code=row)
        with patch('stores.license_notices.timezone.now', return_value=row.ends_at + timedelta(days=1)):
            deliver_notice(event.pk)
        event.refresh_from_db()
        self.assertIsNotNone(event.suppressed_at)
        self.assertFalse(PlatformLicenseNotice.objects.exists())

    def test_delayed_worker_does_not_send_previous_threshold(self):
        row, code = issue(self.token, store_slug=self.second.slug, tier='PRO', kind='INITIAL',
            starts_at=self.now - timedelta(days=1), ends_at=self.now + timedelta(days=20), backdate_reason='测试')
        _activate_after_verified_store_auth(store=self.second, actor_id=self.owner.pk, code=code)
        old = LicenseNotice.objects.get(license_code=row, kind='D30')
        current_time = row.ends_at - timedelta(days=6)
        with patch('stores.license_notices.timezone.now', return_value=current_time):
            deliver_notice(old.pk)
        old.refresh_from_db()
        self.assertIsNotNone(old.suppressed_at)
        self.assertFalse(PlatformLicenseNotice.objects.filter(notice=old).exists())
        self.assertEqual(collect_store_notice(self.second, now=current_time).kind, 'D7')

    def test_outbox_write_failure_rolls_back_late_activation(self):
        row, code = issue(self.token, store_slug=self.second.slug, tier='PRO', kind='INITIAL',
            starts_at=self.now - timedelta(days=1), ends_at=self.now + timedelta(days=5), backdate_reason='测试')
        with patch('stores.license_notices.LicenseNotice.objects.get_or_create', side_effect=DatabaseError('test failure')):
            with self.assertRaises(DatabaseError):
                _activate_after_verified_store_auth(store=self.second, actor_id=self.owner.pk, code=code)
        row.refresh_from_db()
        self.assertIsNone(row.activated_at)
        self.assertFalse(LicenseNotice.objects.exists())

    def test_offline_store_keeps_platform_notice_and_defers_retry(self):
        row = self.create_active()
        event = LicenseNotice.objects.get(license_code=row)
        self.second.database_alias = 'offline_database'
        self.second.save(update_fields=['database_alias'])
        drain_notices(batch_size=1)
        event.refresh_from_db()
        self.assertIsNotNone(event.platform_delivered_at)
        self.assertIsNone(event.store_delivered_at)
        self.assertIsNotNone(event.next_attempt_at)

    def test_failed_notice_is_deferred_so_it_does_not_starve_the_next_batch(self):
        row = self.create_active()
        first = LicenseNotice.objects.get(license_code=row)
        second = LicenseNotice.objects.create(event_key=f'D1:{row.pk}', store=self.second, license_code=row,
                                              kind='D1', deadline=row.ends_at)
        with patch('stores.license_notices.deliver_notice', side_effect=DatabaseError('offline')) as delivery:
            drain_notices(batch_size=1)
            drain_notices(batch_size=1)
        self.assertCountEqual([call.args[0] for call in delivery.call_args_list], [first.pk, second.pk])

    def test_grace_setting_can_make_previously_suppressed_notice_due_again(self):
        from .license_status import change_grace
        row = self.create_active()
        at_expiration = row.ends_at + timedelta(days=2)
        change_grace(self.token, days=1, store_id=self.second.pk)
        event = collect_store_notice(self.second, now=at_expiration)
        self.assertEqual(event.kind, 'GRACE_END')
        change_grace(self.token, days=7, store_id=self.second.pk)
        with patch('stores.license_notices.timezone.now', return_value=at_expiration):
            deliver_notice(event.pk)
        event.refresh_from_db()
        self.assertIsNotNone(event.suppressed_at)
        change_grace(self.token, days=1, store_id=self.second.pk)
        revived = collect_store_notice(self.second, now=at_expiration)
        self.assertEqual(revived.pk, event.pk)
        self.assertIsNone(revived.suppressed_at)
        with patch('stores.license_notices.timezone.now', return_value=at_expiration):
            deliver_notice(event.pk)
        self.assertTrue(PlatformLicenseNotice.objects.filter(notice=event, account=self.platform).exists())

    def test_expiration_notifies_store_and_platform_then_grace_end_notifies_separately(self):
        row = self.create_active()
        at_expiration = row.ends_at + timedelta(seconds=1)
        event = collect_store_notice(self.second, now=at_expiration)
        self.assertEqual(event.kind, 'EXPIRY')
        with patch('stores.license_notices.timezone.now', return_value=at_expiration):
            deliver_notice(event.pk)
            deliver_notice(event.pk)
        self.assertEqual(PlatformLicenseNotice.objects.filter(notice=event, account=self.platform).count(), 1)
        platform_notices = self.client.get('/api/admin/platform/license-notices')
        self.assertIn('门店授权已到期', [row['title'] for row in platform_notices.data['data']['results']])
        with store_context(self.second):
            self.assertEqual(set(Message.objects.filter(title='门店授权已到期').values_list('recipient_account_id', flat=True)),
                             {self.owner.pk, self.delegate.pk})
            self.owner.is_phone_verified = True
            self.owner.save(update_fields=['is_phone_verified'])
            token, _ = issue_account_tokens(self.owner, auth_method='password_sms')
        owner_client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        messages = owner_client.get('/api/messages/')
        self.assertIn('门店授权已到期', [row['title'] for row in messages.data['data']['messages']])
        after_grace = row.ends_at + timedelta(days=7, seconds=1)
        grace_event = collect_store_notice(self.second, now=after_grace)
        self.assertEqual(grace_event.kind, 'GRACE_END')
        with patch('stores.license_notices.timezone.now', return_value=after_grace):
            deliver_notice(grace_event.pk)
        with store_context(self.second):
            self.assertEqual(Message.objects.filter(title='授权宽限期已结束').count(), 2)

    def test_zero_grace_sends_expiration_only_and_old_grace_deadline_is_not_used(self):
        from .license_status import change_grace
        row = self.create_active()
        at_expiration = row.ends_at + timedelta(seconds=1)
        change_grace(self.token, days=0, store_id=self.second.pk)
        event = collect_store_notice(self.second, now=at_expiration)
        self.assertEqual(event.kind, 'EXPIRY')
        with patch('stores.license_notices.timezone.now', return_value=at_expiration):
            deliver_notice(event.pk)
        self.assertFalse(LicenseNotice.objects.filter(license_code=row, kind='GRACE_END').exists())
        change_grace(self.token, days=1, store_id=self.second.pk)
        later = row.ends_at + timedelta(days=2)
        stale = collect_store_notice(self.second, now=later)
        change_grace(self.token, days=2, store_id=self.second.pk)
        with patch('stores.license_notices.timezone.now', return_value=later):
            deliver_notice(stale.pk)
        stale.refresh_from_db()
        self.assertIsNotNone(stale.suppressed_at)
        current = collect_store_notice(self.second, now=later)
        self.assertNotEqual(stale.pk, current.pk)
        self.assertEqual(current.deadline, row.ends_at + timedelta(days=2))

    def test_store_recovery_gets_only_latest_undelivered_reminder(self):
        row, code = issue(self.token, store_slug=self.second.slug, tier='PRO', kind='INITIAL',
            starts_at=self.now - timedelta(days=1), ends_at=self.now + timedelta(days=20), backdate_reason='测试')
        _activate_after_verified_store_auth(store=self.second, actor_id=self.owner.pk, code=code)
        old = LicenseNotice.objects.get(license_code=row, kind='D30')
        self.second.database_alias = 'offline_database'
        self.second.save(update_fields=['database_alias'])
        deliver_notice(old.pk)
        old.refresh_from_db()
        self.assertIsNotNone(old.platform_delivered_at)
        self.assertIsNone(old.store_delivered_at)
        current_time = row.ends_at - timedelta(days=6)
        current = collect_store_notice(self.second, now=current_time)
        old.refresh_from_db()
        self.assertEqual(current.kind, 'D7')
        self.assertIsNotNone(old.suppressed_at)
        self.second.database_alias = 'store_b'
        self.second.save(update_fields=['database_alias'])
        with patch('stores.license_notices.timezone.now', return_value=current_time):
            deliver_notice(old.pk)
            deliver_notice(current.pk)
        with store_context(self.second):
            self.assertEqual(Message.objects.filter(title='门店授权即将到期').count(), 2)
            self.assertFalse(LicenseNoticeDelivery.objects.filter(notice_id=old.pk).exists())
            self.assertEqual(LicenseNoticeDelivery.objects.filter(notice_id=current.pk).count(), 2)

    def test_task_is_global_not_implicitly_scoped_to_shenglang(self):
        from .license_tasks import deliver_license_notices
        from .tasks import StoreTask
        self.assertNotIsInstance(deliver_license_notices, StoreTask)

    @override_settings(LICENSE_NOTICE_DISPATCH_ENABLED=True)
    def test_actual_celery_task_scans_and_delivers_to_nondefault_store(self):
        from .license_tasks import deliver_license_notices
        row = self.create_active()
        result = deliver_license_notices.apply().get(propagate=True)
        self.assertGreaterEqual(result, 1)
        event = LicenseNotice.objects.get(license_code=row, kind='D7')
        self.assertTrue(PlatformLicenseNotice.objects.filter(notice=event, account=self.platform).exists())
        with store_context(self.second):
            self.assertEqual(LicenseNoticeDelivery.objects.filter(notice_id=event.pk).count(), 2)

    def test_first_store_admin_can_read_own_notices_during_setup(self):
        row = self.create_active()
        event = LicenseNotice.objects.get(license_code=row)
        deliver_notice(event.pk)
        self.second.status = Store.Status.CONFIGURING
        self.second.save(update_fields=['status'])
        StoreProvisioningClaim.objects.create(store=self.second, database_fingerprint='a' * 64,
                                               admin_username=self.owner.username)
        with store_context(self.second):
            self.owner.is_phone_verified = True
            self.owner.save(update_fields=['is_phone_verified'])
            token, _ = issue_account_tokens(self.owner, auth_method='password_sms')
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        response = client.get('/api/messages/')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn('门店授权即将到期', [item['title'] for item in response.data['data']['messages']])
        message_id = response.data['data']['messages'][0]['id']
        self.assertEqual(client.get(f'/api/messages/{message_id}/').status_code, 200)
