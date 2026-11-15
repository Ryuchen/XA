from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog
from .context import resolve_store, store_context
from .models import PlatformAccount, PlatformRoleGrant, Store, StoreLicenseActivationAudit, StoreLicenseActivatorGrant, StoreProvisioningClaim
from .platform_auth import issue_tokens
from .tests_foundation import admin
from .tests_hosts import xinan_store


@override_settings(PLATFORM_IDENTITY_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   LOGIN_OTP_REQUIRED=False, ALLOWED_HOSTS=['*'])
class LicenseActivationApiTests(TestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first, self.second = resolve_store(), xinan_store()
        self.platform = PlatformAccount.objects.create(username='license-activation-platform',
            phone='13800000100', is_phone_verified=True)
        PlatformRoleGrant.objects.create(account=self.platform, role='SUPER_ADMIN', scope_slug='*')
        token, _ = issue_tokens(self.platform, 'SMS', self.platform.phone)
        self.platform_client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        self.accounts = {}
        for store in (self.first, self.second):
            with store_context(store):
                superuser = admin('license-super-' + store.slug, super_admin=True)
                ordinary = admin('license-staff-' + store.slug)
                for account in (superuser, ordinary):
                    account.is_phone_verified = True
                    account.save(update_fields=['is_phone_verified'])
                self.accounts[store.slug] = (superuser, ordinary)

    def client_for(self, store, account):
        with store_context(store):
            token, _ = issue_account_tokens(account, auth_method='password_sms')
        return APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)

    def issue(self, store):
        now = timezone.now()
        response = self.platform_client.post(f'/api/admin/platform/store-management/{store.pk}/licenses', {
            'tier': 'PRO', 'kind': 'INITIAL', 'starts_at': (now + timedelta(minutes=5)).isoformat(),
            'ends_at': (now + timedelta(days=365)).isoformat(),
        }, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        return response.data['data']['license_code']

    def test_store_super_admin_activation_cannot_cross_stores_and_audit_is_masked(self):
        code = self.issue(self.first)
        other_client = self.client_for(self.second, self.accounts[self.second.slug][0])
        self.assertEqual(other_client.post('/api/admin/license/activate', {'license_code': code}, format='json').status_code, 400)
        client = self.client_for(self.first, self.accounts[self.first.slug][0])
        result = client.post('/api/admin/license/activate', {'license_code': code}, format='json')
        self.assertEqual(result.status_code, 200, result.content)
        self.assertTrue(result.data['data']['newly_activated'])
        self.assertEqual(result['Cache-Control'], 'no-store')
        again = client.post('/api/admin/license/activate', {'license_code': code}, format='json')
        self.assertEqual(again.status_code, 200)
        self.assertFalse(again.data['data']['newly_activated'])
        self.assertEqual(StoreLicenseActivationAudit.objects.count(), 1)
        with store_context(self.first):
            logs = AdminAuditLog.objects.filter(path='/api/admin/license/activate')
            self.assertTrue(logs.exists())
            self.assertTrue(all(log.request_body == {'license_code': '***'} for log in logs))
            self.assertNotIn(code, str(list(logs.values_list('request_body', flat=True))))

    def test_only_platform_can_delegate_and_revocation_is_immediate(self):
        code = self.issue(self.second)
        account = self.accounts[self.second.slug][1]
        client = self.client_for(self.second, account)
        self.assertEqual(client.post('/api/admin/license/activate', {'license_code': code}, format='json').status_code, 403)
        grant_url = f'/api/admin/platform/store-management/{self.second.pk}/license-activators/{account.pk}'
        self.assertEqual(client.put(grant_url, {}, format='json').status_code, 401)
        granted = self.platform_client.put(grant_url, {}, format='json')
        self.assertEqual(granted.status_code, 200, granted.content)
        grant = StoreLicenseActivatorGrant.objects.get(store=self.second, account_id=account.pk)
        self.assertEqual(grant.account_no, account.account_no)
        self.assertEqual(self.platform_client.delete(grant_url).status_code, 200)
        self.assertEqual(client.post('/api/admin/license/activate', {'license_code': code}, format='json').status_code, 403)
        self.assertEqual(self.platform_client.put(grant_url, {}, format='json').status_code, 200)
        result = client.post('/api/admin/license/activate', {'license_code': code}, format='json')
        self.assertEqual(result.status_code, 200, result.content)
        self.assertTrue(result.data['data']['newly_activated'])

    def test_same_local_account_id_does_not_carry_delegation_across_stores(self):
        first_staff = self.accounts[self.first.slug][1]
        second_staff = self.accounts[self.second.slug][1]
        self.assertEqual(first_staff.pk, second_staff.pk)
        url = f'/api/admin/platform/store-management/{self.second.pk}/license-activators/{second_staff.pk}'
        self.assertEqual(self.platform_client.put(url, {}, format='json').status_code, 200)
        code = self.issue(self.first)
        client = self.client_for(self.first, first_staff)
        self.assertEqual(client.post('/api/admin/license/activate', {'license_code': code}, format='json').status_code, 403)
        self.assertFalse(StoreLicenseActivationAudit.objects.exists())

    def test_grant_requires_a_real_active_staff_member_of_the_target_store(self):
        self.assertEqual(self.platform_client.put(
            f'/api/admin/platform/store-management/{self.second.pk}/license-activators/999999', {}, format='json').status_code, 400)
        self.assertFalse(StoreLicenseActivatorGrant.objects.exists())

    def test_configuring_store_initial_admin_can_activate_before_opening(self):
        code = self.issue(self.second)
        owner = self.accounts[self.second.slug][0]
        self.second.status = Store.Status.CONFIGURING
        self.second.save(update_fields=['status'])
        StoreProvisioningClaim.objects.create(store=self.second, database_fingerprint='a' * 64,
                                               admin_username=owner.username)
        client = self.client_for(self.second, owner)
        result = client.post('/api/admin/license/activate', {'license_code': code}, format='json')
        self.assertEqual(result.status_code, 200, result.content)
        self.second.refresh_from_db()
        self.assertEqual(self.second.status, Store.Status.CONFIGURING)
