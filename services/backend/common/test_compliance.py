import copy
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError
from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError

from common.compliance import (
    BUILTIN_RULES_SHA256, BUILTIN_RULES_V1, CONTENT_FIELDS,
    ComplianceUnavailable, ComplianceViolation, actor_label_for_account, check_content,
    check_content_against_rules, current_rules, normalize_rules,
    normalize_text, record_content_receipt, record_store_receipt, rules_sha256, serialize_rules,
)
from console.models import ContentComplianceReceipt
from stores.models import ComplianceRuleState, ComplianceRuleVersion, Store


class ComplianceTextTests(TestCase):
    databases = {'default'}

    def test_nfkc_whitespace_zero_width_and_html_cannot_hide_literal_terms(self):
        self.assertEqual(normalize_text(' ＡＢ\u200bＣ '), 'abc')
        self.assertEqual(normalize_text('&lt;b&gt;最终&lt;/b&gt; 解释权归本店'), '最终解释权归本店')
        for value in ('最\u200b终解释权归本店', ' 最 终 解 释 权 归 本 店 ',
                      '<p>最终<b>解释权</b>归本店</p>', '最终解释权归本店&#x3002;'):
            with self.subTest(value=value), self.assertRaises(ComplianceViolation):
                check_content('SERVICE_RULES', value)

    def test_defaults_have_21_independent_interpretation_rules_and_full_coverage(self):
        interpretation = [rule for rule in BUILTIN_RULES_V1
                          if rule['category'] == 'INTERPRETATION_CLAUSE' and rule['enabled']]
        self.assertEqual(len(interpretation), 21)
        self.assertEqual(len({rule['code'] for rule in interpretation}), 21)
        self.assertEqual({field for rule in interpretation for field in rule['applies_to']},
                         {'SERVICE_RULES', 'SERVICE_DETAILS', 'GAMEPLAY_RULES', 'GAMEPLAY_DETAILS'})
        self.assertEqual({field for rule in BUILTIN_RULES_V1 if rule['enabled'] for field in rule['applies_to']},
                         CONTENT_FIELDS)
        self.assertEqual(rules_sha256(BUILTIN_RULES_V1), BUILTIN_RULES_SHA256)
        self.assertEqual(serialize_rules(BUILTIN_RULES_V1), serialize_rules(copy.deepcopy(BUILTIN_RULES_V1)))

    def test_rule_schema_rejects_unknown_duplicate_empty_illegal_and_excessive_values(self):
        base = copy.deepcopy(BUILTIN_RULES_V1[0])
        invalid = []
        unknown = copy.deepcopy(base); unknown['extra'] = True; invalid.append([unknown])
        missing = copy.deepcopy(base); del missing['message']; invalid.append([missing])
        duplicate = [copy.deepcopy(base), copy.deepcopy(base)]; invalid.append(duplicate)
        for changes in ({'category': 'OTHER'}, {'applies_to': ['OTHER']}, {'terms': []},
                        {'terms': ['   ']}, {'terms': ['x'] * 31}, {'enabled': 1},
                        {'terms': ['x' * 101]}, {'terms': ['Ａ', 'a']}, {'code': '非法 编码'}):
            row = copy.deepcopy(base); row.update(changes); invalid.append([row])
        invalid.append([{**copy.deepcopy(base), 'code': f'X{index}'} for index in range(501)])
        for rules in invalid:
            with self.subTest(rules=str(rules)[:100]), self.assertRaises(ValidationError):
                normalize_rules(rules)

    def test_publish_requires_interpretation_count_and_all_field_coverage(self):
        with self.assertRaises(ValidationError):
            normalize_rules(copy.deepcopy(BUILTIN_RULES_V1[:20]), require_publishable=True)
        rules = copy.deepcopy(BUILTIN_RULES_V1)
        for rule in rules:
            if rule['code'] != 'EXTREME_WORD_COMMON':
                rule['applies_to'] = [field for field in rule['applies_to'] if field != 'STORE_NAME']
        with self.assertRaises(ValidationError):
            normalize_rules(rules, require_publishable=True)

    def test_reserved_exemption_is_narrow_and_historical_check_uses_explicit_version(self):
        result = check_content('PROVIDER_DISPLAY_NAME', '客服小安', allow_reserved=True)
        self.assertEqual(result.exempted_rule_codes, ('NICKNAME_RESERVED_ROLES',))
        self.assertEqual(result.matched_rule_codes, ('NICKNAME_RESERVED_ROLES',))
        with self.assertRaises(ComplianceViolation) as blocked:
            check_content('PROVIDER_DISPLAY_NAME', '官方最佳接待', allow_reserved=True)
        self.assertIn('EXTREME_WORD_COMMON', blocked.exception.matched_rule_codes)
        self.assertNotIn('NICKNAME_RESERVED_ROLES', blocked.exception.matched_rule_codes)
        historical = check_content_against_rules(
            'PROVIDER_DISPLAY_NAME', '客服小安', list(BUILTIN_RULES_V1),
            version=1, allow_reserved=True,
        )
        self.assertEqual(historical.version, 1)
        self.assertEqual(historical.digest, BUILTIN_RULES_SHA256)

    def test_literal_matching_never_executes_rule_text_as_regex(self):
        rules = list(copy.deepcopy(BUILTIN_RULES_V1))
        rules.append({'code': 'LITERAL', 'category': 'EXTREME_WORD', 'applies_to': ['STORE_NAME'],
                      'terms': ['a.b'], 'message': '字面词', 'enabled': True})
        rules = normalize_rules(rules, require_publishable=True)
        row = ComplianceRuleVersion.objects.create(version=99, status='PUBLISHED', rules=rules,
                                                   rules_sha256=rules_sha256(rules))
        ComplianceRuleState.objects.update_or_create(pk=1, defaults={'current_version': row})
        self.assertEqual(check_content('STORE_NAME', 'axb').matched_rule_codes, ())
        with self.assertRaises(ComplianceViolation):
            check_content('STORE_NAME', 'a.b')


