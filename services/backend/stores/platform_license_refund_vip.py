"""Platform review of unresolved refund-order VIP growth decisions."""
from collections.abc import Mapping
from uuid import UUID

from django.db import DatabaseError, transaction as control_transaction
from rest_framework.exceptions import ValidationError
from console.pagination import ConsolePagination
from .context import store_context
from .models import PlatformAccount, PlatformAuditEvent, Store
from .platform_license_views import _PlatformLicenseBase, _response


ACTION = 'license.refund_vip_reviewed'
TARGET = 'license_refund_vip_review'


def _detail(review):
    return {'request_id': str(review.request_id), 'outcome': review.outcome, 'reason': review.reason,
            'review_id': review.pk, 'original_actor_id': str(review.platform_actor_id),
            'vip_applied': review.vip_applied}


class PlatformLicenseRefundVipReviewView(_PlatformLicenseBase):
    def get(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        kind = request.query_params.get('kind', 'PENDING')
        if kind not in ('PENDING', 'REVIEWED'):
            return _response({'code': 400, 'msg': '请选择待核对或已核对记录'}, 400)
        try:
            store = Store.objects.filter(pk=pk).first()
            if not store:
                return _response({'code': 404, 'msg': '门店不存在'}, 404)
            from .dynamic_database import store_connection_ready
            if not store_connection_ready(store):
                return _response({'code': 409, 'msg': '门店业务库尚未完成初始化'}, 409)
            from engagement.models import LicenseRefundVipDecision
            with store_context(store):
                rows = LicenseRefundVipDecision.objects.filter(outcome='UNKNOWN',
                    review__isnull=(kind == 'PENDING')).select_related('order', 'account', 'review')
                rows = rows.order_by('decided_at', 'pk') if kind == 'PENDING' else rows.order_by('-review__reviewed_at', '-pk')
                paginator = ConsolePagination()
                page = paginator.paginate_queryset(rows, request, view=self)
                audits = {}
                if kind == 'REVIEWED':
                    targets = [f'{store.pk}:{row.pk}' for row in page]
                    for audit in PlatformAuditEvent.objects.filter(action=ACTION, target_store_slug=store.slug,
                                                                    target_type=TARGET, target_id__in=targets):
                        audits.setdefault(audit.target_id, []).append(audit)
                data = []
                for row in page:
                    item = {'id': row.pk, 'order_id': row.order_id, 'order_no': row.order.order_no,
                            'account_id': row.account_id, 'account_no': row.account.account_no,
                            'paid_units': row.paid_units, 'refunded_units': row.refunded_units,
                            'payment_vip_enabled': row.payment_vip_enabled,
                            'confirmation_vip_enabled': row.confirmation_vip_enabled,
                            'decided_at': row.decided_at.isoformat(),
                            'reason': row.license_evidence.get('status', 'UNKNOWN')}
                    if kind == 'REVIEWED':
                        review = row.review
                        matching = audits.get(f'{store.pk}:{row.pk}', [])
                        status = ('AUDIT_PENDING' if not matching else
                                  'REVIEWED' if len(matching) == 1 and matching[0].detail == _detail(review)
                                  else 'AUDIT_REVIEW')
                        item.update(status=status, request_id=str(review.request_id), outcome=review.outcome,
                            review_reason=review.reason, original_actor_id=str(review.platform_actor_id),
                            vip_applied=review.vip_applied, vip_recalculation_pending=review.vip_recalculation_pending,
                            reviewed_at=review.reviewed_at.isoformat())
                    data.append(item)
                response = paginator.get_paginated_response(data)
            response['Cache-Control'] = 'no-store'
            return response
        except DatabaseError:
            return _response({'code': 503, 'msg': '待核对会员成长记录暂不可用'}, 503)


class PlatformLicenseRefundVipResolutionView(_PlatformLicenseBase):
    def post(self, request, pk, decision_id):
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
        from .platform_accounts import AccountChangeError, _locked_actor
        from .db import transaction as tenant_transaction
        from engagement.license_refund_vip_review import resolve_locked
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
                    review = resolve_locked(decision_id, request_id=request_id, actor_id=actor.pk,
                                            outcome=data['outcome'], reason=data['reason'].strip())
                    result = {'id': review.pk, 'decision_id': review.decision_id,
                              'order_id': review.decision.order_id, 'outcome': review.outcome,
                              'vip_applied': review.vip_applied,
                              'vip_recalculation_pending': review.vip_recalculation_pending,
                              'reviewed_at': review.reviewed_at.isoformat()}
                target = f'{store.pk}:{decision_id}'
                audits = PlatformAuditEvent.objects.filter(action=ACTION, target_store_slug=store.slug,
                                                            target_type=TARGET, target_id=target)
                previous, detail = audits.first(), _detail(review)
                if previous and (audits.count() != 1 or previous.detail != detail
                                 or str(previous.account_id) != str(review.platform_actor_id)):
                    raise ValidationError('平台会员成长核对审计记录不一致')
                if not previous:
                    original = PlatformAccount.objects.filter(pk=review.platform_actor_id).first()
                    if not original:
                        raise ValidationError('原平台操作人不可核验')
                    PlatformAuditEvent.objects.create(account=original, action=ACTION, target_store_slug=store.slug,
                        target_type=TARGET, target_id=target, detail=detail)
            return _response({'code': 0, 'data': result})
        except AccountChangeError as exc:
            return _response({'code': exc.status, 'msg': str(exc)}, exc.status)
        except ValidationError as exc:
            return _response({'code': 409, 'msg': str(exc.detail[0])}, 409)
        except DatabaseError:
            return _response({'code': 503, 'msg': '核对结果暂不明确，请使用原请求重试'}, 503)
