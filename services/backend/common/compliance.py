"""Versioned literal text compliance checks and content-only receipts.

The control-plane rule snapshot is authoritative. Receipts retain digests and
rule codes only; user content is never copied into compliance evidence.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import html
import json
import unicodedata
from dataclasses import dataclass
from typing import NamedTuple

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError, InterfaceError
from django.utils.html import strip_tags
from rest_framework.exceptions import APIException, ValidationError

from .compliance_defaults import DEFAULT_RULES_V1


STORE_NAME = 'STORE_NAME'
STORE_INTRODUCTION = 'STORE_INTRODUCTION'
SERVICE_RULES = 'SERVICE_RULES'
SERVICE_DETAILS = 'SERVICE_DETAILS'
GAMEPLAY_RULES = 'GAMEPLAY_RULES'
GAMEPLAY_DETAILS = 'GAMEPLAY_DETAILS'
PROVIDER_DISPLAY_NAME = 'PROVIDER_DISPLAY_NAME'
PROVIDER_REGISTRATION_DISPLAY_NAME = 'PROVIDER_REGISTRATION_DISPLAY_NAME'

CONTENT_FIELDS = frozenset({
    STORE_NAME, STORE_INTRODUCTION,
    SERVICE_RULES, SERVICE_DETAILS,
    GAMEPLAY_RULES, GAMEPLAY_DETAILS,
    PROVIDER_DISPLAY_NAME, PROVIDER_REGISTRATION_DISPLAY_NAME,
})
CATEGORIES = frozenset({'EXTREME_WORD', 'INTERPRETATION_CLAUSE', 'NICKNAME_RESERVED'})
SERVICE_TEXT_FIELDS = frozenset({SERVICE_RULES, SERVICE_DETAILS, GAMEPLAY_RULES, GAMEPLAY_DETAILS})
RULE_KEYS = frozenset({'code', 'category', 'applies_to', 'terms', 'message', 'enabled'})
MAX_RULES = 500
MAX_TERMS = 30
MAX_TERM_LENGTH = 100
MAX_CODE_LENGTH = 80
MAX_MESSAGE_LENGTH = 300
BUILTIN_VERSION = 1


class ComplianceUnavailable(APIException):
    status_code = 503
    default_detail = '内容合规规则暂不可用，请稍后重试'
    default_code = 'COMPLIANCE_RULES_UNAVAILABLE'


class ComplianceViolation(APIException):
    status_code = 400
    default_detail = '内容不符合平台合规规则'
    default_code = 'CONTENT_COMPLIANCE_VIOLATION'

    def __init__(self, *, field, code, matched_rule_codes, message):
        super().__init__(message, code=code)
        self.field = field
        self.code = code
        self.matched_rule_codes = tuple(matched_rule_codes)
        self.message = message


class ComplianceCheck(NamedTuple):
    version: int
    digest: str
    content_sha256: str
    matched_rule_codes: tuple[str, ...]
    exempted_rule_codes: tuple[str, ...]


@dataclass(frozen=True)
class RuleSnapshot:
    version: int
    rules: tuple[dict, ...]
    rules_sha256: str
    source: str


def normalize_text(value: str) -> str:
    """Normalize literal matching text without evaluating regex or markup."""
    if not isinstance(value, str):
        raise TypeError('合规文本必须是字符串')
    value = unicodedata.normalize('NFKC', html.unescape(value))
    value = strip_tags(value).casefold()
    return ''.join(char for char in value if char.isalnum())


def _invalid(message):
    raise ValidationError(message)


def normalize_rules(rules, *, require_publishable=False):
    """Validate the closed rule schema and return a canonical JSON-safe list."""
    if not isinstance(rules, list):
        _invalid('rules 必须是数组')
    if len(rules) > MAX_RULES:
        _invalid(f'规则数量不能超过 {MAX_RULES}')
    normalized = []
    codes = set()
    for index, raw in enumerate(rules):
        if not isinstance(raw, dict):
            _invalid(f'第 {index + 1} 条规则必须是对象')
        if set(raw) != RULE_KEYS:
            _invalid(f'第 {index + 1} 条规则字段不完整或包含未知字段')
        code = raw['code']
        if not isinstance(code, str) or not code.strip() or len(code.strip()) > MAX_CODE_LENGTH:
            _invalid(f'第 {index + 1} 条规则 code 无效')
        code = code.strip()
        if any(not (char.isalnum() or char in '._:-') for char in code):
            _invalid(f'第 {index + 1} 条规则 code 格式无效')
        if code in codes:
            _invalid(f'规则 code 重复：{code}')
        codes.add(code)
        category = raw['category']
        if not isinstance(category, str) or category not in CATEGORIES:
            _invalid(f'规则 {code} 的类别无效')
        applies_to = raw['applies_to']
        if not isinstance(applies_to, list) or not applies_to or len(applies_to) > len(CONTENT_FIELDS):
            _invalid(f'规则 {code} 的适用字段无效')
        if any(not isinstance(field, str) or field not in CONTENT_FIELDS for field in applies_to):
            _invalid(f'规则 {code} 包含不支持的适用字段')
        if len(set(applies_to)) != len(applies_to):
            _invalid(f'规则 {code} 的适用字段重复')
        terms = raw['terms']
        if not isinstance(terms, list) or not 1 <= len(terms) <= MAX_TERMS:
            _invalid(f'规则 {code} 必须包含 1 至 {MAX_TERMS} 个词条')
        clean_terms = []
        seen_terms = set()
        for term in terms:
            if not isinstance(term, str) or not term.strip() or len(term.strip()) > MAX_TERM_LENGTH:
                _invalid(f'规则 {code} 的词条必须是 {MAX_TERM_LENGTH} 字以内的非空短文本')
            clean_term = normalize_text(term)
            if not clean_term:
                _invalid(f'规则 {code} 的词条归一化后不能为空')
            if clean_term in seen_terms:
                _invalid(f'规则 {code} 包含归一化后重复的词条')
            seen_terms.add(clean_term)
            clean_terms.append(clean_term)
        message = raw['message']
        if not isinstance(message, str) or not message.strip() or len(message.strip()) > MAX_MESSAGE_LENGTH:
            _invalid(f'规则 {code} 的提示信息无效')
        if type(raw['enabled']) is not bool:
            _invalid(f'规则 {code} 的 enabled 必须是布尔值')
        normalized.append({
            'code': code,
            'category': category,
            'applies_to': list(applies_to),
            'terms': clean_terms,
            'message': message.strip(),
            'enabled': raw['enabled'],
        })
    if require_publishable:
        enabled = [rule for rule in normalized if rule['enabled']]
        interpretation = [rule for rule in enabled if rule['category'] == 'INTERPRETATION_CLAUSE']
        if len(interpretation) < 21:
            _invalid('发布前至少需要 21 条启用的解释权条款规则')
        interpretation_fields = {field for rule in interpretation for field in rule['applies_to']}
        if not SERVICE_TEXT_FIELDS.issubset(interpretation_fields):
            _invalid('解释权条款规则必须覆盖服务规则、服务详情、玩法规则和玩法详情')
        covered = {field for rule in enabled for field in rule['applies_to']}
        missing = sorted(CONTENT_FIELDS - covered)
        if missing:
            _invalid(f'启用规则尚未覆盖全部字段：{", ".join(missing)}')
    return normalized


def serialize_rules(rules) -> str:
    return json.dumps(rules, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def rules_sha256(rules) -> str:
    return hashlib.sha256(serialize_rules(rules).encode('utf-8')).hexdigest()


BUILTIN_RULES_V1 = tuple(normalize_rules(copy.deepcopy(DEFAULT_RULES_V1), require_publishable=True))
BUILTIN_RULES_SHA256 = rules_sha256(BUILTIN_RULES_V1)


def _builtin_snapshot():
    return RuleSnapshot(BUILTIN_VERSION, copy.deepcopy(BUILTIN_RULES_V1), BUILTIN_RULES_SHA256, 'BUILTIN_V1')


def current_rules() -> RuleSnapshot:
    """Read the published control-plane snapshot, failing closed when invalid.

    Built-in v1 is only an explicit test/bootstrap fallback. Production must
    initialize the immutable version and singleton pointer through migrations
    or the platform bootstrap command before serving content checks.
    """
    from stores.models import ComplianceRuleState, ComplianceRuleVersion
    try:
        state = ComplianceRuleState.objects.filter(pk=1).first()
        if state is None:
            if ComplianceRuleVersion.objects.exists():
                raise ComplianceUnavailable()
            if getattr(settings, 'COMPLIANCE_ALLOW_BUILTIN_BOOTSTRAP', False):
                return _builtin_snapshot()
            raise ComplianceUnavailable()
        if state.current_version_id is None:
            raise ComplianceUnavailable()
        row = ComplianceRuleVersion.objects.get(pk=state.current_version_id)
        if row.status != ComplianceRuleVersion.Status.PUBLISHED:
            raise ComplianceUnavailable()
        rules = normalize_rules(row.rules, require_publishable=True)
        digest = rules_sha256(rules)
        if not hmac.compare_digest(row.rules_sha256, digest):
            raise ComplianceUnavailable()
        return RuleSnapshot(row.version, tuple(copy.deepcopy(rules)), row.rules_sha256, 'DATABASE')
    except ComplianceUnavailable:
        raise
    except (DatabaseError, InterfaceError, ComplianceRuleVersion.DoesNotExist,
            DjangoValidationError, ValidationError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise ComplianceUnavailable() from exc


def _evaluate(field, value, rules, *, version, digest, allow_reserved):
    if field not in CONTENT_FIELDS:
        raise ValueError(f'不支持的合规字段：{field}')
    if type(allow_reserved) is not bool:
        raise TypeError('allow_reserved 必须是布尔值')
    content = normalize_text(value)
    content_sha256 = hashlib.sha256(value.encode('utf-8')).hexdigest()
    matched = []
    exempted = []
    blocking = []
    for rule in rules:
        if not rule['enabled'] or field not in rule['applies_to']:
            continue
        if not any(term in content for term in rule['terms']):
            continue
        matched.append(rule['code'])
        if allow_reserved and rule['category'] == 'NICKNAME_RESERVED':
            exempted.append(rule['code'])
        else:
            blocking.append(rule)
    if blocking:
        first = blocking[0]
        raise ComplianceViolation(
            field=field,
            code=first['code'],
            matched_rule_codes=tuple(rule['code'] for rule in blocking),
            message=first['message'],
        )
    return ComplianceCheck(version, digest, content_sha256, tuple(matched), tuple(exempted))


def check_content(field, value, allow_reserved=False, *, enabled=None) -> ComplianceCheck | None:
    """Check current content, or return ``None`` when its store has opted out.

    ``enabled`` is used by the platform store serializer so the value in the
    same create/update request takes effect before the store row is saved.
    Ordinary store requests always use the current tenant directory record.
    """
    if field not in CONTENT_FIELDS:
        raise ValueError(f'不支持的合规字段：{field}')
    if type(allow_reserved) is not bool:
        raise TypeError('allow_reserved 必须是布尔值')
    if enabled is None:
        from stores.context import current_store
        store = current_store()
        enabled = getattr(store, 'content_compliance_enabled', True) if store is not None else True
    if type(enabled) is not bool:
        raise TypeError('enabled 必须是布尔值')
    if not enabled:
        return None
    snapshot = current_rules()
    return _evaluate(field, value, snapshot.rules, version=snapshot.version,
                     digest=snapshot.rules_sha256, allow_reserved=allow_reserved)


def check_content_against_rules(field, value, rules, *, version, allow_reserved=False) -> ComplianceCheck:
    """Recompute an old receipt from its retained version rules without switching current."""
    if type(version) is not int or version <= 0:
        raise ValueError('历史规则版本必须为正整数')
    normalized = normalize_rules(copy.deepcopy(rules))
    return _evaluate(field, value, normalized, version=version, digest=rules_sha256(normalized),
                     allow_reserved=allow_reserved)


_ACTOR_TYPES = frozenset({'SYSTEM', 'STORE_STAFF', 'PLATFORM_ACCOUNT'})
_ACTOR_LABEL_PREFIXES = {'SYSTEM': 'USER', 'STORE_STAFF': 'ACCOUNT', 'PLATFORM_ACCOUNT': 'PLATFORM_ACCOUNT'}
_FIXED_PUBLIC_ACTOR_LABELS = frozenset({'SYSTEM', 'PUBLIC_REGISTRATION'})


def actor_label_for_account(account):
    """Return a stable non-content identity, never a nickname or username."""
    account_id = getattr(account, 'pk', None)
    return f'ACCOUNT:{account_id}' if account_id not in (None, '') else 'SYSTEM'


def normalize_actor_label(actor_type, actor_id='', actor_label=''):
    """Derive receipt labels from IDs and discard untrusted historical labels."""
    actor_type = str(actor_type or 'SYSTEM')
    actor_id = '' if actor_id in (None, '') else str(actor_id)
    if actor_id:
        prefix = _ACTOR_LABEL_PREFIXES.get(actor_type, 'ACTOR')
        return f'{prefix}:{actor_id}'[:150]
    if actor_label in _FIXED_PUBLIC_ACTOR_LABELS:
        return actor_label
    return actor_type if actor_type in _ACTOR_TYPES else 'SYSTEM'


def store_actor_from_request(request):
    account = getattr(request, 'platform_account', None)
    if account is not None:
        actor_type = 'PLATFORM_ACCOUNT'
    else:
        account = getattr(request, 'account', None)
        actor_type = 'STORE_STAFF' if account is not None else 'SYSTEM'
    if account is not None:
        actor_id = '' if account.pk in (None, '') else str(account.pk)
        return actor_type, actor_id, normalize_actor_label(actor_type, actor_id)
    user = getattr(request, 'user', None) or getattr(request, 'legacy_user', None)
    actor_id = '' if user is None or user.pk in (None, '') else str(user.pk)
    return 'SYSTEM', actor_id, normalize_actor_label('SYSTEM', actor_id)


def record_store_receipt(store, field_name, result, *, actor_type, actor_id='', actor_label=''):
    from stores.models import StoreContentComplianceReceipt
    if not isinstance(result, ComplianceCheck):
        raise TypeError('result 必须来自同一次合规检查')
    if field_name not in CONTENT_FIELDS:
        raise ValueError('field_name 无效')
    actor_type = actor_type or 'SYSTEM'
    if actor_type not in _ACTOR_TYPES:
        raise ValueError('actor_type 无效')
    actor_id = '' if actor_id in (None, '') else str(actor_id)[:100]
    defaults = {
        'rule_set_sha256': result.digest,
        'matched_rule_codes': list(result.matched_rule_codes),
        'exempted_rule_codes': list(result.exempted_rule_codes),
        'actor_type': actor_type,
        'actor_id': actor_id,
        'actor_label': normalize_actor_label(actor_type, actor_id, actor_label),
    }
    return StoreContentComplianceReceipt.objects.create(
        store=store, field_name=field_name, content_sha256=result.content_sha256,
        rule_version=result.version, **defaults,
    )


def record_content_receipt(content_type, object_id, field_name, result, *, actor_account=None,
                           actor_type=None, actor_id='', actor_label=''):
    from console.models import ContentComplianceReceipt
    if not isinstance(result, ComplianceCheck):
        raise TypeError('result 必须来自同一次合规检查')
    if not isinstance(content_type, str) or not content_type or len(content_type) > 32:
        raise ValueError('content_type 无效')
    if field_name not in CONTENT_FIELDS:
        raise ValueError('field_name 无效')
    if actor_account is not None:
        actor_type = actor_type or 'STORE_STAFF'
        actor_id = actor_id or ('' if actor_account.pk in (None, '') else str(actor_account.pk))
    actor_type = actor_type or 'SYSTEM'
    if actor_type not in _ACTOR_TYPES:
        raise ValueError('actor_type 无效')
    actor_id = '' if actor_id in (None, '') else str(actor_id)[:100]
    return ContentComplianceReceipt.objects.create(
        content_type=content_type, object_id=object_id, field_name=field_name,
        content_sha256=result.content_sha256, rule_version=result.version,
        rule_set_sha256=result.digest,
        matched_rule_codes=list(result.matched_rule_codes),
        exempted_rule_codes=list(result.exempted_rule_codes),
        actor_account=actor_account,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_label=normalize_actor_label(actor_type, actor_id, actor_label),
    )
