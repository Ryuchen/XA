"""Audit view entitlements on actual store-bound HTTP and database paths."""
from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog
from stores.context import store_context
from stores.license_features import change_feature
from stores import test_license_account_admission as admission_fixture
from stores.tests_foundation import admin


@override_settings(LICENSE_ACCOUNT_ADMISSION_ENABLED=False, LICENSE_BENEFIT_ENFORCEMENT_ENABLED=True,
                   LICENSE_SNAPSHOT_CACHE_ENABLED=True, LICENSE_NOTICE_DISPATCH_ENABLED=False,
                   LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class AuditLicenseTests(TestCase):
    databases = {'default', 'store_b'}
    setUp = admission_fixture.LicenseAccountAdmissionTests.setUp

    def client_for(self, store, host, *, can_view=True):
        with store_context(store):
            staff = admin('audit-reader')
            role = staff.admin_membership.roles.first()
            role.permissions = ['announcement:edit'] + (['audit:view'] if can_view else [])
            role.save(update_fields=['permissions'])
            token, _ = issue_account_tokens(staff)
        return APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token)

    def seed(self, store):
        with store_context(store):
            recent = AdminAuditLog.objects.create(method='POST', path='/test/recent', status_code=200)
            old = AdminAuditLog.objects.create(method='POST', path='/test/old', status_code=200)
            AdminAuditLog.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=31))
            return recent.pk, old.pk

    def test_recent_full_off_and_store_roles(self):
        staff = self.client_for(self.second, 'xa.esports.club')
        first = self.client_for(self.first, 'sl.esports.club')
        recent, old = self.seed(self.second)
        self.seed(self.first)
        url = '/api/admin/audit-logs/'
        data = staff.get(url).data['data']
        self.assertEqual([item['id'] for item in data['list']], [recent])
        self.assertEqual(staff.get(f'{url}{old}/').status_code, 404)
        self.assertEqual(staff.get(f'{url}?start_date=2000-01-01').data['data']['total'], 1)
        self.assertEqual(first.get(url).status_code, 403)
        change_feature(self.platform_token, tier='BASIC', feature='AUDIT_LOG_VIEW', mode='FULL')
        self.assertEqual(staff.get(url).data['data']['total'], 2)
        self.assertEqual(staff.get(f'{url}{old}/').status_code, 200)
        self.assertEqual(staff.get(url, HTTP_HOST='sl.esports.club').status_code, 401)
        with store_context(self.second):
            no_perm = admin('audit-no-permission')
            token, _ = issue_account_tokens(no_perm)
        denied = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(denied.get(url).status_code, 403)
        change_feature(self.platform_token, tier='BASIC', feature='AUDIT_LOG_VIEW', mode='OFF')
        self.assertEqual(staff.get(url).status_code, 403)
        self.assertIn(staff.post('/api/admin/announcements/', {'title': '审计写入', 'content': '内容'}, format='json').status_code, (200, 201))
        with store_context(self.second):
            self.assertTrue(AdminAuditLog.objects.filter(path='/api/admin/announcements/').exists())
            self.assertTrue(AdminAuditLog.objects.filter(pk=old).exists())

    def test_cache_grace_expired_and_no_cache(self):
        staff = self.client_for(self.second, 'xa.esports.club')
        first = self.client_for(self.first, 'sl.esports.club')
        self.seed(self.second)
        change_feature(self.platform_token, tier='BASIC', feature='AUDIT_LOG_VIEW', mode='FULL')
        self.license.ends_at = timezone.now() - timedelta(days=1)
        self.license.starts_at = timezone.now() - timedelta(days=30)
        self.license.activated_at = timezone.now() - timedelta(days=29)
        self.license.save(update_fields=['starts_at', 'ends_at', 'activated_at'])
        url = '/api/admin/audit-logs/'
        with self.captureOnCommitCallbacks(using='default', execute=True), self.captureOnCommitCallbacks(using='store_b', execute=True):
            self.assertEqual(staff.get(url).data['data']['total'], 2)
        with patch('stores.license_status.status_for_store', side_effect=DatabaseError('offline')):
            self.assertEqual(staff.get(url).data['data']['total'], 2)
            with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(days=7)):
                self.assertEqual(staff.get(url).data['data']['total'], 1)
            self.assertEqual(first.get(url).status_code, 503)
        self.license.ends_at = timezone.now() - timedelta(days=8)
        self.license.save(update_fields=['ends_at'])
        self.assertEqual(staff.get(url).data['data']['total'], 1)

    def test_recent_cutoff_is_inclusive_and_filter_cannot_expand_it(self):
        staff = self.client_for(self.second, 'xa.esports.club')
        now = timezone.now()
        with store_context(self.second):
            at = AdminAuditLog.objects.create(method='POST', path='/test/at', status_code=200)
            before = AdminAuditLog.objects.create(method='POST', path='/test/before', status_code=200)
            cutoff = now - timedelta(days=30)
            AdminAuditLog.objects.filter(pk=at.pk).update(created_at=cutoff)
            AdminAuditLog.objects.filter(pk=before.pk).update(created_at=cutoff - timedelta(microseconds=1))
        with patch('django.utils.timezone.now', return_value=now):
            response = staff.get('/api/admin/audit-logs/?start_date=2000-01-01')
        self.assertEqual([row['id'] for row in response.data['data']['list']], [at.pk])