class ComplianceSnapshotTests(TestCase):
    databases = {'default'}

    def setUp(self):
        ComplianceRuleState.objects.all().delete()
        ComplianceRuleVersion.objects.all().delete()

    def test_absent_state_uses_builtin_only_in_explicit_bootstrap_mode(self):
        snapshot = current_rules()
        self.assertEqual((snapshot.version, snapshot.source, snapshot.rules_sha256),
                         (1, 'BUILTIN_V1', BUILTIN_RULES_SHA256))
        with override_settings(COMPLIANCE_ALLOW_BUILTIN_BOOTSTRAP=False):
            with self.assertRaises(ComplianceUnavailable):
                current_rules()
        ComplianceRuleState.objects.create(pk=1, current_version=None)
        with self.assertRaises(ComplianceUnavailable):
            current_rules()

    def test_absent_state_with_existing_versions_fails_closed(self):
        ComplianceRuleVersion.objects.create(
            version=2, status='PUBLISHED', rules=list(BUILTIN_RULES_V1),
            rules_sha256=BUILTIN_RULES_SHA256,
        )
        with self.assertRaises(ComplianceUnavailable):
            current_rules()

    def test_missing_unpublished_corrupt_and_digest_mismatch_fail_closed(self):
        state = ComplianceRuleState.objects.create(pk=1, current_version=None)
        state.current_version_id = 999
        state.save(update_fields=['current_version'])
        with self.assertRaises(ComplianceUnavailable):
            current_rules()
        row = ComplianceRuleVersion.objects.create(
            version=2, status='DRAFT', rules=list(BUILTIN_RULES_V1), rules_sha256=BUILTIN_RULES_SHA256,
        )
        state.current_version = row; state.save(update_fields=['current_version'])
        with self.assertRaises(ComplianceUnavailable):
            current_rules()
        row.status = 'PUBLISHED'; row.save(update_fields=['status'])
        row.rules_sha256 = '0' * 64
        ComplianceRuleVersion.objects.filter(pk=row.pk).update(rules_sha256=row.rules_sha256)
        with self.assertRaises(ComplianceUnavailable):
            current_rules()
        row.rules_sha256 = BUILTIN_RULES_SHA256
        row.rules = [{'broken': True}]
        ComplianceRuleVersion.objects.filter(pk=row.pk).update(rules=row.rules)
        with self.assertRaises(ComplianceUnavailable):
            current_rules()
        row.rules = list(BUILTIN_RULES_V1)
        ComplianceRuleVersion.objects.filter(pk=row.pk).update(rules=row.rules)
        with patch('stores.models.ComplianceRuleState.objects.first', side_effect=DatabaseError('offline')):
            with self.assertRaises(ComplianceUnavailable):
                current_rules()

    def test_receipt_retains_only_digest_codes_and_actor_never_content(self):
        result = check_content('PROVIDER_DISPLAY_NAME', '客服小安', allow_reserved=True)
        receipt = record_content_receipt('escort_profile', 42, 'PROVIDER_DISPLAY_NAME', result,
                                         actor_label='TEST_STAFF')
        self.assertEqual(receipt.content_sha256, result.content_sha256)
        self.assertEqual(receipt.exempted_rule_codes, ['NICKNAME_RESERVED_ROLES'])
        self.assertNotIn('客服小安', str(ContentComplianceReceipt.objects.values().get(pk=receipt.pk)))
        self.assertEqual(ContentComplianceReceipt.objects.count(), 1)
        with self.assertRaises(ValueError):
            record_content_receipt('provider_registration', 42, 'UNKNOWN', result)

    def test_actor_labels_use_stable_ids_instead_of_names_or_nicknames(self):
        account = SimpleNamespace(pk=73, nickname='被校验的昵称正文', username='含正文的用户名')
        self.assertEqual(actor_label_for_account(account), 'ACCOUNT:73')
        result = check_content('PROVIDER_DISPLAY_NAME', '合规昵称')
        receipt = record_content_receipt(
            'escort_profile', 42, 'PROVIDER_DISPLAY_NAME', result,
            actor_type='STORE_STAFF', actor_id='73', actor_label='被校验的昵称正文',
        )
        self.assertEqual(receipt.actor_label, 'ACCOUNT:73')
        self.assertNotIn('被校验的昵称正文', str(ContentComplianceReceipt.objects.values().get(pk=receipt.pk)))

    def test_receipts_append_every_real_write_and_retain_latest_actor(self):
        store = Store.objects.create(slug='receipt-append', entity_prefix='RA',
                                     database_alias='store_receipt_append', name='回执追加门店')
        first = check_content('STORE_NAME', '门店甲')
        second = check_content('STORE_NAME', '门店乙')
        record_store_receipt(store, 'STORE_NAME', first, actor_type='PLATFORM_ACCOUNT',
                             actor_id='1', actor_label='操作者一')
        record_store_receipt(store, 'STORE_NAME', second, actor_type='PLATFORM_ACCOUNT',
                             actor_id='1', actor_label='操作者一')
        record_store_receipt(store, 'STORE_NAME', first, actor_type='PLATFORM_ACCOUNT',
                             actor_id='2', actor_label='操作者二')
        receipts = list(store.content_compliance_receipts.order_by('pk'))
        self.assertEqual(len(receipts), 3)
        self.assertEqual((receipts[-1].actor_id, receipts[-1].actor_label,
                          receipts[-1].content_sha256), ('2', 'PLATFORM_ACCOUNT:2', first.content_sha256))
        record_content_receipt('service_item', 42, 'SERVICE_RULES', first,
                               actor_type='PLATFORM_ACCOUNT', actor_id='1', actor_label='操作者一')
        record_content_receipt('service_item', 42, 'SERVICE_RULES', second,
                               actor_type='PLATFORM_ACCOUNT', actor_id='1', actor_label='操作者一')
        record_content_receipt('service_item', 42, 'SERVICE_RULES', first,
                               actor_type='PLATFORM_ACCOUNT', actor_id='2', actor_label='操作者二')
        local = list(ContentComplianceReceipt.objects.order_by('pk'))
        self.assertEqual(len(local), 3)
        self.assertEqual((local[-1].actor_type, local[-1].actor_id, local[-1].actor_label),
                         ('PLATFORM_ACCOUNT', '2', 'PLATFORM_ACCOUNT:2'))
