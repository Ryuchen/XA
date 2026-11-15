"""Platform-super-admin review of tenant KOOK License evidence."""
from collections.abc import Mapping
from uuid import UUID

from django.db import DatabaseError, transaction as control_transaction
from rest_framework.exceptions import ValidationError

from console.models import AdminAuditLog
from console.pagination import ConsolePagination
from .context import store_context
from .models import PlatformAuditEvent, Store
from .platform_accounts import AccountChangeError, _locked_actor
from .platform_license_views import _PlatformLicenseBase, _response


def _audit_detail(review):
    return {'request_id': str(review.request_id), 'outcome': review.outcome, 'reason': review.reason,
            'evidence_digest': review.evidence_digest,
            'awarded_points_hundredths': review.awarded_points, 'credited_milliseconds': review.credited_milliseconds,
            'skip_reason': review.skip_reason, 'review_id': review.pk,
            'original_actor_id': str(review.platform_actor_id)}


class PlatformKookLicenseReviewView(_PlatformLicenseBase):
    def get(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        kind = request.query_params.get('kind', 'PENDING')
        if kind not in ('PENDING', 'REVIEWED'):
            return _response({'code': 400, 'msg': '请选择待核对或已核对'}, 400)
        try:
            store = Store.objects.filter(pk=pk).first()
            if store is None:
                return _response({'code': 404, 'msg': '门店不存在'}, 404)
            from .dynamic_database import store_connection_ready
            if not store_connection_ready(store):
                return _response({'code': 409, 'msg': '门店业务库尚未完成初始化'}, 409)
            with store_context(store):
                rows = AdminAuditLog.objects.filter(resource='kook-license-points', method='SYSTEM',
                    request_body__outcome='UNKNOWN', kook_license_review__isnull=kind == 'PENDING'
                ).select_related('kook_license_review').order_by('id')
                paginator = ConsolePagination()
                page = paginator.paginate_queryset(rows, request, view=self)
                data = []
                for row in page:
                    item = {'id': row.pk, 'binding_id': row.object_id, 'evidence': row.request_body,
                            'recorded_at': row.created_at.isoformat()}
                    if kind == 'REVIEWED':
                        review = row.kook_license_review
                        audits = PlatformAuditEvent.objects.using('default').filter(action='license.kook_points_reviewed',
                            target_store_slug=store.slug, target_type='kook_license_review', target_id=f'{store.pk}:{row.pk}')
                        matches = list(audits.values_list('detail', flat=True)[:2])
                        item.update({'review': _audit_detail(review), 'reviewed_at': review.reviewed_at.isoformat(),
                            'audit_status': 'PENDING' if not matches else
                            'RECORDED' if len(matches) == 1 and matches[0] == _audit_detail(review) else 'REVIEW'})
                    data.append(item)
                response = paginator.get_paginated_response(data)
            response['Cache-Control'] = 'no-store'
            return response
        except DatabaseError:
            return _response({'code': 503, 'msg': 'KOOK 授权核对记录暂不可用'}, 503)


class PlatformKookLicenseResolutionView(_PlatformLicenseBase):
    def post(self, request, pk, source_id):
        if not self.allowed(request):
            return self.denied()
        if (not isinstance(request.data, Mapping) or set(request.data) != {'request_id', 'outcome', 'reason'}
                or request.data['outcome'] not in ('ALLOW', 'DENY')
                or not isinstance(request.data['reason'], str)
                or not 1 <= len(request.data['reason'].strip()) <= 500):
            return _response({'code': 400, 'msg': '请填写核对结论、依据及请求编号'}, 400)
        try:
            request_id = UUID(str(request.data['request_id']))
        except (ValueError, TypeError, AttributeError):
            return _response({'code': 400, 'msg': '请求编号无效'}, 400)
        from .db import transaction as tenant_transaction
        from engagement.kook_license_review import resolve_locked
        from engagement.kook_review_lifecycle import capture, locked
        from club_accounts.provider_lifecycle import ProviderLifecycleError
        try:
            with control_transaction.atomic(using='default'):
                actor = _locked_actor(request.auth)
                store = Store.objects.select_for_update().filter(pk=pk).first()
                if store is None:
                    return _response({'code': 404, 'msg': '门店不存在'}, 404)
                from .dynamic_database import store_connection_ready
                if not store_connection_ready(store):
                    return _response({'code': 409, 'msg': '门店业务库尚未完成初始化'}, 409)
                with store_context(store):
                    provider = capture(source_id, request.data['outcome'])
                    with tenant_transaction.atomic(), locked(provider):
                        review = resolve_locked(source_id, request_id=request_id, actor_id=actor.pk,
                            outcome=request.data['outcome'], reason=request.data['reason'].strip())
                        result = {'id': review.pk, 'source_id': source_id, **_audit_detail(review),
                                  'reviewed_at': review.reviewed_at.isoformat()}
                target = f'{store.pk}:{source_id}'
                detail = _audit_detail(review)
                audits = PlatformAuditEvent.objects.filter(action='license.kook_points_reviewed',
                    target_store_slug=store.slug, target_type='kook_license_review', target_id=target)
                previous = audits.first()
                if previous and (audits.count() != 1 or previous.detail != detail):
                    raise ValidationError('平台 KOOK 授权核对审计记录不一致')
                if not previous:
                    PlatformAuditEvent.objects.create(account=actor, action='license.kook_points_reviewed',
                        target_store_slug=store.slug, target_type='kook_license_review', target_id=target, detail=detail)
            return _response({'code': 0, 'data': result})
        except AccountChangeError as exc:
            return _response({'code': exc.status, 'msg': str(exc)}, exc.status)
        except ProviderLifecycleError:
            return _response({'code': 503, 'msg': '陪玩生命周期状态暂不可核验，请使用原请求重试'}, 503)
        except ValidationError as exc:
            return _response({'code': 409, 'msg': str(exc.detail[0])}, 409)
        except DatabaseError:
            return _response({'code': 503, 'msg': '核对结果暂不明确，请使用原请求重试'}, 503)


class PlatformKookManualReviewView(_PlatformLicenseBase):
    def get(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        from engagement.models import KookManualOnlineReview
        from .dynamic_database import store_connection_ready
        try:
            store = Store.objects.filter(pk=pk).first()
            if not store:
                return _response({'code': 404, 'msg': '门店不存在'}, 404)
            if not store_connection_ready(store):
                return _response({'code': 409, 'msg': '门店业务库尚未完成初始化'}, 409)
            kind = request.query_params.get('kind', 'PENDING')
            if kind not in ('PENDING', 'REVIEWED'):
                return _response({'code': 400, 'msg': '请选择待核对或已核对'}, 400)
            with store_context(store):
                rows = KookManualOnlineReview.objects.filter(status='PENDING_REVIEW') if kind == 'PENDING' else KookManualOnlineReview.objects.exclude(status='PENDING_REVIEW')
                paginator = ConsolePagination()
                page = paginator.paginate_queryset(rows.order_by('created_at', 'pk'), request, view=self)
                response = paginator.get_paginated_response([{'id': row.pk, 'account_id': row.account_id,
                    'business_cycle': row.business_cycle, 'source_id': row.source_id,
                    'started_at': row.started_at, 'ended_at': row.ended_at, 'online_seconds': row.online_seconds,
                    'status': row.status, 'reason': row.reason, 'created_at': row.created_at,
                    'resolution_outcome': row.resolution_outcome, 'resolution_points': row.resolution_points,
                    'resolution_reason': row.resolution_reason, 'resolved_at': row.resolved_at} for row in page])
                response['Cache-Control'] = 'no-store'
                return response
        except DatabaseError:
            return _response({'code': 503, 'msg': '门店来源暂不可用，请稍后重试'}, 503)


class PlatformKookManualResolutionView(_PlatformLicenseBase):
    def post(self, request, pk, review_id):
        if not self.allowed(request):
            return self.denied()
        data = request.data
        required = {'request_id', 'outcome', 'reason', 'points'}
        if (not isinstance(data, Mapping) or not required.issubset(data) or set(data) - required - {'allocations'}
                or data['outcome'] not in ('ALLOW', 'DENY') or not isinstance(data['reason'], str)
                or not 1 <= len(data['reason'].strip()) <= 500
                or type(data['points']) is not int or not 0 <= data['points'] <= 1000000):
            return _response({'code': 400, 'msg': '请填写裁决、核对依据、整数差额及请求编号'}, 400)
        allocations = data.get('allocations')
        if allocations is not None and (not isinstance(allocations, list) or any(
                not isinstance(item, Mapping) or set(item) != {'rule_revision', 'points', 'reason'}
                or type(item['rule_revision']) is not int or item['rule_revision'] < 0
                or type(item['points']) is not int or item['points'] <= 0
                or not isinstance(item['reason'], str) or not 1 <= len(item['reason'].strip()) <= 500
                for item in allocations)):
            return _response({'code': 400, 'msg': '逐规则差额或核对依据无效'}, 400)
        try:
            request_id = UUID(str(data['request_id']))
        except (ValueError, TypeError, AttributeError):
            return _response({'code': 400, 'msg': '请求编号无效'}, 400)
        from .db import transaction as tenant_transaction
        from .dynamic_database import store_connection_ready
        from engagement.kook_manual_online import resolve_locked
        try:
            with control_transaction.atomic(using='default'):
                actor = _locked_actor(request.auth)
                store = Store.objects.select_for_update().filter(pk=pk).first()
                if not store:
                    return _response({'code': 404, 'msg': '门店不存在'}, 404)
                if not store_connection_ready(store):
                    return _response({'code': 409, 'msg': '门店业务库尚未完成初始化'}, 409)
                with store_context(store), tenant_transaction.atomic():
                    row = resolve_locked(review_id, request_id=request_id, actor_id=actor.pk,
                        outcome=data['outcome'], reason=data['reason'].strip(), points=data['points'],
                        allocations=allocations)
                    allocations_record = (list(row.points_ledger.kook_rule_allocations.order_by('rule_version_id')
                        .values('rule_version_id', 'points', 'review_reason')) if row.points_ledger_id else [])
                    detail = {'request_id': str(row.resolution_request_id), 'outcome': row.resolution_outcome,
                        'reason': row.resolution_reason, 'points': row.resolution_points,
                        'account_id': row.account_id, 'business_cycle': row.business_cycle,
                        'source_id': row.source_id, 'started_at': row.started_at.isoformat(),
                        'ended_at': row.ended_at.isoformat(), 'online_seconds': row.online_seconds,
                        'original_actor_id': str(row.platform_actor_id), 'ledger_id': row.points_ledger_id,
                        'rule_allocations': allocations_record}
                target = f'{store.pk}:{row.pk}'
                audits = PlatformAuditEvent.objects.filter(action='license.kook_manual_online_reviewed',
                    target_store_slug=store.slug, target_type='kook_manual_online_review', target_id=target)
                previous = audits.first()
                if previous and (audits.count() != 1 or previous.detail != detail):
                    raise ValidationError('平台人工在线核对审计记录不一致')
                if not previous:
                    PlatformAuditEvent.objects.create(account=actor, action='license.kook_manual_online_reviewed',
                        target_store_slug=store.slug, target_type='kook_manual_online_review', target_id=target, detail=detail)
            return _response({'code': 0, 'data': {'id': row.pk, 'status': row.status, **detail}})
        except AccountChangeError as exc:
            return _response({'code': exc.status, 'msg': str(exc)}, exc.status)
        except ValidationError as exc:
            return _response({'code': 409, 'msg': str(exc.detail[0])}, 409)
        except DatabaseError:
            return _response({'code': 503, 'msg': '核对结果暂不明确，请使用原请求重试'}, 503)
