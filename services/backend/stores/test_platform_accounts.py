from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from stores.context import resolve_store, store_context
from stores.models import PlatformAccount, PlatformAuditEvent, PlatformRoleGrant
from stores.platform_auth import issue_tokens
from stores.tests_foundation import admin
from stores.tests_hosts import xinan_store


@override_settings(ALLOWED_HOSTS=['*'], PLATFORM_IDENTITY_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False)
class PlatformAccountManagementTests(TestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first = resolve_store()
        self.second = xinan_store()
        self.client = APIClient(HTTP_HOST='admin.esports.club')
        self.super = PlatformAccount(username='root-platform', phone='13800001111', is_phone_verified=True)
        self.super.set_password('test-root-strong-password')
        self.super.save()
        PlatformRoleGrant.objects.create(account=self.super, role='SUPER_ADMIN', scope_slug='*')
        self.headers = {'HTTP_AUTHORIZATION': 'Bearer ' + issue_tokens(self.super, 'SMS', self.super.phone)[0]}
        self.url = '/api/admin/platform/accounts'
        self.new = {'username': 'finance-new', 'password': 'test-finance-strong-password',
                    'nickname': '平台财务', 'phone': '13800002222',
                    'grants': [{'role': 'FINANCE', 'scope_slug': 'xinan'}]}

    def create_finance(self):
        response = self.client.post(self.url, self.new, format='json', **self.headers)
        self.assertEqual(response.status_code, 201, response.data)
        return response.data['data']

    def test_create_finance_is_scoped_audited_and_does_not_store_plaintext(self):
        data = self.create_finance()
        account = PlatformAccount.objects.get(pk=data['id'])
        self.assertTrue(account.check_password(self.new['password']))
        self.assertNotEqual(account.password, self.new['password'])
        self.assertFalse(account.is_phone_verified)
        event = PlatformAuditEvent.objects.get(action='platform.account_created', target_id=str(account.pk))
        self.assertNotIn(self.new['password'], str(event.detail))
        self.assertNotIn(self.new['phone'], str(event.detail))
        self.assertEqual(data['grants'], [{'role': 'FINANCE', 'scope_slug': 'xinan'}])
        self.assertEqual(self.client.post(self.url, self.new, format='json', **self.headers).status_code, 409)
        self.assertEqual(len(self.client.get(self.url, **self.headers).data['data']['results']), 2)
        history = self.client.get(f"{self.url}/{data['id']}/audit", **self.headers).data['data']['results']
        self.assertEqual(history[0]['action'], 'platform.account_created')
        self.assertEqual(history[0]['actor'], self.super.username)
        self.assertNotIn(self.new['password'], str(history))

    def test_finance_and_store_staff_cannot_manage_platform_accounts(self):
        self.create_finance()
        finance = PlatformAccount.objects.get(username='finance-new')
        finance.is_phone_verified = True
        finance.save(update_fields=['is_phone_verified'])
        finance_headers = {'HTTP_AUTHORIZATION': 'Bearer ' + issue_tokens(finance, 'SMS', finance.phone)[0]}
        for method in ('get', 'post'):
            response = getattr(self.client, method)(self.url, **finance_headers)
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get(f"{self.url}/{finance.pk}/audit", **finance_headers).status_code, 403)
        with store_context(self.first):
            staff = admin('store-staff')
            token, _ = issue_account_tokens(staff)
        self.assertEqual(self.client.get(self.url, HTTP_AUTHORIZATION='Bearer ' + token).status_code, 401)

    def test_scope_updates_are_checked_and_stale_updates_do_not_overwrite(self):
        data = self.create_finance()
        path = f"{self.url}/{data['id']}"
        updated = self.client.patch(path, {'expected_updated_at': data['updated_at'],
            'grants': [{'role': 'FINANCE', 'scope_slug': 'shenglang'}]}, format='json', **self.headers)
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data['data']['grants'], [{'role': 'FINANCE', 'scope_slug': 'shenglang'}])
        stale = self.client.patch(path, {'expected_updated_at': data['updated_at'], 'nickname': '旧数据'}, format='json', **self.headers)
        self.assertEqual(stale.status_code, 409)
        current = updated.data['data']
        for bad in ([{'role': 'FINANCE', 'scope_slug': 'unknown'}],
                    [{'role': 'SUPER_ADMIN', 'scope_slug': 'xinan'}],
                    [{'role': 'FINANCE', 'scope_slug': '*'}, {'role': 'FINANCE', 'scope_slug': 'xinan'}]):
            self.assertEqual(self.client.patch(path, {'expected_updated_at': current['updated_at'], 'grants': bad}, format='json', **self.headers).status_code, 400)
        event = PlatformAuditEvent.objects.get(action='platform.account_updated', target_id=data['id'])
        self.assertEqual(event.detail['grants_before'], [['FINANCE', 'xinan']])
        self.assertEqual(event.detail['grants_after'], [['FINANCE', 'shenglang']])

    def test_phone_change_disables_old_session_and_last_super_cannot_be_removed(self):
        data = self.create_finance()
        account = PlatformAccount.objects.get(pk=data['id'])
        account.is_phone_verified = True
        account.save(update_fields=['is_phone_verified'])
        token, _ = issue_tokens(account, 'SMS', account.phone)
        path = f"{self.url}/{data['id']}"
        response = self.client.patch(path, {'expected_updated_at': data['updated_at'], 'phone': '13800003333'}, format='json', **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['data']['is_phone_verified'])
        self.assertEqual(self.client.get('/api/admin/platform/profile', HTTP_AUTHORIZATION='Bearer ' + token).status_code, 401)
        current = self.client.get(self.url, **self.headers).data['data']['results']
        root = next(item for item in current if item['id'] == str(self.super.pk))
        self.assertEqual(self.client.patch(f"{self.url}/{root['id']}", {'expected_updated_at': root['updated_at'], 'is_active': False}, format='json', **self.headers).status_code, 409)
        self.assertEqual(self.client.patch(f"{self.url}/{root['id']}", {'expected_updated_at': root['updated_at'], 'grants': [{'role': 'FINANCE', 'scope_slug': '*'}]}, format='json', **self.headers).status_code, 409)

    def test_revoked_session_does_not_revive_after_reactivation_or_scope_restore(self):
        data = self.create_finance()
        account = PlatformAccount.objects.get(pk=data['id'])
        account.is_phone_verified = True
        account.save(update_fields=['is_phone_verified'])
        old_token, _ = issue_tokens(account, 'SMS', account.phone)
        path = f"{self.url}/{data['id']}"
        disabled = self.client.patch(path, {'expected_updated_at': data['updated_at'], 'is_active': False}, format='json', **self.headers).data['data']
        enabled = self.client.patch(path, {'expected_updated_at': disabled['updated_at'], 'is_active': True}, format='json', **self.headers).data['data']
        self.assertEqual(self.client.get('/api/admin/platform/profile', HTTP_AUTHORIZATION='Bearer ' + old_token).status_code, 401)
        account.refresh_from_db()
        scope_token, _ = issue_tokens(account, 'SMS', account.phone)
        narrowed = self.client.patch(path, {'expected_updated_at': enabled['updated_at'], 'grants': [{'role': 'FINANCE', 'scope_slug': 'shenglang'}]}, format='json', **self.headers).data['data']
        self.client.patch(path, {'expected_updated_at': narrowed['updated_at'], 'grants': [{'role': 'FINANCE', 'scope_slug': 'xinan'}]}, format='json', **self.headers)
        self.assertEqual(self.client.get('/api/admin/platform/profile', HTTP_AUTHORIZATION='Bearer ' + scope_token).status_code, 401)
