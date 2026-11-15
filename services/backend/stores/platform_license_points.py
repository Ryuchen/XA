"""Platform inspection of consumption-point decisions needing evidence."""
from django.conf import settings
from collections.abc import Mapping
from uuid import UUID

from django.db import DatabaseError, transaction as control_transaction
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from console.pagination import ConsolePagination
from engagement.models import LicenseConsumptionPointsDecision
from .context import store_context
from .models import Store
from .platform_license_views import _PlatformLicenseBase, _response


def _addon_decisions(row):
    result = {}
    for name in ('vip', 'points'):
        decision = row.license_decisions.get(name, {})
        evidence = decision.get('evidence', {})
        result[name] = {'outcome': decision.get('outcome'), 'applicable': decision.get('applicable', False),
                        'reason': evidence.get('status', 'UNKNOWN')}
        if name == 'points':
            result[name]['points_at_decision_hundredths'] = decision.get('points_at_decision_hundredths')
            result[name]['refunded_principal_at_decision'] = decision.get('refunded_principal_at_decision')
    return result


class PlatformLicensePointsReviewView(_PlatformLicenseBase):
    def get(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        kind = request.query_params.get('kind', 'ORDER')
        if kind not in ('ORDER', 'ORDER_REVIEWED', 'ADDON', 'ADDON_REVIEWED'):
            return _response({'code': 400, 'msg': '请选择订单或加购核对记录'}, 400)
        try:
            store = Store.objects.filter(pk=pk).first()
            if store is None:
                return _response({'code': 404, 'msg': '门店不存在'}, 404)
            from .dynamic_database import store_connection_ready
            if not store_connection_ready(store):
                return _response({'code': 409, 'msg': '门店业务库尚未完成初始化'}, 409)
            with store_context(store):
                if kind == 'ADDON':
                    from orders.models import OrderAddonConsumption
                    rows = OrderAddonConsumption.objects.filter(license_review__isnull=True).filter(
                        Q(license_decisions__vip__applicable=True, license_decisions__vip__outcome='UNKNOWN') |
                        Q(license_decisions__points__applicable=True, license_decisions__points__outcome='UNKNOWN')
                    ).select_related('payment__intent__order', 'account').order_by('confirmed_at', 'pk')
                elif kind == 'ADDON_REVIEWED':
                    from orders.models import OrderAddonConsumption
                    rows = OrderAddonConsumption.objects.filter(license_review__isnull=False).select_related(
                        'payment__intent__order', 'account', 'license_review').order_by('-license_review__reviewed_at', '-pk')
                elif kind == 'ORDER':
                    rows = LicenseConsumptionPointsDecision.objects.filter(outcome='UNKNOWN', review__isnull=True).select_related(
                        'order', 'account').order_by('decided_at', 'pk')
                else:
                    rows = LicenseConsumptionPointsDecision.objects.filter(outcome='UNKNOWN', review__isnull=False).select_related(
                        'order', 'account', 'review').order_by('-review__reviewed_at', '-pk')
                paginator = ConsolePagination()
                page = paginator.paginate_queryset(rows, request, view=self)
                if kind == 'ADDON':
                    data = [{'id': row.pk, 'payment_id': row.payment_id,
                             'order_id': row.payment.intent.order_id, 'order_no': row.payment.intent.order.order_no,
                             'account_id': row.account_id, 'account_no': row.account.account_no,
                             'decided_at': row.confirmed_at.isoformat(), 'decisions': _addon_decisions(row),
                             'principal_units': row.principal_units,
                             'refunded_principal_units_current': row.refunded_principal_units}
                            for row in page]
                elif kind == 'ADDON_REVIEWED':
                    from .models import PlatformAuditEvent
                    audits = {}
                    for audit in PlatformAuditEvent.objects.using('default').filter(
                        action='license.addon_benefits_reviewed', target_store_slug=store.slug,
                        target_type='license_addon_review', target_id__in=[f'{store.pk}:{row.pk}' for row in page]):
                        audits.setdefault(audit.target_id, []).append(audit)
                    data = []
                    for row in page:
                        review = row.license_review
                        detail = _addon_audit_detail(review)
                        matching = audits.get(f'{store.pk}:{row.pk}', [])
                        state = ('AUDIT_PENDING' if not matching else
                                 'REVIEWED' if len(matching) == 1 and matching[0].detail == detail else 'AUDIT_REVIEW')
                        data.append({'id': row.pk, 'payment_id': row.payment_id, 'order_id': row.payment.intent.order_id,
                            'account_id': row.account_id, 'status': state, 'request_id': str(review.request_id),
                            'vip_outcome': review.vip_outcome or None, 'points_outcome': review.points_outcome or None,
                            'reason': review.reason, 'original_actor_id': str(review.platform_actor_id),
                            'vip_applied': review.vip_applied, 'vip_recalculation_pending': review.vip_recalculation_pending,
                            'awarded_points_hundredths': review.awarded_points, 'skip_reason': review.skip_reason,
                            'reviewed_at': review.reviewed_at.isoformat()})
                elif kind == 'ORDER':
                    data = [{'id': row.pk, 'order_id': row.order_id, 'order_no': row.order.order_no,
                             'account_id': row.account_id, 'account_no': row.account.account_no,
                             'points_at_decision_hundredths': row.points_amount, 'decided_at': row.decided_at.isoformat(),
                             'reason': row.license_evidence.get('status', 'UNKNOWN')}
                            for row in page]
                else:
                    from .models import PlatformAuditEvent
                    audits = {}
                    for audit in PlatformAuditEvent.objects.using('default').filter(
                        action='license.points_reviewed', target_store_slug=store.slug,
                        target_type='license_points_review', target_id__in=[f'{store.pk}:{row.pk}' for row in page]):
                        audits.setdefault(audit.target_id, []).append(audit)
                    data = []
                    for row in page:
                        review = row.review
                        detail = {'request_id': str(review.request_id), 'outcome': review.outcome,
                                  'reason': review.reason, 'awarded_points_hundredths': review.awarded_points,
                                  'review_id': review.pk, 'original_actor_id': str(review.platform_actor_id)}
                        matching = audits.get(f'{store.pk}:{row.pk}', [])
                        state = ('AUDIT_PENDING' if not matching else
                                 'REVIEWED' if len(matching) == 1 and matching[0].detail == detail else 'AUDIT_REVIEW')
                        data.append({'id': row.pk, 'order_id': row.order_id, 'order_no': row.order.order_no,
                            'account_id': row.account_id, 'status': state, 'request_id': str(review.request_id),
                            'outcome': review.outcome, 'reason': review.reason,
                            'original_actor_id': str(review.platform_actor_id),
                            'awarded_points_hundredths': review.awarded_points,
                            'reviewed_at': review.reviewed_at.isoformat()})
                response = paginator.get_paginated_response(data)
            response['Cache-Control'] = 'no-store'
            return response
        except DatabaseError:
            return _response({'code': 503, 'msg': '待核对积分记录暂不可用'}, 503)


def _addon_audit_detail(review):
    return {'request_id': str(review.request_id), 'vip_outcome': review.vip_outcome,
            'points_outcome': review.points_outcome, 'reason': review.reason, 'review_id': review.pk,
            'vip_applied': review.vip_applied, 'awarded_points_hundredths': review.awarded_points,
            'original_actor_id': str(review.platform_actor_id)}


class PlatformLicenseAddonResolutionView(_PlatformLicenseBase):
    def post(self, request, pk, consumption_id):
        if not self.allowed(request):
            return self.denied()
        fields = {'request_id', 'vip_outcome', 'points_outcome', 'reason'}
        if (not isinstance(request.data, Mapping) or set(request.data) != fields
                or request.data['vip_outcome'] not in (None, 'ALLOW', 'DENY')
                or request.data['points_outcome'] not in (None, 'ALLOW', 'DENY')
                or not isinstance(request.data['reason'], str)
                or not 1 <= len(request.data['reason'].strip()) <= 500):
            return _response({'code': 400, 'msg': '请填写加购授权核对结论、依据及请求编号'}, 400)
        try:
            request_id = UUID(str(request.data['request_id']))
        except (ValueError, TypeError, AttributeError):
            return _response({'code': 400, 'msg': '请求编号无效'}, 400)
        from .platform_accounts import AccountChangeError, _locked_actor
        from .models import PlatformAuditEvent
        from .db import transaction as tenant_transaction
        from engagement.license_addon_review import resolve_locked
        try:
            with control_transaction.atomic(using='default'):
                actor = _locked_actor(request.auth)
                store = Store.objects.select_for_update().filter(pk=pk).first()
                if store is None:
                    return _response({'code': 404, 'msg': '门店不存在'}, 404)
                from .dynamic_database import store_connection_ready
                if not store_connection_ready(store):
                    return _response({'code': 409, 'msg': '门店业务库尚未完成初始化'}, 409)
                with store_context(store), tenant_transaction.atomic():
                    review = resolve_locked(consumption_id, request_id=request_id, actor_id=actor.pk,
                        vip_outcome=request.data['vip_outcome'], points_outcome=request.data['points_outcome'],
                        reason=request.data['reason'].strip())
                    result = {'id': review.pk, 'consumption_id': review.consumption_id,
                        'vip_outcome': review.vip_outcome or None, 'points_outcome': review.points_outcome or None,
                        'vip_applied': review.vip_applied, 'vip_recalculation_pending': review.vip_recalculation_pending,
                        'awarded_points_hundredths': review.awarded_points, 'skip_reason': review.skip_reason,
                        'reviewed_at': review.reviewed_at.isoformat()}
                audits = PlatformAuditEvent.objects.filter(action='license.addon_benefits_reviewed',
                    target_store_slug=store.slug, target_type='license_addon_review', target_id=f'{store.pk}:{consumption_id}')
                previous, detail = audits.first(), _addon_audit_detail(review)
                if previous and (audits.count() != 1 or previous.detail != detail):
                    raise ValidationError('平台加购权益核对审计不一致')
                if not previous:
                    PlatformAuditEvent.objects.create(account=actor, action='license.addon_benefits_reviewed',
                        target_store_slug=store.slug, target_type='license_addon_review',
                        target_id=f'{store.pk}:{consumption_id}', detail=detail)
            return _response({'code': 0, 'data': result})
        except AccountChangeError as exc:
            return _response({'code': exc.status, 'msg': str(exc)}, exc.status)
        except ValidationError as exc:
            return _response({'code': 409, 'msg': str(exc.detail[0])}, 409)
        except DatabaseError:
            return _response({'code': 503, 'msg': '核对结果暂不明确，请使用原请求重试'}, 503)


class PlatformLicensePointsResolutionView(_PlatformLicenseBase):
    """Platform super administrator records the evidence-based historical decision."""

    def post(self, request, pk, decision_id):
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
        from .platform_accounts import AccountChangeError, _locked_actor
        from .models import PlatformAuditEvent
        from .db import transaction as tenant_transaction
        from engagement.license_points_review import resolve_locked
        try:
            with control_transaction.atomic(using='default'):
                actor = _locked_actor(request.auth)
                store = Store.objects.select_for_update().filter(pk=pk).first()
                if store is None:
                    return _response({'code': 404, 'msg': '门店不存在'}, 404)
                from .dynamic_database import store_connection_ready
                if not store_connection_ready(store):
                    return _response({'code': 409, 'msg': '门店业务库尚未完成初始化'}, 409)
                with store_context(store), tenant_transaction.atomic():
                    review = resolve_locked(decision_id, request_id=request_id, actor_id=actor.pk,
                        outcome=request.data['outcome'], reason=request.data['reason'].strip())
                    result = {'id': review.pk, 'decision_id': review.decision_id, 'order_id': review.decision.order_id,
                              'outcome': review.outcome, 'awarded_points_hundredths': review.awarded_points,
                              'skip_reason': review.skip_reason, 'reviewed_at': review.reviewed_at.isoformat()}
                target = f'{store.pk}:{decision_id}'
                detail = {'request_id': str(request_id), 'outcome': review.outcome, 'reason': review.reason,
                          'awarded_points_hundredths': review.awarded_points, 'review_id': review.pk,
                          'original_actor_id': str(review.platform_actor_id)}
                audits = PlatformAuditEvent.objects.filter(action='license.points_reviewed',
                    target_store_slug=store.slug, target_type='license_points_review', target_id=target)
                previous = audits.first()
                if previous and (audits.count() != 1 or previous.detail != detail):
                    raise ValidationError('平台积分核对审计记录不一致')
                if not previous:
                    PlatformAuditEvent.objects.create(account=actor, action='license.points_reviewed',
                        target_store_slug=store.slug, target_type='license_points_review', target_id=target, detail=detail)
            return _response({'code': 0, 'data': result})
        except AccountChangeError as exc:
            return _response({'code': exc.status, 'msg': str(exc)}, exc.status)
        except ValidationError as exc:
            return _response({'code': 409, 'msg': str(exc.detail[0])}, 409)
        except DatabaseError:
            return _response({'code': 503, 'msg': '核对结果暂不明确，请使用原请求重试'}, 503)
