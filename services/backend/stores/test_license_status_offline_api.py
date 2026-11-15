from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import DatabaseError, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.models import LicenseStatusSnapshot
from club_accounts.tokens import issue_account_tokens
from stores.context import resolve_store, store_context
from stores.models import (PlatformAccount, PlatformLicenseSettings, Store,
                           StoreLicenseCode, StoreLicenseContract)
from stores.tests_foundation import admin


@skipUnless(getattr(settings, 'ISOLATED_CONTROL_PLANE', False) and
            {'store_a', 'store_b'}.issubset(settings.DATABASES), 'requires isolated three-database settings')
@override_settings(LICENSE_SNAPSHOT_CACHE_ENABLED=True, LICENSE_ACCOUNT_ADMISSION_ENABLED=False,
                   PLATFORM_IDENTITY_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class StoreLicenseStatusOfflineTests(TransactionTestCase):
    databases = ({'default', 'store_a', 'store_b'} if {'store_a', 'store_b'}.issubset(settings.DATABASES)
                 else {'default'})

    def test_store_admin_reads_verified_cached_status_during_control_outage(self):
        now = timezone.now()
        store = Store.objects.create(slug='xinan', name='兴安电竞', currency_name='星辉',
            database_alias='store_b', status='ACTIVE', initialized_at=now)
        platform = PlatformAccount.objects.create(username='license-status-owner', phone='13800001111')
        PlatformLicenseSettings.objects.create(default_grace_days=0)
        contract = StoreLicenseContract.objects.create(store=store, amount_due_fen=0,
            created_by=platform, updated_by=platform)
        StoreLicenseCode.objects.create(store=store, contract=contract, code_digest='a' * 64,
            tier='PRO', kind='INITIAL', starts_at=now - timedelta(days=1), ends_at=now + timedelta(minutes=1),
            activated_at=now - timedelta(hours=1), activated_by_id=1, created_by=platform)
        with store_context(store):
            owner = admin('license-status-admin', super_admin=True)
            owner.is_phone_verified = True
            owner.save(update_fields=['is_phone_verified'])
            token, _ = issue_account_tokens(owner, auth_method='password_sms')
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        resolve_store('xinan')
        online = client.get('/api/admin/license/activate')
        self.assertEqual(online.status_code, 200, online.data)
        self.assertEqual((online.data['data']['status'], online.data['data']['license_source']), ('ACTIVE', 'ONLINE'))
        self.assertTrue(online.data['data']['grace_settings_available'])
        self.assertNotIn('verified_snapshot', online.data['data'])

        def fail_control_sql(_execute, _sql, _params, _many, _context):
            raise DatabaseError('simulated control outage')

        with connections['default'].execute_wrapper(fail_control_sql):
            cached = client.get('/api/admin/license/activate')
            self.assertEqual(cached.status_code, 200, cached.data)
            self.assertEqual((cached.data['data']['status'], cached.data['data']['license_source']), ('ACTIVE', 'CACHE'))
            self.assertFalse(cached.data['data']['grace_settings_available'])
            self.assertIsNone(cached.data['data']['default_grace_days'])
            self.assertNotIn('verified_snapshot', cached.data['data'])
            with patch('django.utils.timezone.now', return_value=now + timedelta(minutes=2)):
                expired = client.get('/api/admin/license/activate')
            self.assertEqual(expired.data['data']['status'], 'EXPIRED')
            self.assertFalse(expired.data['data']['allow_new_accounts'])
            with store_context(store):
                LicenseStatusSnapshot.objects.filter(pk=1).delete()
            missing = client.get('/api/admin/license/activate')
            self.assertEqual(missing.status_code, 503, missing.data)
            self.assertIn('联系平台', missing.data['msg'])

        restored = client.get('/api/admin/license/activate')
        self.assertEqual((restored.data['data']['status'], restored.data['data']['license_source']), ('ACTIVE', 'ONLINE'))
