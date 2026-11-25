"""Platform super-admin management of the literal text compliance library."""
from collections.abc import Mapping
from urllib.parse import urlencode

from django.core.paginator import EmptyPage, Paginator
from django.db import DatabaseError, InterfaceError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from common.compliance import (
    BUILTIN_RULES_SHA256, BUILTIN_RULES_V1, BUILTIN_VERSION,
    ComplianceUnavailable, CONTENT_FIELDS, MAX_RULES, current_rules, normalize_actor_label,
    normalize_rules, rules_sha256,
)
from .hosts import is_admin_host
from .models import ComplianceRuleState, ComplianceRuleVersion, PlatformAuditEvent, Store, StoreContentComplianceReceipt
from .platform_auth import IsPlatformAuthenticated, PlatformAuthentication
from .platform_permissions import platform_may


def _version_data(row):
    return {
        'version': row.version,
        'status': row.status,
        'rules': row.rules,
        'rulesSha256': row.rules_sha256,
        'ruleCount': len(row.rules),
        'createdAt': row.created_at.isoformat() if row.created_at else None,
        'publishedAt': row.published_at.isoformat() if row.published_at else None,
    }


def _builtin_data():
    return {
        'version': BUILTIN_VERSION,
        'status': ComplianceRuleVersion.Status.PUBLISHED,
        'rules': list(BUILTIN_RULES_V1),
        'rulesSha256': BUILTIN_RULES_SHA256,
        'ruleCount': len(BUILTIN_RULES_V1),
        'createdAt': None,
        'publishedAt': None,
    }


def _create_builtin_version(account):
    return ComplianceRuleVersion.objects.create(
        version=BUILTIN_VERSION,
        status=ComplianceRuleVersion.Status.PUBLISHED,
        rules=list(BUILTIN_RULES_V1),
        rules_sha256=BUILTIN_RULES_SHA256,
        created_by=account,
        published_by=account,
        published_at=timezone.now(),
    )


def _audit(account, action, *, version, rule_count, digest, status):
    # Deliberately metadata-only: rule bodies and checked content never enter audit.
    PlatformAuditEvent.objects.create(
        account=account,
        action=action,
        target_type='compliance_rule_version',
        target_id=str(version),
        detail={
            'version': version,
            'ruleCount': rule_count,
            'rulesSha256': digest,
            'status': status,
        },
    )


def _locked_current_state(account):
    state = ComplianceRuleState.objects.select_for_update().filter(pk=1).first()
    if state is None:
        if ComplianceRuleVersion.objects.exists():
            raise ComplianceUnavailable()
        state = ComplianceRuleState.objects.create(pk=1)
    if state.current_version_id is None:
        if ComplianceRuleVersion.objects.exists():
            raise ComplianceUnavailable()
        state.current_version = _create_builtin_version(account)
        state.save(update_fields=['current_version', 'updated_at'])
    current_rules()
    return state


