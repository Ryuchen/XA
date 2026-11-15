from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from console.dictionary_models import BusinessDictionaryItem
from console.models import AdminRole
from stores.context import resolve_store, store_context
from stores.tests_foundation import admin
from stores.tests_hosts import xinan_store


@override_settings(LICENSE_ACCOUNT_ADMISSION_ENABLED=False, LEGACY_FORCE_AUTH_COMPAT=False,
                   PLATFORM_IDENTITY_ENABLED=True, ALLOWED_HOSTS=['*'])
class StoreFinanceRoleTests(TestCase):
    databases = {'default', 'store_b'}

    def test_seed_and_role_admission_are_store_scoped(self):
        first, second = resolve_store(), xinan_store()
        clients = []
        for store, host in ((first, 'sl.esports.club'), (second, 'xa.esports.club')):
            with store_context(store):
                self.assertFalse(AdminRole.objects.filter(code='finance').exists())
                self.assertFalse(BusinessDictionaryItem.objects.filter(type='SALARY_POSITION', code='FINANCE').exists())
                support = AdminRole.objects.get(code='support')
                self.assertTrue({'wallet:recharge', 'withdraw:view', 'withdraw:audit'}.issubset(set(support.permissions)))
                actor = admin('role-boundary', super_admin=True)
                token, _ = issue_account_tokens(actor)
            clients.append(APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token))
        for client in clients:
            self.assertEqual(client.post('/api/admin/roles/', {'code': 'finance', 'name': '其他名称', 'permissions': []}, format='json').status_code, 400)
            self.assertEqual(client.post('/api/admin/roles/', {'code': 'other_role', 'name': '财务', 'permissions': []}, format='json').status_code, 400)
            self.assertEqual(client.post('/api/admin/dictionary-items/', {'type': 'SALARY_POSITION', 'code': 'FINANCE', 'name': '财务'}, format='json').status_code, 400)
            accepted = client.post('/api/admin/roles/', {'code': 'service_operations', 'name': '客服运营', 'permissions': ['wallet:recharge']}, format='json')
            self.assertEqual(accepted.status_code, 201, accepted.data)
            self.assertEqual(client.get('/api/admin/platform/withdrawals', HTTP_HOST='admin.esports.club').status_code, 401)
        self.assertEqual(clients[0].get('/api/admin/roles/', HTTP_HOST='xa.esports.club').status_code, 401)

    def test_existing_store_finance_role_cannot_be_assigned(self):
        store = xinan_store()
        with store_context(store):
            actor = admin('role-owner', super_admin=True)
            token, _ = issue_account_tokens(actor)
            finance = AdminRole.objects.create(code='finance', name='财务', permissions=[])
        client = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        response = client.post('/api/admin/admins/', {'username': 'test-staff', 'password': 'Password123',
            'phone': '13800001111', 'roles': [finance.pk]}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
