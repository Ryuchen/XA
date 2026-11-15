from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from console.middleware import _mask
from .models import PlatformAccount, PlatformAuditEvent, PlatformRoleGrant, Store, StoreLicenseCode, StoreLicenseContract
from .platform_auth import issue_tokens


@override_settings(PLATFORM_IDENTITY_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class PlatformLicenseApiTests(TestCase):
    def setUp(self):
        self.store = Store.objects.first()
        self.actor = PlatformAccount.objects.create(username='license-api-owner', phone='13800000010', is_phone_verified=True)
        PlatformRoleGrant.objects.create(account=self.actor, role='SUPER_ADMIN', scope_slug='*')
        token, _ = issue_tokens(self.actor, 'SMS', self.actor.phone)
        self.client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        self.url = f'/api/admin/platform/store-management/{self.store.pk}/licenses'
        now = timezone.now()
        self.data = {'tier': 'BASIC', 'kind': 'INITIAL', 'starts_at': (now + timedelta(minutes=5)).isoformat(),
                     'ends_at': (now + timedelta(days=365)).isoformat()}

    def test_issue_list_void_do_not_reveal_code_again_and_audit_is_safe(self):
        created = self.client.post(self.url, self.data, format='json')
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created['Cache-Control'], 'no-store')
        code = created.data['data']['license_code']
        code_id = created.data['data']['id']
        self.assertNotIn(code, str(StoreLicenseCode.objects.get(pk=code_id).__dict__))
        listed = self.client.get(self.url)
        self.assertEqual(listed.status_code, 200, listed.content)
        self.assertNotIn(code, str(listed.data))
        self.assertNotIn('code_digest', str(listed.data))
        rejected = self.client.post(self.url, self.data, format='json')
        self.assertEqual(rejected.status_code, 400)
        revoked = self.client.post(f'/api/admin/platform/licenses/{code_id}/void', {'reason': '授权码丢失'}, format='json')
        self.assertEqual(revoked.status_code, 200, revoked.content)
        self.assertNotIn(code, str(revoked.data))
        replacement = self.client.post(self.url, self.data, format='json')
        self.assertEqual(replacement.status_code, 201, replacement.content)
        self.assertNotEqual(replacement.data['data']['license_code'], code)
        self.assertNotIn(code, str(list(PlatformAuditEvent.objects.values_list('detail', flat=True))))

    def test_finance_scoped_super_admin_and_anonymous_are_denied(self):
        for index, (role, scope) in enumerate([('FINANCE', '*'), ('SUPER_ADMIN', self.store.slug)]):
            actor = PlatformAccount.objects.create(username=f'license-api-denied-{index}',
                phone='13800000011', is_phone_verified=True)
            PlatformRoleGrant.objects.create(account=actor, role=role, scope_slug=scope)
            token, _ = issue_tokens(actor, 'SMS', actor.phone)
            client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
            self.assertEqual(client.post(self.url, self.data, format='json').status_code, 403)
            self.assertEqual(client.get(self.url).status_code, 403)
        self.assertEqual(APIClient(HTTP_HOST='admin.esports.club').post(self.url, self.data, format='json').status_code, 401)
        self.assertFalse(StoreLicenseCode.objects.exists())

    def test_invalid_inputs_and_audit_failure_do_not_issue(self):
        for changes in ({'tier': []}, {'starts_at': 'not-a-date'}, {'ends_at': '2027-01-01'}, {'extra': 1},
                        {'starts_at': '9999-12-31T23:59:59+00:00', 'ends_at': None}):
            self.assertEqual(self.client.post(self.url, {**self.data, **changes}, format='json').status_code, 400)
        self.assertEqual(self.client.post(self.url, {'tier': 'PRO', 'kind': 'INITIAL',
            'starts_at': '9999-12-31T23:59:59+00:00'}, format='json').status_code, 400)
        with patch('stores.license_codes.PlatformAuditEvent.objects.create', side_effect=DatabaseError):
            self.assertEqual(self.client.post(self.url, self.data, format='json').status_code, 503)
        self.assertFalse(StoreLicenseCode.objects.exists())

    def test_generic_admin_audit_masks_nested_license_codes(self):
        self.assertEqual(_mask({'license_code': 'secret', 'nested': [{'licenseCode': 'secret2'}]}),
                         {'license_code': '***', 'nested': [{'licenseCode': '***'}]})

    def test_issue_defaults_to_one_calendar_year(self):
        created = self.client.post(self.url, {'tier': 'BASIC', 'kind': 'INITIAL'}, format='json')
        self.assertEqual(created.status_code, 201, created.content)
        self.assertTrue(created.data['data']['starts_at'])
        self.assertTrue(created.data['data']['ends_at'])

    def test_grace_changes_recalculate_store_status_immediately_and_are_audited(self):
        now = timezone.now()
        contract = StoreLicenseContract.objects.create(store=self.store, amount_due_fen=0, created_by=self.actor, updated_by=self.actor)
        StoreLicenseCode.objects.create(store=self.store, contract=contract, code_digest='a' * 64, tier='BASIC', kind='INITIAL',
            starts_at=now - timedelta(days=30), ends_at=now - timedelta(days=1),
            activated_at=now - timedelta(days=29), activated_by_id=1, created_by=self.actor)
        url = f'/api/admin/platform/store-management/{self.store.pk}/license-status'
        status = self.client.get(url)
        self.assertEqual(status.data['data']['status'], 'GRACE')
        self.assertEqual(status.data['data']['grace_days'], 7)
        changed = self.client.put(url, {'grace_days_override': 0}, format='json')
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual(changed.data['data']['status'], 'EXPIRED')
        changed = self.client.put('/api/admin/platform/license-settings', {'default_grace_days': 10}, format='json')
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual(self.client.get(url).data['data']['status'], 'EXPIRED')
        restored = self.client.put(url, {'grace_days_override': None}, format='json')
        self.assertEqual(restored.data['data']['status'], 'GRACE')
        self.assertEqual(restored.data['data']['grace_days'], 10)
        self.assertEqual(PlatformAuditEvent.objects.filter(action='license.grace_changed').count(), 3)
        for value in (True, -1, '7', None):
            self.assertEqual(self.client.put('/api/admin/platform/license-settings',
                {'default_grace_days': value}, format='json').status_code, 400)

    def test_grace_audit_failure_rolls_back_change(self):
        with patch('stores.license_status.PlatformAuditEvent.objects.create', side_effect=DatabaseError):
            result = self.client.put('/api/admin/platform/license-settings', {'default_grace_days': 10}, format='json')
        self.assertEqual(result.status_code, 503)
        current = self.client.get('/api/admin/platform/license-settings')
        self.assertEqual(current.data['data']['default_grace_days'], 7)

    def test_tier_features_update_store_status_immediately_and_cannot_modify_core(self):
        now = timezone.now()
        contract = StoreLicenseContract.objects.create(store=self.store, amount_due_fen=0, created_by=self.actor, updated_by=self.actor)
        StoreLicenseCode.objects.create(store=self.store, contract=contract, code_digest='b' * 64, tier='BASIC', kind='INITIAL',
            starts_at=now - timedelta(days=1), ends_at=now + timedelta(days=30),
            activated_at=now - timedelta(hours=1), activated_by_id=1, created_by=self.actor)
        status_url = f'/api/admin/platform/store-management/{self.store.pk}/license-status'
        feature_url = '/api/admin/platform/license-tiers/BASIC/features/PROMOTIONS'
        self.assertEqual(self.client.get(status_url).data['data']['feature_modes']['PROMOTIONS'], 'OFF')
        changed = self.client.put(feature_url, {'mode': 'FULL'}, format='json')
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual(self.client.get(status_url).data['data']['feature_modes']['PROMOTIONS'], 'FULL')
        self.assertEqual(self.client.get('/api/admin/platform/license-tiers/PRO/features').data['data']['features']['PROMOTIONS']['mode'], 'FULL')
        self.assertEqual(self.client.put('/api/admin/platform/license-tiers/BASIC/features/ORDER',
            {'mode': 'OFF'}, format='json').status_code, 400)
        reset = self.client.put(feature_url, {'mode': None}, format='json')
        self.assertEqual(reset.data['data']['mode'], 'OFF')
        self.assertEqual(self.client.get(status_url).data['data']['feature_modes']['PROMOTIONS'], 'OFF')
        self.assertEqual(PlatformAuditEvent.objects.filter(action='license.tier_feature_changed').count(), 2)