class PlatformComplianceBase(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def allowed(self, request, action):
        return is_admin_host(request.get_host()) and platform_may(request.platform_account, action)

    def denied(self):
        return Response({'code': 403, 'errorCode': 'PLATFORM_FORBIDDEN', 'msg': '需要平台超级管理员权限'}, status=403)

    def unavailable(self):
        return Response({'code': 503, 'errorCode': 'COMPLIANCE_RULES_UNAVAILABLE',
                         'msg': '内容合规规则暂不可用，请稍后重试'}, status=503)


class PlatformComplianceRulesView(PlatformComplianceBase):
    def invalid(self, message):
        return Response({'code': 400, 'errorCode': 'VALIDATION_ERROR', 'msg': message}, status=400)

    def positive_param(self, request, name, default, maximum):
        values = request.query_params.getlist(name)
        if len(values) > 1:
            raise ValidationError(f'{name} 不能重复')
        raw = values[0] if values else str(default)
        if not raw.isascii() or not raw.isdigit() or not 1 <= int(raw) <= maximum:
            raise ValidationError(f'{name} 无效')
        return int(raw)

    def link(self, request, page_number, page_size):
        query = urlencode({'page': page_number, 'page_size': page_size})
        return f'{request.build_absolute_uri(request.path)}?{query}'

    def get(self, request):
        if not self.allowed(request, 'compliance:view'):
            return self.denied()
        if any(key not in {'page', 'page_size'} for key in request.query_params):
            return self.invalid('存在不支持的查询参数')
        try:
            page_number = self.positive_param(request, 'page', 1, 2_147_483_647)
            page_size = self.positive_param(request, 'page_size', 20, 100)
        except ValidationError as exc:
            return self.invalid(str(exc.detail[0]))
        try:
            snapshot = current_rules()
            current_row = ComplianceRuleVersion.objects.filter(version=snapshot.version).first()
            current = _version_data(current_row) if current_row is not None else None
            if current is None and snapshot.source == 'BUILTIN_V1':
                current = _builtin_data()
            versions = ComplianceRuleVersion.objects.order_by('-version')
            latest_version = versions.values_list('version', flat=True).first() or BUILTIN_VERSION
            paginator = Paginator(versions, page_size)
            try:
                page = paginator.page(page_number)
            except EmptyPage:
                return self.invalid('page 超出范围')
            next_link = self.link(request, page.next_page_number(), page_size) if page.has_next() else None
            previous_link = self.link(request, page.previous_page_number(), page_size) if page.has_previous() else None
            links = []
            if next_link:
                links.append(f'<{next_link}>; rel="next"')
            if previous_link:
                links.append(f'<{previous_link}>; rel="previous"')
            response = Response({'code': 0, 'data': {
                'current': current,
                'versions': [_version_data(row) for row in page.object_list],
                'count': paginator.count,
                'next': next_link,
                'previous': previous_link,
                'latestVersion': max(latest_version, snapshot.version, BUILTIN_VERSION),
            }})
            response['X-Total-Count'] = str(paginator.count)
            if links:
                response['Link'] = ', '.join(links)
            return response
        except (DatabaseError, InterfaceError, ComplianceUnavailable):
            return self.unavailable()

    def post(self, request):
        if not self.allowed(request, 'compliance:manage'):
            return self.denied()
        if not isinstance(request.data, Mapping) or set(request.data) != {'rules'}:
            return Response({'code': 400, 'errorCode': 'VALIDATION_ERROR', 'msg': '请求只能包含 rules 数组'}, status=400)
        try:
            rules = normalize_rules(request.data['rules'])
        except ValidationError as exc:
            return Response({'code': 400, 'errorCode': 'VALIDATION_ERROR', 'msg': str(exc.detail[0])}, status=400)
        digest = rules_sha256(rules)
        try:
            with transaction.atomic(using='default'):
                _locked_current_state(request.platform_account)
                if ComplianceRuleVersion.objects.count() >= MAX_RULES:
                    return Response({'code': 409, 'errorCode': 'RULE_VERSION_LIMIT',
                                     'msg': f'合规规则版本总数不能超过 {MAX_RULES}'}, status=409)
                latest = ComplianceRuleVersion.objects.order_by('-version').values_list('version', flat=True).first() or 0
                row = ComplianceRuleVersion.objects.create(
                    version=max(latest, BUILTIN_VERSION) + 1,
                    status=ComplianceRuleVersion.Status.DRAFT,
                    rules=rules,
                    rules_sha256=digest,
                    created_by=request.platform_account,
                )
                _audit(request.platform_account, 'compliance.draft_created', version=row.version,
                       rule_count=len(rules), digest=digest, status=row.status)
            return Response({'code': 0, 'data': _version_data(row)}, status=201)
        except (DatabaseError, InterfaceError):
            return self.unavailable()


class PlatformComplianceRuleDetailView(PlatformComplianceBase):
    def get(self, request, version):
        if not self.allowed(request, 'compliance:view'):
            return self.denied()
        try:
            snapshot = current_rules()
            row = ComplianceRuleVersion.objects.filter(version=version).first()
            if row is None and version == BUILTIN_VERSION and snapshot.source == 'BUILTIN_V1':
                return Response({'code': 0, 'data': _builtin_data()})
            if row is None:
                return Response({'code': 404, 'errorCode': 'NOT_FOUND', 'msg': '规则版本不存在'}, status=404)
            return Response({'code': 0, 'data': _version_data(row)})
        except (DatabaseError, InterfaceError):
            return self.unavailable()


class PlatformComplianceRulePublishView(PlatformComplianceBase):
    def post(self, request, version):
        if not self.allowed(request, 'compliance:manage'):
            return self.denied()
        try:
            with transaction.atomic(using='default'):
                state = ComplianceRuleState.objects.select_for_update().filter(pk=1).first()
                if state is None:
                    if ComplianceRuleVersion.objects.exists() or version != BUILTIN_VERSION:
                        raise ComplianceUnavailable()
                    state = ComplianceRuleState.objects.create(pk=1)
                if state.current_version_id is None:
                    if ComplianceRuleVersion.objects.exists() or version != BUILTIN_VERSION:
                        raise ComplianceUnavailable()
                    state.current_version = _create_builtin_version(request.platform_account)
                    state.save(update_fields=['current_version', 'updated_at'])
                current_rules()
                row = ComplianceRuleVersion.objects.select_for_update().filter(version=version).first()
                if row is None:
                    return Response({'code': 404, 'errorCode': 'NOT_FOUND', 'msg': '规则版本不存在'}, status=404)
                if row.status == ComplianceRuleVersion.Status.DRAFT:
                    rules = normalize_rules(row.rules, require_publishable=True)
                    digest = rules_sha256(rules)
                    if digest != row.rules_sha256:
                        raise ComplianceUnavailable()
                    row.rules = rules
                    row.status = ComplianceRuleVersion.Status.PUBLISHED
                    row.published_by = request.platform_account
                    row.published_at = timezone.now()
                    row.save(update_fields=['rules', 'status', 'published_by', 'published_at'])
                    state.current_version = row
                    state.save(update_fields=['current_version', 'updated_at'])
                elif row.status != ComplianceRuleVersion.Status.PUBLISHED:
                    raise ComplianceUnavailable()
                # Republishing any immutable published version is a no-op, including
                # when a newer version is current; history is never rewound silently.
                _audit(request.platform_account, 'compliance.published', version=row.version,
                       rule_count=len(row.rules), digest=row.rules_sha256, status=row.status)
            return Response({'code': 0, 'data': _version_data(row)})
        except ValidationError as exc:
            return Response({'code': 400, 'errorCode': 'VALIDATION_ERROR', 'msg': str(exc.detail[0])}, status=400)
        except (DatabaseError, InterfaceError, ComplianceUnavailable):
            return self.unavailable()


class PlatformStoreContentComplianceReceiptsView(PlatformComplianceBase):
    def invalid(self, message):
        return Response({'code': 400, 'errorCode': 'VALIDATION_ERROR', 'msg': message}, status=400)

    def single_param(self, request, name, default=''):
        values = request.query_params.getlist(name)
        if len(values) > 1:
            raise ValidationError(f'{name} 不能重复')
        return values[0] if values else default

    def positive_param(self, request, name, default, maximum):
        raw = self.single_param(request, name, str(default))
        if not raw.isascii() or not raw.isdigit() or not 1 <= int(raw) <= maximum:
            raise ValidationError(f'{name} 无效')
        return int(raw)

    def link(self, request, page, *, field_name, page_size):
        params = {'page': page, 'page_size': page_size}
        if field_name:
            params['field_name'] = field_name
        return request.build_absolute_uri(request.path) + '?' + urlencode(params)

    def get(self, request, pk):
        if not self.allowed(request, 'compliance:view'):
            return self.denied()
        try:
            store = Store.objects.filter(pk=pk).first()
            if store is None:
                return Response({'code': 404, 'errorCode': 'NOT_FOUND', 'msg': '门店不存在'}, status=404)
            if any(key not in {'field_name', 'page', 'page_size'} for key in request.query_params):
                return self.invalid('存在不支持的查询参数')
            field_name = self.single_param(request, 'field_name')
            if field_name and field_name not in CONTENT_FIELDS:
                return self.invalid('field_name 无效')
            page_number = self.positive_param(request, 'page', 1, 2_147_483_647)
            page_size = self.positive_param(request, 'page_size', 20, 100)
            receipts = StoreContentComplianceReceipt.objects.filter(store=store)
            if field_name:
                receipts = receipts.filter(field_name=field_name)
            paginator = Paginator(receipts.order_by('-created_at', '-pk'), page_size)
            try:
                page = paginator.page(page_number)
            except EmptyPage:
                return self.invalid('page 超出范围')
            data = [{
                'id': row.pk,
                'storeId': store.pk,
                'storeSlug': store.slug,
                'storeName': store.name,
                'fieldName': row.field_name,
                'contentSha256': row.content_sha256,
                'ruleVersion': row.rule_version,
                'ruleSetSha256': row.rule_set_sha256,
                'matchedRuleCodes': row.matched_rule_codes,
                'exemptedRuleCodes': row.exempted_rule_codes,
                'actorType': row.actor_type,
                'actorId': row.actor_id,
                'actorLabel': normalize_actor_label(row.actor_type, row.actor_id, row.actor_label),
                'createdAt': row.created_at.isoformat(),
            } for row in page.object_list]
            return Response({'code': 0, 'data': {
                'results': data,
                'count': paginator.count,
                'next': self.link(request, page.next_page_number(), field_name=field_name, page_size=page_size)
                       if page.has_next() else None,
                'previous': self.link(request, page.previous_page_number(), field_name=field_name, page_size=page_size)
                            if page.has_previous() else None,
            }})
        except ValidationError as exc:
            return self.invalid(str(exc.detail[0]))
        except (DatabaseError, InterfaceError):
            return self.unavailable()
