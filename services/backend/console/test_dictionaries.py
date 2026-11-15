"""Real authenticated dictionary flows; run on the isolated MySQL test database."""
from django.core.exceptions import ValidationError as ModelValidationError
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.models import OtpCredential
from club_accounts.otp import encrypt_secret, hotp
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.dictionary_models import BusinessDictionaryItem
from console.models import AdminMembership
from orders.tests.factories import make_console_user, make_user


@override_settings(LOGIN_OTP_REQUIRED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class BusinessDictionaryWorkflowTest(APITestCase):
    def setUp(self):
        self.editor = self.staff(['dictionary:view', 'dictionary:edit'])

    def staff(self, permissions):
        user = make_console_user(permissions)
        account = get_or_create_account_for_legacy_user(user)
        AdminMembership.objects.filter(user=user).update(account=account)
        otp_secret = 'JBSWY3DPEHPK3PXP'
        OtpCredential.objects.create(account=account, secret_ciphertext=encrypt_secret(otp_secret))
        client = APIClient()
        first = client.post('/api/admin/auth/login', {
            'username': account.username, 'password': 'pass1234',
        }, format='json')
        self.assertEqual(first.data['code'], 0)
        gate = first.data['data']
        self.assertTrue(gate['requiresOTP'])
        self.assertFalse(gate['requiresOTPSetup'])
        signed = client.post('/api/admin/auth/login', {
            'username': account.username, 'password': 'pass1234',
            'otpChallenge': gate['otpChallenge'],
            'otpCode': hotp(otp_secret, int(timezone.now().timestamp()) // 30),
        }, format='json')
        self.assertEqual(signed.data['code'], 0)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + signed.data['data']['token'])
        client.account_for_tests = account
        return client

    def create(self, **values):
        payload = {'type': 'AD_CHANNEL', 'code': 'TEST_AD', 'name': '隔离测试渠道', **values}
        response = self.editor.post('/api/admin/dictionary-items/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data['data']

    def test_seeds_match_store_positions_without_actual_ad_spend(self):
        codes = set(BusinessDictionaryItem.objects.filter(type='SALARY_POSITION').values_list('code', flat=True))
        self.assertEqual(codes, {
            'STORE_MANAGER', 'CUSTOMER_SERVICE_FULLTIME', 'CUSTOMER_SERVICE_PARTTIME',
            'PROVIDER_MANAGER', 'AFTERSALE_MANAGER', 'OPERATIONS',
            'EXAMINER_SHENGLANG', 'EXAMINER_XINGAN',
        })
        self.assertEqual(set(BusinessDictionaryItem.objects.filter(type='AD_CHANNEL').values_list('code', flat=True)), {'UNKNOWN', 'OWNED'})

    def test_create_edit_filter_and_delete_unreferenced_item(self):
        row = self.create()
        updated = self.editor.patch(f'/api/admin/dictionary-items/{row["id"]}/', {
            'name': '更新渠道', 'remark': '仅测试', 'sort_order': 7,
        }, format='json')
        self.assertEqual(updated.status_code, 200, updated.data)
        result = self.editor.get('/api/admin/dictionary-items/', {'type': 'AD_CHANNEL', 'keyword': '更新渠道'})
        self.assertEqual(result.data['data']['total'], 1)
        self.assertEqual(result.data['data']['list'][0]['sort_order'], 7)
        deleted = self.editor.delete(f'/api/admin/dictionary-items/{row["id"]}/')
        self.assertEqual(deleted.status_code, 200, deleted.data)
        self.assertFalse(BusinessDictionaryItem.objects.filter(pk=row['id']).exists())

    def test_unique_code_is_scoped_to_type(self):
        self.create()
        duplicate = self.editor.post('/api/admin/dictionary-items/', {
            'type': 'AD_CHANNEL', 'code': 'TEST_AD', 'name': '重复',
        }, format='json')
        self.assertEqual(duplicate.status_code, 400, duplicate.data)
        self.create(type='COST_CATEGORY')
        self.assertEqual(BusinessDictionaryItem.objects.filter(code='TEST_AD').count(), 2)

    def test_type_and_code_cannot_change_after_creation(self):
        row = self.create()
        for payload in ({'code': 'RENAMED'}, {'type': 'SALARY_POSITION'}):
            response = self.editor.patch(f'/api/admin/dictionary-items/{row["id"]}/', payload, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        stored = BusinessDictionaryItem.objects.get(pk=row['id'])
        self.assertEqual((stored.type, stored.code), ('AD_CHANNEL', 'TEST_AD'))
        stored.code = 'REPLACED'
        with self.assertRaises(ModelValidationError):
            stored.save()

    def test_invalid_type_code_and_sort_are_rejected(self):
        for values in ({'type': 'ORDER_STATUS'}, {'code': 'unsafe code'}, {'name': '  '}, {'sort_order': -1}):
            response = self.editor.post('/api/admin/dictionary-items/', {
                'type': 'AD_CHANNEL', 'code': 'INVALID_SAMPLE', 'name': '测试', **values,
            }, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.editor.get('/api/admin/dictionary-items/', {'type': 'ORDER_STATUS'}).status_code, 400)
        self.assertEqual(self.editor.get('/api/admin/dictionary-items/', {'is_active': 'invalid'}).status_code, 400)
        self.assertEqual(self.editor.get('/api/admin/dictionary-options/').status_code, 400)

    def test_inactive_items_stay_in_history_but_not_new_options(self):
        row = self.create()
        response = self.editor.patch(f'/api/admin/dictionary-items/{row["id"]}/', {'is_active': False}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        options = self.editor.get('/api/admin/dictionary-options/', {'type': 'AD_CHANNEL', 'page_size': 100})
        self.assertNotIn(row['id'], [item['id'] for item in options.data['data']['list']])
        detail = self.editor.get(f'/api/admin/dictionary-items/{row["id"]}/')
        self.assertEqual(detail.status_code, 200)
        self.assertFalse(detail.data['data']['is_active'])
        inactive = self.editor.get('/api/admin/dictionary-items/', {'type': 'AD_CHANNEL', 'is_active': 'false'})
        self.assertEqual([item['id'] for item in inactive.data['data']['list']], [row['id']])

    def test_consumer_permissions_only_allow_their_dictionary_options(self):
        salary = self.staff(['salary:view'])
        cost = self.staff(['cost:edit'])
        for client, allowed, forbidden in (
            (salary, 'SALARY_POSITION', 'AD_CHANNEL'), (cost, 'AD_CHANNEL', 'SALARY_POSITION'),
        ):
            self.assertEqual(client.get('/api/admin/dictionary-options/', {'type': allowed}).status_code, 200)
            self.assertEqual(client.get('/api/admin/dictionary-options/', {'type': forbidden}).status_code, 403)
            self.assertEqual(client.get('/api/admin/dictionary-items/').status_code, 403)
            denied = client.post('/api/admin/dictionary-items/', {'type': allowed, 'code': 'DENIED', 'name': '无权'}, format='json')
            self.assertEqual(denied.status_code, 403)
        self.assertEqual(cost.get('/api/admin/dictionary-options/', {'type': 'COST_CATEGORY'}).status_code, 200)

    def test_dictionary_viewer_cannot_write(self):
        viewer = self.staff(['dictionary:view'])
        row = self.create()
        self.assertEqual(viewer.get('/api/admin/dictionary-items/').status_code, 200)
        self.assertEqual(viewer.patch(f'/api/admin/dictionary-items/{row["id"]}/', {'name': '越权'}, format='json').status_code, 403)
        self.assertEqual(viewer.delete(f'/api/admin/dictionary-items/{row["id"]}/').status_code, 403)
        self.assertEqual(BusinessDictionaryItem.objects.get(pk=row['id']).name, row['name'])

    def test_anonymous_and_boss_accounts_cannot_access_admin_dictionaries(self):
        anonymous = APIClient()
        self.assertEqual(anonymous.get('/api/admin/dictionaries/').status_code, 401)
        account = get_or_create_account_for_legacy_user(make_user())
        boss = APIClient()
        boss.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        for path in ('/api/admin/dictionaries/', '/api/admin/dictionary-items/', '/api/admin/dictionary-options/?type=AD_CHANNEL'):
            self.assertEqual(boss.get(path).status_code, 403)

    def test_options_paginate_beyond_first_hundred_without_losing_entries(self):
        BusinessDictionaryItem.objects.bulk_create([
            BusinessDictionaryItem(type='AD_CHANNEL', code=f'PAGINATED_{index}', name=f'渠道{index}', sort_order=100 + index)
            for index in range(105)
        ])
        first = self.editor.get('/api/admin/dictionary-options/', {'type': 'AD_CHANNEL', 'page_size': 999})
        second = self.editor.get('/api/admin/dictionary-options/', {'type': 'AD_CHANNEL', 'page_size': 100, 'page': 2})
        self.assertEqual(first.data['data']['total'], 107)
        self.assertEqual(first.data['data']['page_size'], 100)
        ids = [row['id'] for row in first.data['data']['list'] + second.data['data']['list']]
        self.assertEqual(len(ids), 107)
        self.assertEqual(len(set(ids)), 107)

    def test_actual_salary_reference_prevents_delete_but_allows_disable(self):
        from console.payroll_models import SalaryPlan
        position = BusinessDictionaryItem.objects.get(type='SALARY_POSITION', code='OPERATIONS')
        SalaryPlan.objects.create(position=position, name='隔离运营底薪')
        deleted = self.editor.delete(f'/api/admin/dictionary-items/{position.pk}/')
        self.assertEqual(deleted.status_code, 400, deleted.data)
        self.assertTrue(BusinessDictionaryItem.objects.filter(pk=position.pk).exists())
        disabled = self.editor.patch(f'/api/admin/dictionary-items/{position.pk}/', {'is_active': False}, format='json')
        self.assertEqual(disabled.status_code, 200, disabled.data)
        self.assertEqual(SalaryPlan.objects.get(name='隔离运营底薪').position_id, position.pk)

    def test_actual_cost_references_protect_category_and_channel(self):
        from console.payroll_models import OperatingCost
        category = BusinessDictionaryItem.objects.get(type='COST_CATEGORY', code='OTHER')
        channel = BusinessDictionaryItem.objects.get(type='AD_CHANNEL', code='OWNED')
        cost = OperatingCost.objects.create(day=timezone.localdate(), category=category, channel=channel,
                                           amount_fen=1200, note='隔离测试成本', created_by=self.editor.account_for_tests)
        for item in (category, channel):
            response = self.editor.delete(f'/api/admin/dictionary-items/{item.pk}/')
            self.assertEqual(response.status_code, 400, response.data)
            disabled = self.editor.patch(f'/api/admin/dictionary-items/{item.pk}/', {'is_active': False}, format='json')
            self.assertEqual(disabled.status_code, 200, disabled.data)
        cost.refresh_from_db()
        self.assertEqual((cost.category_id, cost.channel_id, cost.amount_fen), (category.pk, channel.pk, 1200))

    def test_catalog_links_preserve_legacy_permissions_and_do_not_expose_secret_values(self):
        response = self.editor.get('/api/admin/dictionaries/')
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data['data']
        self.assertEqual({row['id'] for row in data['legacy']}, {'game-categories', 'service-categories', 'boss-types', 'escort-levels'})
        self.assertFalse(any(row['can_view'] for row in data['legacy']))
        self.assertTrue(data['readonly'])
        service_editor = self.staff(['dictionary:view', 'service:view'])
        linked = service_editor.get('/api/admin/dictionaries/').data['data']['legacy']
        self.assertTrue(next(row for row in linked if row['id'] == 'game-categories')['can_view'])
        self.assertFalse(next(row for row in linked if row['id'] == 'game-categories')['can_edit'])
        self.assertFalse(any('value' in row or 'secret' in row for row in data['policies']))

    def test_legacy_dictionary_endpoints_keep_their_original_permissions(self):
        viewer = self.staff(['service:view', 'boss_type:view', 'escort_level:view'])
        for resource in ('game-categories', 'service-categories', 'boss-types', 'escort-levels'):
            response = viewer.get(f'/api/admin/{resource}/')
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0)
            self.assertEqual(self.editor.get(f'/api/admin/{resource}/').status_code, 403)
