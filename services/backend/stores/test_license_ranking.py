"""Store-bound leaderboard admission using real License and HTTP code."""
from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens
from stores.context import store_context
from stores.license_features import change_feature
from stores.test_license_account_admission import LicenseAccountAdmissionTests


@override_settings(LICENSE_ACCOUNT_ADMISSION_ENABLED=False, LICENSE_BENEFIT_ENFORCEMENT_ENABLED=True,
                   LICENSE_SNAPSHOT_CACHE_ENABLED=True, LICENSE_NOTICE_DISPATCH_ENABLED=False,
                   LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class LicenseRankingTests(TestCase):
    databases = {'default', 'store_b'}
    setUp = LicenseAccountAdmissionTests.setUp

    def client_for(self, store, host):
        with store_context(store):
            account = ClubAccount.objects.create_account(username='ranking-boss', account_type='BOSS')
            token, _ = issue_account_tokens(account)
        return APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token)

    def test_live_tier_override_is_per_store_and_does_not_block_core(self):
        first = self.client_for(self.first, 'sl.esports.club')
        second = self.client_for(self.second, 'xa.esports.club')
        path = '/api/orders/rankings/'
        self.assertEqual(second.get(path).status_code, 403)  # Basic default OFF
        change_feature(self.platform_token, tier='BASIC', feature='ACHIEVEMENTS_RANKINGS', mode='FULL')
        response = second.get(path)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn('consume_rank', response.data['data'])
        self.assertEqual(first.get(path).status_code, 403)  # first store is unlicensed
        self.assertEqual(first.get('/api/users/me/').status_code, 200)
        self.assertEqual(second.get('/api/users/me/').status_code, 200)
        self.assertEqual(first.get('/api/orders/orders/').status_code, 200)
        self.assertEqual(second.get('/api/orders/orders/').status_code, 200)
        self.assertEqual(second.get(path, HTTP_HOST='sl.esports.club').status_code, 401)
        change_feature(self.platform_token, tier='BASIC', feature='ACHIEVEMENTS_RANKINGS', mode='OFF')
        self.assertEqual(second.get(path).status_code, 403)

    def test_grace_expiry_and_signed_cache_fail_closed(self):
        client = self.client_for(self.second, 'xa.esports.club')
        change_feature(self.platform_token, tier='BASIC', feature='ACHIEVEMENTS_RANKINGS', mode='FULL')
        self.license.ends_at = timezone.now() - timedelta(days=1)
        self.license.starts_at = timezone.now() - timedelta(days=30)
        self.license.activated_at = timezone.now() - timedelta(days=29)
        self.license.save(update_fields=['starts_at', 'ends_at', 'activated_at'])
        with self.captureOnCommitCallbacks(using='default', execute=True), self.captureOnCommitCallbacks(using='store_b', execute=True):
            self.assertEqual(client.get('/api/orders/rankings/').status_code, 200)
        with patch('stores.license_status.status_for_store', side_effect=DatabaseError('offline')):
            self.assertEqual(client.get('/api/orders/rankings/').status_code, 200)
            with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(days=7)):
                self.assertEqual(client.get('/api/orders/rankings/').status_code, 403)
        self.license.ends_at = timezone.now() - timedelta(days=8)
        self.license.save(update_fields=['ends_at'])
        self.assertEqual(client.get('/api/orders/rankings/').status_code, 403)
        self.assertEqual(client.get('/api/orders/orders/').status_code, 200)
        first = self.client_for(self.first, 'sl.esports.club')
        with patch('stores.license_status.status_for_store', side_effect=DatabaseError('offline')):
            self.assertEqual(first.get('/api/orders/rankings/').status_code, 403)
            self.assertEqual(first.get('/api/orders/orders/').status_code, 200)

