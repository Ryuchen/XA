"""Platform-only reconciliation of uncertain achievement authorizations."""
from collections.abc import Mapping
from uuid import UUID
from django.db import DatabaseError, transaction as control_transaction
from rest_framework.exceptions import ValidationError
from console.pagination import ConsolePagination
from .context import store_context
from .models import PlatformAccount, PlatformAuditEvent, Store
from .platform_accounts import AccountChangeError, _locked_actor
from .platform_license_views import _PlatformLicenseBase, _response

ACTION = 'license.achievement_reviewed'
TARGET = 'license_achievement_review'


def _detail(review):
    return {'request_id': str(review.request_id), 'outcome': review.outcome, 'reason': review.reason,
            'review_id': review.pk, 'original_actor_id': str(review.platform_actor_id),
            'granted_ids': review.granted_ids}


class PlatformAchievementReviewView(_PlatformLicenseBase):
    def get(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        kind = request.query_params.get('kind', 'PENDING')
        if kind not in ('PENDING', 'REVIEWED'):
            return _response({'code': 400, 'msg': '请选择待核对或已核对'}, 400)
        try:
            store = Store.objects.filter(pk=pk).first()
            if not store:
                return _response({'code': 404, 'msg': '门店不存在'}, 404)
            from .dynamic_database import store_connection_ready
            if not store_connection_ready(store):
                return _response({'code': 409, 'msg': '门店业务库尚未完成初始化'}, 409)
            from engagement.models import AchievementLicenseEvent
            with store_context(store):
                rows = AchievementLicenseEvent.objects.filter(outcome='UNKNOWN',
                    review__isnull=kind == 'PENDING').select_related('order', 'account', 'review').order_by('decided_at', 'pk')
                paginator = ConsolePagination()
                page = paginator.paginate_queryset(rows, request, view=self)
                data = []
                for row in page:
                    item = {'id': row.pk, 'event_key': row.event_key, 'order_id': row.order_id,
                        'order_no': row.order.order_no, 'account_id': row.account_id,
                        'business_cycle': row.business_cycle, 'decided_at': row.decided_at.isoformat(),
                        'license_evidence': row.license_evidence, 'metrics': row.metrics, 'eligible': row.eligible}
                    if kind == 'REVIEWED':
                        detail = _detail(row.review)
                        audits = list(PlatformAuditEvent.objects.filter(action=ACTION, target_store_slug=store.slug,
                            target_type=TARGET, target_id=f'{store.pk}:{row.pk}').values_list('detail', flat=True)[:2])
                        item.update(review=detail, audit_status='PENDING' if not audits else
                            'RECORDED' if len(audits) == 1 and audits[0] == detail else 'REVIEW')
                    data.append(item)
                response = paginator.get_paginated_response(data)
            response['Cache-Control'] = 'no-store'
            return response
        except DatabaseError:
            return _response({'code': 503, 'msg': '成就授权待核对记录暂不可用'}, 503)


class PlatformAchievementResolutionView(_PlatformLicenseBase):
    def post(self, request, pk, event_id):
        if not self.allowed(request):
            return self.denied()
        data = request.data
        if (not isinstance(data, Mapping) or set(data) != {'request_id', 'outcome', 'reason'}
                or data['outcome'] not in ('ALLOW', 'DENY') or not isinstance(data['reason'], str)
                or not 1 <= len(data['reason'].strip()) <= 500):
            return _response({'code': 400, 'msg': '请填写核对结论、依据及请求编号'}, 400)
        try:
            request_id = UUID(str(data['request_id']))
        except (ValueError, TypeError, AttributeError):
            return _response({'code': 400, 'msg': '请求编号无效'}, 400)
        from .db import transaction as tenant_transaction
        from engagement.license_achievement_review import resolve_locked
        try:
            with control_transaction.atomic(using='default'):
                actor = _locked_actor(request.auth)
                store = Store.objects.select_for_update().filter(pk=pk).first()
                if not store:
                    return _response({'code': 404, 'msg': '门店不存在'}, 404)
                from .dynamic_database import store_connection_ready
                if not store_connection_ready(store):
                    return _response({'code': 409, 'msg': '门店业务库尚未完成初始化'}, 409)
                with store_context(store), tenant_transaction.atomic():
                    review = resolve_locked(event_id, request_id=request_id, actor_id=actor.pk,
                        outcome=data['outcome'], reason=data['reason'].strip())
                    result = {'id': review.pk, 'event_id': event_id, **_detail(review)}
                audits = PlatformAuditEvent.objects.filter(action=ACTION, target_store_slug=store.slug,
                    target_type=TARGET, target_id=f'{store.pk}:{event_id}')
                previous, detail = audits.first(), _detail(review)
                if previous and (audits.count() != 1 or previous.detail != detail
                                 or str(previous.account_id) != str(review.platform_actor_id)):
                    raise ValidationError('平台成就审核审计记录不一致')
                if not previous:
                    original = PlatformAccount.objects.filter(pk=review.platform_actor_id).first()
                    if not original:
                        raise ValidationError('原平台操作人不可核验')
                    PlatformAuditEvent.objects.create(account=original, action=ACTION, target_store_slug=store.slug,
                        target_type=TARGET, target_id=f'{store.pk}:{event_id}', detail=detail)
            return _response({'code': 0, 'data': result})
        except AccountChangeError as exc:
            return _response({'code': exc.status, 'msg': str(exc)}, exc.status)
        except ValidationError as exc:
            return _response({'code': 409, 'msg': str(exc.detail[0])}, 409)
        except DatabaseError:
            return _response({'code': 503, 'msg': '核对结果暂不明确，请使用原请求重试'}, 503)
