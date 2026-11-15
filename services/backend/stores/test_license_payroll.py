from uuid import uuid4

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from console.dictionary_models import BusinessDictionaryItem
from stores import test_license_account_admission as fixture
from stores.context import store_context
from stores.license_features import change_feature
from stores.tests_foundation import admin


@override_settings(LICENSE_ACCOUNT_ADMISSION_ENABLED=False, LICENSE_BENEFIT_ENFORCEMENT_ENABLED=True,
                   LICENSE_NOTICE_DISPATCH_ENABLED=False, LEGACY_FORCE_AUTH_COMPAT=False,
                   PLATFORM_IDENTITY_ENABLED=True, ALLOWED_HOSTS=['*'])
class PayrollLicenseTests(TestCase):
    databases = {'default', 'store_b'}
    setUp = fixture.LicenseAccountAdmissionTests.setUp

    def test_store_and_platform_configuration_and_existing_wage_access(self):
        with store_context(self.second):
            staff = admin('payroll-license', super_admin=True)
            token, _ = issue_account_tokens(staff)
            position = BusinessDictionaryItem.objects.create(type='SALARY_POSITION', code='LICENSE', name='测试岗位')
        client = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        platform = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + str(self.platform_token))
        plans = '/api/admin/salary-plans/'
        platform_plans = '/api/admin/platform/store-payroll/xinan/plans'
        payload = {'position': position.pk, 'name': '许可测试', 'base_fen': 1000}
        change_feature(self.platform_token, tier='BASIC', feature='PAYROLL', mode='BASIC')
        created = client.post(plans, payload, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        operation = {'request_id': str(uuid4()), **payload, 'name': '平台许可测试'}
        result = platform.post(platform_plans, operation, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        change_feature(self.platform_token, tier='BASIC', feature='PAYROLL', mode='OFF')
        self.assertEqual(client.post(plans, payload, format='json').status_code, 403)
        self.assertEqual(platform.post(platform_plans, {**operation, 'request_id': str(uuid4())}, format='json').status_code, 403)
        self.assertEqual(platform.post(platform_plans, operation, format='json').status_code, 200)
        self.assertEqual(client.put('/api/admin/payroll/config/', {'night_start': None, 'night_end': None, 'revision': 0}, format='json').status_code, 403)
        self.assertEqual(platform.put('/api/admin/platform/store-payroll/xinan/config', {'night_start': None, 'night_end': None, 'revision': 0}, format='json').status_code, 403)
        self.assertEqual(client.get(plans).status_code, 200)
        self.assertEqual(client.get('/api/admin/payroll/preview/', {'month': '2026-11'}).status_code, 200)
        self.assertEqual(platform.get('/api/admin/platform/store-payroll/xinan/preview', {'month': '2026-11'}).status_code, 200)
