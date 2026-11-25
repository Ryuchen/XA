import copy
import json
from unittest.mock import patch

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from common.compliance import (
    BUILTIN_RULES_SHA256, BUILTIN_RULES_V1, ComplianceViolation, check_content,
    check_content_against_rules, rules_sha256,
)
from .models import (
    ComplianceRuleState, ComplianceRuleVersion, PlatformAccount,
    PlatformAuditEvent, PlatformRoleGrant, Store, StoreContentComplianceReceipt,
)
from .platform_auth import issue_tokens
from .context import store_context


@override_settings(PLATFORM_IDENTITY_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   STORE_DATABASE_ALIASES=('store_b',), ALLOWED_HOSTS=['*'])
class PlatformComplianceTests(TransactionTestCase):
    databases = {'default', 'store_b'}
    reset_sequences = True

    def platform(self, username, role='SUPER_ADMIN', scope='*', host='admin.esports.club'):
        account = PlatformAccount(username=username, phone='13800001111', is_phone_verified=True)
        account.set_password('Synthetic-Platform-Password-2026!')
        account.save()
        PlatformRoleGrant.objects.create(account=account, role=role, scope_slug=scope)
        token, _ = issue_tokens(account, 'SMS', account.phone)
        return account, APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token)

    def draft_rules(self):
        rules = list(copy.deepcopy(BUILTIN_RULES_V1))
        rules.append({'code': 'STORE_ONLY_V2', 'category': 'EXTREME_WORD',
                      'applies_to': ['STORE_NAME'], 'terms': ['新版本禁用词'],
                      'message': '新版本门店名称限制', 'enabled': True})
        return rules

    def test_permissions_host_and_anonymous_denials_never_write_audit(self):
        _, finance = self.platform('compliance-finance', 'FINANCE')
        _, scoped = self.platform('compliance-scoped', scope='shenglang')
        _, wrong_host = self.platform('compliance-host', host='xa.esports.club')
        for client in (finance, scoped, wrong_host, APIClient(HTTP_HOST='admin.esports.club')):
            with self.subTest(client=client):
                self.assertIn(client.get('/api/admin/platform/compliance-rules').status_code, (401, 403, 404))
                self.assertIn(client.post('/api/admin/platform/compliance-rules', {'rules': []}, format='json').status_code, (401, 403, 404))
        self.assertFalse(PlatformAuditEvent.objects.exists())

    def test_create_read_publish_idempotence_and_metadata_only_audit(self):
        actor, client = self.platform('compliance-owner')
        listing = client.get('/api/admin/platform/compliance-rules')
        self.assertEqual(listing.status_code, 200, listing.content)
        self.assertEqual(listing.data['data']['current']['version'], 1)
        created = client.post('/api/admin/platform/compliance-rules', {'rules': self.draft_rules()}, format='json')
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.data['data']['status'], 'DRAFT')
        version = created.data['data']['version']
        self.assertEqual(client.get(f'/api/admin/platform/compliance-rules/{version}').data['data']['rules'],
                         self.draft_rules())
        published = client.post(f'/api/admin/platform/compliance-rules/{version}/publish', {}, format='json')
        self.assertEqual(published.status_code, 200, published.content)
        self.assertEqual(published.data['data']['status'], 'PUBLISHED')
        with self.assertRaises(DjangoValidationError):
            ComplianceRuleVersion.objects.get(version=version).delete()
        with self.assertRaises(DjangoValidationError):
            row = ComplianceRuleVersion.objects.get(version=version)
            row.rules = []
            row.save(update_fields=['rules'])
        self.assertEqual(client.post(f'/api/admin/platform/compliance-rules/{version}/publish', {}, format='json').status_code, 200)
        self.assertEqual(ComplianceRuleState.objects.get(pk=1).current_version.version, version)
        events = list(PlatformAuditEvent.objects.filter(account=actor).order_by('pk'))
        self.assertEqual([event.action for event in events],
                         ['compliance.draft_created', 'compliance.published', 'compliance.published'])
        for event in events:
            self.assertEqual(set(event.detail), {'version', 'ruleCount', 'rulesSha256', 'status'})
            self.assertNotIn('新版本禁用词', json.dumps(event.detail, ensure_ascii=False))

    def test_audit_failure_rolls_back_draft_and_publish(self):
        _, client = self.platform('compliance-audit-failure')
        before_pointer = ComplianceRuleState.objects.filter(pk=1).values_list('current_version_id', flat=True).first()
        with patch('stores.platform_compliance.PlatformAuditEvent.objects.create', side_effect=DatabaseError('audit offline')):
            response = client.post('/api/admin/platform/compliance-rules', {'rules': self.draft_rules()}, format='json')
        self.assertEqual(response.status_code, 503, response.content)
        self.assertFalse(ComplianceRuleVersion.objects.filter(version=2).exists())
        self.assertEqual(ComplianceRuleState.objects.filter(pk=1).values_list('current_version_id', flat=True).first(), before_pointer)
        created = client.post('/api/admin/platform/compliance-rules', {'rules': self.draft_rules()}, format='json')
        version = created.data['data']['version']
        before_publish_pointer = ComplianceRuleState.objects.filter(pk=1).values_list('current_version_id', flat=True).first()
        with patch('stores.platform_compliance.PlatformAuditEvent.objects.create', side_effect=DatabaseError('audit offline')):
            response = client.post(f'/api/admin/platform/compliance-rules/{version}/publish', {}, format='json')
        self.assertEqual(response.status_code, 503, response.content)
        row = ComplianceRuleVersion.objects.get(version=version)
        self.assertEqual(row.status, 'DRAFT')
        self.assertEqual(ComplianceRuleState.objects.filter(pk=1).values_list('current_version_id', flat=True).first(), before_publish_pointer)

    def test_version_switch_and_old_rules_remain_recomputable(self):
        _, client = self.platform('compliance-version-switch')
        # Materialize the virtual post-flush v1 so both versions are immutable
        # published rows when exercising the no-rewind publish behavior.
        bootstrap = client.post('/api/admin/platform/compliance-rules/1/publish', {}, format='json')
        self.assertEqual(bootstrap.status_code, 200, bootstrap.content)
        created = client.post('/api/admin/platform/compliance-rules', {'rules': self.draft_rules()}, format='json')
        version = created.data['data']['version']
        client.post(f'/api/admin/platform/compliance-rules/{version}/publish', {}, format='json')
        old_result = check_content_against_rules('STORE_NAME', '新版本禁用词', list(BUILTIN_RULES_V1), version=1)
        self.assertEqual(old_result.matched_rule_codes, ())
        from common.compliance import ComplianceViolation, check_content
        with self.assertRaises(ComplianceViolation) as blocked:
            check_content('STORE_NAME', '新版本禁用词')
        self.assertEqual(blocked.exception.code, 'STORE_ONLY_V2')
        self.assertEqual(BUILTIN_RULES_SHA256, rules_sha256(list(BUILTIN_RULES_V1)))
        # Publishing an older immutable version is a no-op and never silently rewinds current.
        client.post('/api/admin/platform/compliance-rules/1/publish', {}, format='json')
        self.assertEqual(ComplianceRuleState.objects.get(pk=1).current_version.version, version)

    def test_store_text_validation_receipts_unchanged_patch_and_rollback(self):
        _, client = self.platform('compliance-store-writer')
        base = {'slug': 'compliance-store', 'entity_prefix': 'CS', 'database_alias': 'store_b'}
        for changes in ({'name': '第一门店'}, {'introduction': '本店最佳'}):
            with self.subTest(changes=changes):
                response = client.post('/api/admin/platform/store-management', {**base, 'name': '合规门店', **changes}, format='json')
                self.assertEqual(response.status_code, 400, response.content)
                self.assertFalse(Store.objects.filter(slug='compliance-store').exists())
        created = client.post('/api/admin/platform/store-management',
                              {**base, 'name': '合规门店', 'introduction': '本店提供陪玩服务'}, format='json')
        self.assertEqual(created.status_code, 201, created.content)
        store = Store.objects.get(slug='compliance-store')
        receipts = StoreContentComplianceReceipt.objects.filter(store=store)
        self.assertEqual(set(receipts.values_list('field_name', flat=True)), {'STORE_NAME', 'STORE_INTRODUCTION'})
        self.assertNotIn('合规门店', str(receipts.values()))
        route = f'/api/admin/platform/store-management/{store.pk}'
        unchanged = client.patch(route, {'name': '合规门店', 'expected_updated_at': store.updated_at.isoformat()}, format='json')
        self.assertEqual(unchanged.status_code, 200, unchanged.content)
        self.assertEqual(receipts.count(), 2)
        store.refresh_from_db()
        changed = client.patch(route, {'introduction': '更新的合规介绍', 'expected_updated_at': store.updated_at.isoformat()}, format='json')
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual(receipts.count(), 3)
        current = Store.objects.get(pk=store.pk)
        with patch('common.compliance.record_store_receipt', side_effect=DatabaseError('receipt offline')):
            failed = client.patch(route, {'name': '不得保存', 'expected_updated_at': current.updated_at.isoformat()}, format='json')
        self.assertEqual(failed.status_code, 503, failed.content)
        current.refresh_from_db()
        self.assertEqual(current.name, '合规门店')
        self.assertEqual(receipts.count(), 3)

    def test_store_create_audit_failure_and_platform_receipt_read_scope(self):
        actor, client = self.platform('compliance-store-audit')
        with patch('stores.platform_store_management.PlatformAuditEvent.objects.create', side_effect=DatabaseError('audit offline')):
            response = client.post('/api/admin/platform/store-management', {
                'slug': 'not-saved', 'entity_prefix': 'NS', 'database_alias': 'store_b', 'name': '不得保存',
            }, format='json')
        self.assertEqual(response.status_code, 503)
        self.assertFalse(Store.objects.filter(slug='not-saved').exists())
        store = Store.objects.create(slug='receipt-store', entity_prefix='RS', database_alias='store_b', name='回执门店')
        other = Store.objects.first()
        route = f'/api/admin/platform/store-management/{store.pk}/content-compliance-receipts'
        self.assertEqual(client.get(route).data['data']['results'], [])
        self.assertEqual(client.get('/api/admin/platform/store-management/999999/content-compliance-receipts').status_code, 404)

    def test_first_draft_materializes_builtin_without_leaving_empty_state(self):
        _, client = self.platform('compliance-bootstrap')
        ComplianceRuleState.objects.all().delete()
        ComplianceRuleVersion.objects.all().delete()
        created = client.post('/api/admin/platform/compliance-rules', {'rules': self.draft_rules()}, format='json')
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.data['data']['version'], 2)
        self.assertEqual(ComplianceRuleState.objects.get(pk=1).current_version.version, 1)
        self.assertEqual(client.get('/api/admin/platform/compliance-rules').data['data']['current']['version'], 1)

    def test_receipt_pagination_filters_store_identity_and_rejects_invalid_queries(self):
        _, client = self.platform('compliance-receipt-pages')
        store = Store.objects.create(slug='receipt-pages', entity_prefix='RP', database_alias='store_b', name='分页门店')
        other = Store.objects.create(slug='receipt-other', entity_prefix='RO', database_alias='store_receipt_other', name='其他门店')
        StoreContentComplianceReceipt.objects.bulk_create([
            StoreContentComplianceReceipt(
                store=store, field_name='STORE_NAME' if index % 2 == 0 else 'STORE_INTRODUCTION',
                content_sha256=f'{index:064x}', rule_version=1, rule_set_sha256=BUILTIN_RULES_SHA256,
                matched_rule_codes=[], exempted_rule_codes=[], actor_type='PLATFORM_ACCOUNT',
                actor_id='1', actor_label='平台管理员',
            ) for index in range(1, 6)
        ])
        StoreContentComplianceReceipt.objects.create(
            store=other, field_name='STORE_NAME', content_sha256='f' * 64,
            rule_version=1, rule_set_sha256=BUILTIN_RULES_SHA256,
            matched_rule_codes=[], exempted_rule_codes=[], actor_type='PLATFORM_ACCOUNT',
            actor_id='1', actor_label='平台管理员',
        )
        route = f'/api/admin/platform/store-management/{store.pk}/content-compliance-receipts'
        first = client.get(route, {'page_size': 2})
        self.assertEqual(first.status_code, 200, first.content)
        data = first.data['data']
        self.assertEqual((data['count'], len(data['results'])), (5, 2))
        self.assertEqual({item['actorLabel'] for item in data['results']}, {'PLATFORM_ACCOUNT:1'})
        self.assertIsNotNone(data['next'])
        self.assertIsNone(data['previous'])
        self.assertEqual({(item['storeId'], item['storeSlug'], item['storeName']) for item in data['results']},
                         {(store.pk, store.slug, store.name)})
        second = client.get(route, {'page': 2, 'page_size': 2})
        self.assertEqual(second.data['data']['count'], 5)
        self.assertNotEqual({item['id'] for item in data['results']},
                            {item['id'] for item in second.data['data']['results']})
        filtered = client.get(route, {'field_name': 'STORE_NAME', 'page_size': 100})
        self.assertEqual(filtered.data['data']['count'], 2)
        self.assertEqual({item['fieldName'] for item in filtered.data['data']['results']}, {'STORE_NAME'})
        for query in ({'page': 0}, {'page': 'x'}, {'page_size': 101}, {'field_name': 'UNKNOWN'}, {'extra': '1'}):
            with self.subTest(query=query):
                self.assertEqual(client.get(route, query).status_code, 400)
        self.assertEqual(client.get(f'{route}?page=1&page=2').status_code, 400)

    def test_draft_version_uses_server_latest_after_reading_a_later_page(self):
        _, client = self.platform('compliance-latest-version')
        empty = client.get('/api/admin/platform/compliance-rules')
        self.assertEqual(empty.status_code, 200, empty.content)
        self.assertEqual(empty.data['data']['count'], 0)
        self.assertEqual(empty.data['data']['latestVersion'], 1)
        self.assertEqual(empty.data['data']['versions'], [])
        self.assertIsNone(empty.data['data']['next'])
        self.assertIsNone(empty.data['data']['previous'])
        bootstrap = client.post('/api/admin/platform/compliance-rules/1/publish', {}, format='json')
        self.assertEqual(bootstrap.status_code, 200, bootstrap.content)
        ComplianceRuleVersion.objects.bulk_create([
            ComplianceRuleVersion(
                version=number, status='DRAFT', rules=list(BUILTIN_RULES_V1),
                rules_sha256=BUILTIN_RULES_SHA256,
            ) for number in range(2, 46)
        ])
        later_page = client.get('/api/admin/platform/compliance-rules', {'page': 2, 'page_size': 20})
        self.assertEqual(later_page.status_code, 200, later_page.content)
        data = later_page.data['data']
        self.assertEqual((data['count'], data['latestVersion']), (45, 45))
        self.assertEqual([row['version'] for row in data['versions']], list(range(25, 5, -1)))
        self.assertEqual(data['current']['version'], 1)
        created = client.post('/api/admin/platform/compliance-rules', {'rules': self.draft_rules()}, format='json')
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.data['data']['version'], 46)

    def test_publish_never_recovers_old_version_after_state_loss(self):
        _, client = self.platform('compliance-state-loss')
        bootstrap = client.post('/api/admin/platform/compliance-rules/1/publish', {}, format='json')
        self.assertEqual(bootstrap.status_code, 200, bootstrap.content)
        created = client.post('/api/admin/platform/compliance-rules', {'rules': self.draft_rules()}, format='json')
        version = created.data['data']['version']
        self.assertEqual(client.post(f'/api/admin/platform/compliance-rules/{version}/publish', {}, format='json').status_code, 200)
        ComplianceRuleState.objects.all().delete()
        recovered = client.post('/api/admin/platform/compliance-rules/1/publish', {}, format='json')
        self.assertEqual(recovered.status_code, 503, recovered.content)
        self.assertFalse(ComplianceRuleState.objects.exists())
        state = ComplianceRuleState.objects.create(pk=1, current_version=None)
        self.assertEqual(client.post('/api/admin/platform/compliance-rules/1/publish', {}, format='json').status_code, 503)
        state.refresh_from_db()
        self.assertIsNone(state.current_version_id)

    def test_store_compliance_switch_is_platform_only_and_disables_checks_without_disabling_store(self):
        _, finance = self.platform('compliance-switch-finance', 'FINANCE')
        _, client = self.platform('compliance-switch-owner')
        store = Store.objects.create(
            slug='compliance-switch-store', entity_prefix='CSW', database_alias='store_b',
            name='合规开关门店', status=Store.Status.ACTIVE, initialized_at=timezone.now(),
        )
        route = f'/api/admin/platform/store-management/{store.pk}'
        denied = finance.patch(route, {
            'content_compliance_enabled': False, 'expected_updated_at': store.updated_at.isoformat(),
        }, format='json')
        self.assertEqual(denied.status_code, 403, denied.content)
        store.refresh_from_db()
        self.assertTrue(store.content_compliance_enabled)

        disabled = client.patch(route, {
            'content_compliance_enabled': False, 'expected_updated_at': store.updated_at.isoformat(),
        }, format='json')
        self.assertEqual(disabled.status_code, 200, disabled.content)
        self.assertIs(disabled.data['data']['content_compliance_enabled'], False)
        store.refresh_from_db()
        self.assertEqual(store.status, Store.Status.ACTIVE)
        self.assertFalse(store.content_compliance_enabled)
        with store_context(store), patch('common.compliance.current_rules', side_effect=AssertionError('不应读取全局规则')):
            self.assertIsNone(check_content('STORE_NAME', '第一门店'))

        store.refresh_from_db()
        changed_while_disabling = client.patch(route, {
            'content_compliance_enabled': False, 'name': '第一门店',
            'expected_updated_at': store.updated_at.isoformat(),
        }, format='json')
        self.assertEqual(changed_while_disabling.status_code, 200, changed_while_disabling.content)
        store.refresh_from_db()
        self.assertEqual(store.name, '第一门店')
        self.assertFalse(StoreContentComplianceReceipt.objects.filter(
            store=store, field_name='STORE_NAME', content_sha256__isnull=False,
        ).exists())

        enabled = client.patch(route, {
            'content_compliance_enabled': True, 'expected_updated_at': store.updated_at.isoformat(),
        }, format='json')
        self.assertEqual(enabled.status_code, 200, enabled.content)
        store.refresh_from_db()
        with store_context(store), self.assertRaises(ComplianceViolation):
            check_content('STORE_NAME', '第一门店')

    def test_rule_version_total_is_capped_at_500_under_write_lock(self):
        _, client = self.platform('compliance-version-limit')
        bootstrap = client.post('/api/admin/platform/compliance-rules/1/publish', {}, format='json')
        self.assertEqual(bootstrap.status_code, 200, bootstrap.content)
        ComplianceRuleVersion.objects.bulk_create([
            ComplianceRuleVersion(
                version=version, status='DRAFT', rules=list(BUILTIN_RULES_V1),
                rules_sha256=BUILTIN_RULES_SHA256,
            ) for version in range(2, 501)
        ])
        listing = client.get('/api/admin/platform/compliance-rules')
        self.assertEqual(listing.status_code, 200, listing.content)
        data = listing.data['data']
        self.assertEqual(set(data), {'current', 'versions', 'count', 'next', 'previous', 'latestVersion'})
        self.assertEqual(len(data['versions']), 20)
        self.assertEqual(data['count'], 500)
        self.assertEqual(data['latestVersion'], 500)
        self.assertIsNone(data['previous'])
        self.assertEqual(data['next'], 'http://admin.esports.club/api/admin/platform/compliance-rules?page=2&page_size=20')
        self.assertEqual(listing['X-Total-Count'], '500')
        self.assertEqual([row['version'] for row in data['versions']],
                         list(range(500, 480, -1)))
        self.assertIn('page=2', listing['Link'])
        middle = client.get('/api/admin/platform/compliance-rules', {'page': 2, 'page_size': 20})
        self.assertEqual(middle.status_code, 200, middle.content)
        self.assertEqual(middle.data['data']['count'], 500)
        self.assertEqual(middle.data['data']['latestVersion'], 500)
        self.assertEqual(middle.data['data']['previous'],
                         'http://admin.esports.club/api/admin/platform/compliance-rules?page=1&page_size=20')
        self.assertEqual(middle.data['data']['next'],
                         'http://admin.esports.club/api/admin/platform/compliance-rules?page=3&page_size=20')
        last = client.get('/api/admin/platform/compliance-rules', {'page': 25, 'page_size': 20})
        self.assertEqual(last.status_code, 200, last.content)
        self.assertEqual(last.data['data']['count'], 500)
        self.assertEqual(last.data['data']['latestVersion'], 500)
        self.assertEqual(last.data['data']['previous'],
                         'http://admin.esports.club/api/admin/platform/compliance-rules?page=24&page_size=20')
        self.assertIsNone(last.data['data']['next'])
        self.assertEqual([row['version'] for row in last.data['data']['versions']],
                         list(range(20, 0, -1)))
        self.assertNotIn('rel="next"', last['Link'])
        largest_page = client.get('/api/admin/platform/compliance-rules', {'page': 5, 'page_size': 100})
        self.assertEqual(largest_page.status_code, 200, largest_page.content)
        self.assertEqual(len(largest_page.data['data']['versions']), 100)
        self.assertEqual(largest_page.data['data']['count'], 500)
        for query in ({'page': 0}, {'page': 'x'}, {'page': 26}, {'page_size': 101},
                      {'page_size': 0}, {'extra': '1'}):
            with self.subTest(query=query):
                self.assertEqual(client.get('/api/admin/platform/compliance-rules', query).status_code, 400)
        self.assertEqual(client.get('/api/admin/platform/compliance-rules?page=1&page=2').status_code, 400)
        rejected = client.post('/api/admin/platform/compliance-rules', {'rules': self.draft_rules()}, format='json')
        self.assertEqual(rejected.status_code, 409, rejected.content)
        self.assertEqual(rejected.data['errorCode'], 'RULE_VERSION_LIMIT')
        self.assertEqual(ComplianceRuleVersion.objects.count(), 500)
