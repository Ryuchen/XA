from unittest.mock import patch

from django.db import DatabaseError
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import ContentComplianceReceipt
from orders.models import Gameplay, ServiceItem
from orders.tests.factories import make_console_user, make_provider
from stores.context import resolve_store, store_context
from stores.models import Store
from stores.tests_foundation import store_b


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class ContentComplianceTests(APITestCase):
    databases = {'default', 'store_b'}

    def client_for(self, permissions, host='admin.esports.club'):
        user = make_console_user(permissions)
        account = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = account
        user.admin_membership.save(update_fields=['account'])
        return APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])

    def test_service_and_gameplay_text_violations_do_not_save_and_valid_text_is_receipted(self):
        client = self.client_for(['service:view', 'service:edit'])
        for resource, payload in (
            ('service-items', {'name': '违规服务', 'price': 10000, 'rules': '最终解释权归本店'}),
            ('service-items', {'name': '违规服务', 'price': 10000, 'details_html': '<p>本店可单方面变更服务内容</p>'}),
            ('gameplays', {'name': '违规玩法', 'code': 'bad-gameplay', 'rules': '活动解释权归本店'}),
            ('gameplays', {'name': '违规玩法', 'code': 'bad-gameplay', 'details_html': '<b>条款最终以本店审核为准</b>'}),
        ):
            with self.subTest(resource=resource, payload=payload):
                response = client.post(f'/api/admin/{resource}/', payload, format='json')
                self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(ServiceItem.objects.filter(name='违规服务').exists())
        self.assertFalse(Gameplay.objects.filter(name='违规玩法').exists())
        service_response = client.post('/api/admin/service-items/', {
            'name': '合规服务', 'price': 10000, 'rules': '按约定完成服务',
            'details_html': '<p>服务内容与价格已列明</p>',
        }, format='json')
        self.assertEqual(service_response.status_code, 201, service_response.content)
        service = ServiceItem.objects.get(name='合规服务')
        self.assertEqual(set(ContentComplianceReceipt.objects.filter(
            content_type='service_item', object_id=service.pk).values_list('field_name', flat=True)),
            {'SERVICE_RULES', 'SERVICE_DETAILS'})
        gameplay_response = client.post('/api/admin/gameplays/', {
            'name': '合规玩法', 'code': 'good-gameplay', 'rules': '遵守玩法规则',
            'details_html': '<p>完整玩法说明</p>',
        }, format='json')
        self.assertEqual(gameplay_response.status_code, 201, gameplay_response.content)
        gameplay = Gameplay.objects.get(code='good-gameplay')
        self.assertEqual(set(ContentComplianceReceipt.objects.filter(
            content_type='gameplay', object_id=gameplay.pk).values_list('field_name', flat=True)),
            {'GAMEPLAY_RULES', 'GAMEPLAY_DETAILS'})
        before = ContentComplianceReceipt.objects.count()
        unchanged = client.patch(f'/api/admin/service-items/{service.pk}/', {'rules': '按约定完成服务'}, format='json')
        self.assertEqual(unchanged.status_code, 200, unchanged.content)
        self.assertEqual(ContentComplianceReceipt.objects.count(), before)
        invalid = client.patch(f'/api/admin/service-items/{service.pk}/', {'rules': '本店保留修改条款的权利'}, format='json')
        self.assertEqual(invalid.status_code, 400, invalid.content)
        service.refresh_from_db()
        self.assertEqual(service.rules, '按约定完成服务')

    def test_disabled_store_compliance_allows_text_without_receipts(self):
        store = Store.objects.get(slug='shenglang')
        Store.objects.filter(pk=store.pk).update(content_compliance_enabled=False)
        self.addCleanup(Store.objects.filter(pk=store.pk).update, content_compliance_enabled=True)
        client = self.client_for(['service:view', 'service:edit'])
        response = client.post('/api/admin/service-items/', {
            'name': '停用合规店服务', 'price': 10000,
            'rules': '最终解释权归本店', 'details_html': '<p>本店可单方面变更服务内容</p>',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertFalse(ContentComplianceReceipt.objects.exists())

    def test_receipt_failure_rolls_back_catalog_update(self):
        client = self.client_for(['service:view', 'service:edit'])
        response = client.post('/api/admin/service-items/', {'name': '回滚服务', 'price': 10000, 'rules': '原规则'}, format='json')
        service = ServiceItem.objects.get(name='回滚服务')
        with patch('common.compliance.record_content_receipt', side_effect=DatabaseError('receipt offline')):
            with self.assertRaises(DatabaseError):
                client.patch(f'/api/admin/service-items/{service.pk}/', {'rules': '新规则'}, format='json')
        service.refresh_from_db()
        self.assertEqual(service.rules, '原规则')

    def test_provider_name_requires_official_permission_and_exemption_is_receipted(self):
        ordinary = self.client_for(['escort:view', 'escort:edit'])
        provider = make_provider(display_name='普通昵称')
        route = f"/api/admin/escorts/{provider.escort_profile.pk}/"
        denied = ordinary.patch(route, {'display_name': '客服小安'}, format='json')
        self.assertEqual(denied.status_code, 400, denied.content)
        provider.escort_profile.refresh_from_db()
        self.assertEqual(provider.escort_profile.display_name, '普通昵称')
        self.assertFalse(ContentComplianceReceipt.objects.exists())
        authorized = self.client_for(['escort:view', 'escort:edit', 'content:official_name'])
        allowed = authorized.patch(route, {'display_name': '客服小安'}, format='json')
        self.assertEqual(allowed.status_code, 200, allowed.content)
        receipt = ContentComplianceReceipt.objects.get(content_type='escort_profile', object_id=provider.escort_profile.pk)
        self.assertEqual(receipt.exempted_rule_codes, ['NICKNAME_RESERVED_ROLES'])
        # The exemption never extends to extreme words.
        blocked = authorized.patch(route, {'display_name': '官方最佳客服'}, format='json')
        self.assertEqual(blocked.status_code, 400, blocked.content)
        created = authorized.post('/api/admin/escorts/', {
            'username': 'official_provider', 'password': 'Synthetic-Password-2026!',
            'phone': '13900009999', 'display_name': '官方接待',
        }, format='json')
        self.assertEqual(created.status_code, 201, created.content)
        profile_id = created.data['data']['id']
        self.assertEqual(ContentComplianceReceipt.objects.get(
            content_type='escort_profile', object_id=profile_id).exempted_rule_codes,
            ['NICKNAME_RESERVED_ROLES'])

    def test_receipt_read_requires_permission_filters_and_never_returns_content(self):
        provider = make_provider(display_name='普通昵称')
        receipt = ContentComplianceReceipt.objects.create(
            content_type='escort_profile', object_id=provider.escort_profile.pk,
            field_name='PROVIDER_DISPLAY_NAME', content_sha256='a' * 64, rule_version=1,
            rule_set_sha256='b' * 64, matched_rule_codes=[], exempted_rule_codes=[],
            actor_label='TEST',
        )
        denied = self.client_for(['escort:view'])
        self.assertEqual(denied.get('/api/admin/content-compliance-receipts/').status_code, 403)
        reader = self.client_for(['content:view'])
        response = reader.get('/api/admin/content-compliance-receipts/', {
            'content_type': 'escort_profile', 'object_id': provider.escort_profile.pk,
            'field_name': 'PROVIDER_DISPLAY_NAME',
        })
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data['data']['count'], 1)
        item = response.data['data']['results'][0]
        self.assertEqual(item['id'], receipt.pk)
        self.assertNotIn('content', item)
        self.assertNotIn('普通昵称', str(response.data))
        self.assertEqual(reader.get('/api/admin/content-compliance-receipts/', {'object_id': 'bad'}).status_code, 400)

    @override_settings(STORE_HOST_BINDINGS={
        'sl.esports.club': 'shenglang', 'xa.esports.club': 'xinan',
        'branch.esports.club': 'branch-b', 'testserver': 'shenglang',
        'localhost': 'shenglang', '127.0.0.1': 'shenglang',
    })
    def test_same_numeric_object_id_is_isolated_between_stores(self):
        ContentComplianceReceipt.objects.create(
            content_type='service_item', object_id=777, field_name='SERVICE_RULES',
            content_sha256='1' * 64, rule_version=1, rule_set_sha256='2' * 64,
            matched_rule_codes=[], exempted_rule_codes=[], actor_label='STORE_A',
        )
        branch = store_b()
        with store_context(branch):
            ContentComplianceReceipt.objects.create(
                content_type='service_item', object_id=777, field_name='SERVICE_RULES',
                content_sha256='3' * 64, rule_version=1, rule_set_sha256='4' * 64,
                matched_rule_codes=[], exempted_rule_codes=[], actor_label='STORE_B',
            )
            client = self.client_for(['content:view'], host='branch.esports.club')
            response = client.get('/api/admin/content-compliance-receipts/', {'content_type': 'service_item', 'object_id': 777})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([row['actorLabel'] for row in response.data['data']['results']], ['SYSTEM'])

    def test_receipt_pagination_returns_real_count_and_distinct_pages(self):
        ContentComplianceReceipt.objects.bulk_create([
            ContentComplianceReceipt(
                content_type='service_item', object_id=1000 + index, field_name='SERVICE_RULES',
                content_sha256=f'{index:064x}', rule_version=1, rule_set_sha256='b' * 64,
                matched_rule_codes=[], exempted_rule_codes=[], actor_label='TEST',
            ) for index in range(205)
        ])
        reader = self.client_for(['content:view'])
        route = '/api/admin/content-compliance-receipts/'
        first = reader.get(route, {'page_size': 100})
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual((first.data['data']['count'], len(first.data['data']['results'])), (205, 100))
        self.assertIsNotNone(first.data['data']['next'])
        second = reader.get(route, {'page': 2, 'page_size': 100})
        self.assertEqual(second.data['data']['count'], 205)
        self.assertNotEqual({row['id'] for row in first.data['data']['results']},
                            {row['id'] for row in second.data['data']['results']})
        third = reader.get(route, {'page': 3, 'page_size': 100})
        self.assertEqual(len(third.data['data']['results']), 5)
        self.assertIsNone(third.data['data']['next'])
        for query in ({'page': 0}, {'page': 'x'}, {'page_size': 101}, {'extra': '1'},
                      {'page': 1, 'page_size': 100, 'field_name': 'UNKNOWN'}):
            with self.subTest(query=query):
                self.assertEqual(reader.get(route, query).status_code, 400)
        self.assertEqual(reader.get(route + '?page=1&page=2').status_code, 400)

    def test_registration_display_name_is_a_valid_receipt_filter(self):
        ContentComplianceReceipt.objects.create(
            content_type='provider_registration', object_id=77,
            field_name='PROVIDER_REGISTRATION_DISPLAY_NAME', content_sha256='a' * 64,
            rule_version=1, rule_set_sha256='b' * 64, matched_rule_codes=[],
            exempted_rule_codes=[], actor_label='PUBLIC_REGISTRATION',
        )
        reader = self.client_for(['content:view'])
        response = reader.get('/api/admin/content-compliance-receipts/', {
            'field_name': 'PROVIDER_REGISTRATION_DISPLAY_NAME',
        })
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data['data']['count'], 1)
        self.assertEqual(response.data['data']['results'][0]['fieldName'],
                         'PROVIDER_REGISTRATION_DISPLAY_NAME')
