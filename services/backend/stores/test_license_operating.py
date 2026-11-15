from datetime import timedelta
from unittest.mock import patch
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.tokens import issue_account_tokens
from console.dictionary_models import BusinessDictionaryItem
from console.payroll_models import OperatingCost
from stores.context import store_context
from stores.license_features import change_feature
from stores import test_license_account_admission as fixture
from stores.tests_foundation import admin


@override_settings(LICENSE_ACCOUNT_ADMISSION_ENABLED=False, LICENSE_BENEFIT_ENFORCEMENT_ENABLED=True,
                   LICENSE_SNAPSHOT_CACHE_ENABLED=True, LICENSE_NOTICE_DISPATCH_ENABLED=False,
                   LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class OperatingLicenseTests(TestCase):
    databases = {'default', 'store_b'}
    setUp = fixture.LicenseAccountAdmissionTests.setUp
    cost = '/api/admin/operating-costs/'
    roi = '/api/admin/operating-roi/?start_date=2026-10-01&end_date=2026-10-31'

    def client_for(self, store, host, perms):
        with store_context(store):
            staff = admin('cost-reader')
            role = staff.admin_membership.roles.first()
            role.permissions = perms
            role.save(update_fields=['permissions'])
            token, _ = issue_account_tokens(staff)
        return APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token)

    def mode(self, value):
        change_feature(self.platform_token, tier='BASIC', feature='OPERATING_COST_ROI', mode=value)

    def test_modes_permissions_and_isolation(self):
        client = self.client_for(self.second, 'xa.esports.club', ['cost:view', 'cost:edit', 'roi:view'])
        other = self.client_for(self.first, 'sl.esports.club', ['cost:view'])
        with store_context(self.second):
            category, _ = BusinessDictionaryItem.objects.get_or_create(type='COST_CATEGORY', code='TEST_COST', defaults={'name': '测试成本'})
        payload = {'day': '2026-10-15', 'category': category.pk, 'amount_fen': 1234, 'client_request_id': 'isolated-cost'}
        self.assertEqual(client.get(self.cost).status_code, 403)
        self.mode('BASIC')
        created = client.post(self.cost, payload, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        pk = created.data['data']['id']
        self.assertEqual(client.get(self.cost).data['data']['total'], 1)
        self.assertEqual(client.get(self.roi).status_code, 403)
        self.assertEqual(other.get(self.cost).status_code, 403)
        self.assertEqual(client.get(self.cost, HTTP_HOST='sl.esports.club').status_code, 401)
        self.mode('FULL')
        self.assertEqual(client.get(self.roi).status_code, 200)
        self.mode('READ_ONLY')
        self.assertEqual(client.get(f'{self.cost}{pk}/').status_code, 200)
        self.assertEqual(client.patch(f'{self.cost}{pk}/', {'amount_fen': 5, 'revision': 1}, format='json').status_code, 403)
        self.assertEqual(client.get(self.roi).status_code, 403)
        self.mode('OFF')
        self.assertEqual(client.get(self.cost).status_code, 403)
        with store_context(self.second):
            self.assertEqual(OperatingCost.objects.get(pk=pk).amount_fen, 1234)
            staff = admin('cost-no-permission')
            token, _ = issue_account_tokens(staff)
        no_perm = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        self.mode('FULL')
        self.assertEqual(no_perm.get(self.cost).status_code, 403)
        self.assertEqual(no_perm.get(self.roi).status_code, 403)

    def test_default_tiers(self):
        client = self.client_for(self.second, 'xa.esports.club', ['cost:view', 'roi:view'])
        for tier, cost_status, roi_status in (('BASIC', 403, 403), ('PRO', 200, 403), ('ULTRA', 200, 200)):
            self.license.tier = tier
            self.license.save(update_fields=['tier'])
            self.assertEqual(client.get(self.cost).status_code, cost_status)
            self.assertEqual(client.get(self.roi).status_code, roi_status)

    def test_expiry_grace_and_signed_cache(self):
        client = self.client_for(self.second, 'xa.esports.club', ['cost:view', 'cost:edit', 'roi:view'])
        other = self.client_for(self.first, 'sl.esports.club', ['cost:view'])
        self.mode('FULL')
        self.license.starts_at = timezone.now() - timedelta(days=30)
        self.license.activated_at = timezone.now() - timedelta(days=29)
        self.license.ends_at = timezone.now() - timedelta(days=1)
        self.license.save(update_fields=['starts_at', 'activated_at', 'ends_at'])
        with self.captureOnCommitCallbacks(using='default', execute=True), self.captureOnCommitCallbacks(using='store_b', execute=True):
            self.assertEqual(client.get(self.roi).status_code, 200)
        with patch('stores.license_status.status_for_store', side_effect=DatabaseError('offline')):
            self.assertEqual(client.get(self.roi).status_code, 200)
            with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(days=7)):
                self.assertEqual(client.get(self.cost).status_code, 200)
                self.assertEqual(client.get(self.roi).status_code, 403)
                self.assertEqual(client.post(self.cost, {}, format='json').status_code, 403)
            self.assertEqual(other.get(self.cost).status_code, 503)
