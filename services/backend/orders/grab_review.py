"""Store staff review of unresolved grab receipts; no timestamps are edited."""
from contextlib import ExitStack
from django.db.models import F, Q
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.permissions import IsClubAccountAuthenticated
from console.permissions import get_account_permissions, is_console_account
from stores.db import transaction
from .grab_round_facts import GrabRoundReviewRequired, current_round, latest_round_state, latest_status_for_pool, pool_exists
from .models import (Order, OrderGrabPoolOpeningIntent, OrderGrabReceiptProbe,
                     OrderGrabReviewCancellation, OrderGrabRound, OrderGrabRoundOpeningEvidence)

MISSING_ROUND_REASON = '当前抢单轮次状态或人数待核对'


def mark_round_review(state, reason):
    """Persist the real pre-review phase with the existing ranking evidence."""
    if state.status not in ('OPEN', 'PARKED') or not isinstance(state.ranking_snapshot, dict):
        raise ValueError('抢单核对阶段或排序证据异常')
    state.ranking_snapshot = {**state.ranking_snapshot, 'review_phase': state.status}
    state.status, state.reason = 'REVIEW', str(reason)[:255]
    state.save(update_fields=['status', 'reason', 'ranking_snapshot', 'updated_at'])


def report_invalid_round(order, error):
    state = error.state
    if (order.status == Order.Status.PENDING and state is not None
            and state.status in ('OPEN', 'PARKED') and isinstance(state.ranking_snapshot, dict)):
        mark_round_review(state, str(error))


def _allowed(account, *permissions):
    return is_console_account(account) and set(permissions).issubset(get_account_permissions(account))


def _state(order, *, lock=False):
    return latest_round_state(order.pk, seats=order.required_provider_count, lock=lock)


def _customer_cancellation(order):
    row = OrderGrabReviewCancellation.objects.filter(order=order).first()
    return {'status': row.status, 'reason': row.reason, 'created_at': row.created_at} if row else None


def _missing_round_review(order):
    round_ = OrderGrabRound.objects.filter(pool__order=order).order_by('-number').values('pk', 'number').first()
    return {'order_id': order.pk, 'round_id': round_['pk'] if round_ else None,
            'round_number': round_['number'] if round_ else None, 'status': 'REVIEW', 'reason': MISSING_ROUND_REASON}


def _receipt(row):
    evidence = getattr(row, 'pass_evidence', None)
    admission = getattr(row, 'single_admission', None)
    return {'id': row.pk, 'actor_id': row.actor_id, 'kind': row.kind, 'phase': row.round_phase,
            'request_id': row.request_key, 'payload': row.payload, 'state': row.state, 'reason': row.review_reason,
            'lower_at': row.lower_at, 'upper_at': row.upper_observed_at, 'cutoff_at': row.cutoff_at,
            'precision_us': row.precision_us, 'clock_evidence': row.clock_evidence,
            'pass_evidence': ({'state': evidence.state, 'submitted_tier': evidence.submitted_tier,
                               'reason': evidence.review_reason} if evidence else None),
            'admission': ({'status': admission.status, 'reason': admission.disqualification_reason}
                          if admission else None)}


class GrabReviewView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request, order_id=None):
        if not _allowed(request.account, 'order:view'):
            return Response({'code': 403, 'msg': '无权查看抢单核对'}, status=403)
        if order_id is not None:
            order = Order.objects.filter(pk=order_id).first()
            state = _state(order) if order else None
            if not state:
                if order and pool_exists(order.pk):
                    return Response({'code': 0, 'data': {**_missing_round_review(order), 'receipts': [],
                        'customer_cancellation': _customer_cancellation(order)}})
                opening = OrderGrabPoolOpeningIntent.objects.filter(order=order).first() if order else None
                if not opening:
                    return Response({'code': 404, 'msg': '抢单订单不存在'}, status=404)
                return Response({'code': 0, 'data': {'order_id': order.pk, 'status': 'OPENING_' + opening.state,
                    'reason': opening.reason, 'payment_observed_at': opening.commit_observed_at,
                    'database_identity': opening.database_identity, 'receipts': [],
                    'customer_cancellation': _customer_cancellation(order)}})
            rows = OrderGrabReceiptProbe.objects.filter(round=state.round).order_by('pk')
            teams = state.round.team_drafts.order_by('pk') if order.required_provider_count == 2 else []
            status, reason = state.status, state.reason
            if order.status == Order.Status.PENDING:
                try:
                    current_round(state.round.pool, state.round_id)
                except ValueError as exc:
                    status, reason = 'REVIEW', str(exc)
            evidence = OrderGrabRoundOpeningEvidence.objects.filter(round=state.round).first()
            from .grab_clock_contract import production_enabled
            from .grab_round_opening import cutoff_bounds
            deadline_lower = deadline_upper = total_lower = total_upper = None
            if production_enabled():
                try:
                    deadline_lower, deadline_upper = cutoff_bounds(state.round, 'OPEN')
                    total_lower, total_upper = cutoff_bounds(state.round, 'PARKED')
                except ValueError:
                    pass
            return Response({'code': 0, 'data': {'order_id': order.pk, 'round_id': state.round_id,
                'round_number': state.round.number, 'status': status, 'reason': reason,
                'ranking_snapshot': state.ranking_snapshot,
                'deadline': None if production_enabled() else state.round.deadline,
                'total_deadline': None if production_enabled() else state.round.total_deadline,
                'deadline_lower_at': deadline_lower, 'deadline_upper_at': deadline_upper,
                'total_deadline_lower_at': total_lower, 'total_deadline_upper_at': total_upper,
                'round_opening': ({'state': evidence.state, 'reason': evidence.reason,
                    'activation_lower_at': evidence.activation_lower_at,
                    'activation_upper_at': evidence.activation_upper_at} if evidence else None),
                'customer_cancellation': _customer_cancellation(order),
                'receipts': [_receipt(row) for row in rows],
                'teams': [{'id': team.pk, 'captain_id': team.captain_id, 'partner_id': team.partner_id,
                           'status': team.status, 'reason': team.disqualification_reason} for team in teams]}})
        query = Order.objects.filter(status=Order.Status.PENDING).alias(
            latest_single_status=latest_status_for_pool(1), latest_team_status=latest_status_for_pool(2),
        ).filter(
            Q(grab_pool_snapshot__rounds__single_state__status='REVIEW')
            | Q(grab_pool_snapshot__rounds__team_state__status='REVIEW')
            | Q(grab_pool_snapshot__required_provider_count=1, latest_single_status__isnull=True)
            | Q(grab_pool_snapshot__required_provider_count=2, latest_team_status__isnull=True)
            | (Q(grab_pool_snapshot__isnull=False) & ~Q(grab_pool_snapshot__required_provider_count=F('required_provider_count')))
            | Q(grab_pool_snapshot__rounds__receipt_probes__state__in=['PENDING', 'WAIT_OPENING', 'REVIEW'])
            | Q(grab_pool_snapshot__rounds__opening_evidence__state__in=['PENDING', 'PREPARED', 'REVIEW'])
            | Q(grab_review_cancellation__status='PENDING')
            | Q(grab_opening_intent__state='REVIEW', grab_pool_snapshot__isnull=True)).distinct().order_by('pk')[:100]
        result = []
        for order in query:
            state = _state(order)
            if (state and state.status in ('OPEN', 'PARKED', 'REVIEW')
                    and (state.status == 'REVIEW'
                         or state.round.receipt_probes.filter(state__in=['PENDING', 'WAIT_OPENING', 'REVIEW']).exists()
                         or OrderGrabRoundOpeningEvidence.objects.filter(round=state.round,
                             state__in=['PENDING', 'PREPARED', 'REVIEW']).exists()
                         or OrderGrabReviewCancellation.objects.filter(order=order, status='PENDING').exists())):
                result.append({'order_id': order.pk, 'round_id': state.round_id,
                               'status': state.status, 'reason': state.reason})
            elif not state:
                if pool_exists(order.pk):
                    result.append(_missing_round_review(order))
                    continue
                opening = OrderGrabPoolOpeningIntent.objects.filter(order=order).first()
                if opening and (opening.state == 'REVIEW' or OrderGrabReviewCancellation.objects.filter(
                        order=order, status='PENDING').exists()):
                    result.append({'order_id': order.pk, 'round_id': None,
                                   'status': 'OPENING_REVIEW', 'reason': opening.reason or '老板已申请取消，等待客服核对'})
        return Response({'code': 0, 'data': result})


class GrabReviewActionView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request, order_id):
        if not _allowed(request.account, 'order:dispatch'):
            return Response({'code': 403, 'msg': '无权处理抢单核对'}, status=403)
        data = request.data
        if (not isinstance(data, dict) or set(data) != {'action', 'reason', 'client_request_id'}
                or data['action'] not in ('RESUME', 'CANCEL')
                or any(not isinstance(data[key], str) or data[key] != data[key].strip()
                       or not data[key] or len(data[key]) > limit
                       for key, limit in (('reason', 255), ('client_request_id', 64)))):
            return Response({'code': 400, 'msg': '请填写处理方式、原因和请求编号'}, status=400)
        if data['action'] == 'CANCEL' and not _allowed(request.account, 'order:refund'):
            return Response({'code': 403, 'msg': '无权执行订单退款'}, status=403)
        from console.models import AdminAuditLog
        from .financial_lock import lock_order_money
        from .grab_receipt_probe import _effective_state
        from .points_service_payment import needs_integrated_processing
        with transaction.atomic(), ExitStack() as stack:
            if data['action'] == 'CANCEL':
                from django.conf import settings
                if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                    from console.payroll_event_barrier import lock_payment_refund_events
                    lock_payment_refund_events()
            order = Order.objects.select_for_update().filter(pk=order_id).first()
            if not order:
                return Response({'code': 404, 'msg': '抢单订单不存在'}, status=404)
            audit_rows = AdminAuditLog.objects.filter(resource='grab-review', object_id=str(order.pk)).order_by('-pk')
            for audit in audit_rows:
                body = audit.request_body
                if isinstance(body, dict) and body.get('client_request_id') == data['client_request_id']:
                    if audit.operator_account_id != request.account.pk or body != dict(data):
                        return Response({'code': 409, 'msg': '请求编号已用于其他处理'}, status=409)
                    if data['action'] == 'CANCEL' and needs_integrated_processing(order):
                        from django.core.exceptions import ValidationError as DjangoValidationError
                        from rest_framework.exceptions import ValidationError
                        from .points_prestart_cancel_funding import verify_replay_locked
                        from .points_service_refund import verify_refund_history_locked
                        refunds = list(order.refund_records.order_by('pk')[:2])
                        if len(refunds) != 1:
                            return Response({'code': 409, 'msg': '积分退款结果待核对'}, status=409)
                        try:
                            verify_replay_locked(refunds[0])
                            verify_refund_history_locked(refunds[0].pk)
                        except (ValidationError, DjangoValidationError):
                            return Response({'code': 409, 'msg': '积分退款或到账释放待核对'}, status=409)
                    return Response({'code': 0, 'data': {'order_id': order.pk, 'action': data['action']}})
            if data['action'] == 'CANCEL':
                from .applications import ApplicationError, _enter_assignment_scope
                try:
                    _enter_assignment_scope(stack, order)
                except ApplicationError:
                    return Response({'code': 409, 'msg': '积分订单资金凭证待核对，暂不能取消'}, status=409)
            try:
                state = _state(order, lock=True)
            except GrabRoundReviewRequired:
                return Response({'code': 409, 'errorCode': 'GRAB_ROUND_REVIEW', 'msg': MISSING_ROUND_REASON}, status=409)
            opening = (OrderGrabPoolOpeningIntent.objects.select_for_update().filter(order=order).first()
                       if not state else None)
            if (order.status != Order.Status.PENDING
                    or (state and state.status not in ('OPEN', 'PARKED', 'REVIEW'))
                    or (not state and not opening)):
                return Response({'code': 409, 'msg': '订单状态已变化，请刷新核对'}, status=409)
            lock_order_money()
            rows = list(OrderGrabReceiptProbe.objects.select_for_update().filter(round=state.round).order_by('pk')) if state else []
            if data['action'] == 'RESUME':
                if OrderGrabReviewCancellation.objects.filter(order=order, status='PENDING').exists():
                    return Response({'code': 409, 'msg': '老板已申请取消，请先处理退款'}, status=409)
                if not state:
                    if opening.state == 'REVIEW':
                        return Response({'code': 409, 'msg': '开池时间或数据库身份待核对，不能直接恢复'}, status=409)
                    from django.conf import settings
                    if not getattr(settings, 'GRAB_COMMITTED_OPENING_ENABLED', False):
                        return Response({'code': 409, 'msg': '付款后开池尚未开放，暂不能恢复'}, status=409)
                    from .tasks import open_committed_grab_order
                    transaction.on_commit(lambda: open_committed_grab_order.delay(order.pk), robust=True)
                else:
                    from .grab_clock_contract import production_enabled
                    from .grab_round_opening import opening_bounds
                    if production_enabled():
                        try:
                            opening_bounds(state.round)
                        except ValueError:
                            return Response({'code': 409, 'msg': '开池提交证据待核对，不能直接恢复'}, status=409)
                    snapshot = state.ranking_snapshot
                    phase = (snapshot.get('review_phase') if state.status == 'REVIEW' and isinstance(snapshot, dict)
                             else state.status)
                    if phase not in ('OPEN', 'PARKED'):
                        return Response({'code': 409, 'msg': '抢单核对前阶段缺少真实记录，暂不能恢复'}, status=409)
                    # Match the resolver's actual candidate scope. A parked
                    # candidate can be recorded after OPEN is sealed, without
                    # becoming part of the original first-window decision.
                    relevant = [row for row in rows if row.round_phase == phase
                        or (phase == 'OPEN' and row.round_phase == 'PARKED'
                            and row.kind in ('BOSS_CANCEL', 'WITHDRAW'))]
                    if any(_effective_state(row, getattr(row, 'pass_evidence', None)) in ('PENDING', 'REVIEW')
                           for row in relevant):
                        return Response({'code': 409, 'msg': '仍有登记时间或身份无法核实，请继续人工处理'}, status=409)
                    from .grab_single_flow import enabled as single_enabled, enqueue as single_enqueue
                    from .grab_team_flow import enabled as team_enabled, enqueue as team_enqueue
                    team = order.required_provider_count == 2
                    if not (team_enabled() if team else single_enabled()):
                        return Response({'code': 409, 'msg': '抢单仲裁尚未开放，暂不能恢复'}, status=409)
                    if state.status == 'REVIEW':
                        state.status = phase
                        state.reason = ''
                        state.save(update_fields=['status', 'reason', 'updated_at'])
                        if team:
                            from site_messages.grab_invite import schedule
                            for row in rows:
                                if row.kind == 'INVITE' and row.state == 'BEFORE':
                                    schedule(row.pk)
                    transaction.on_commit(lambda: (team_enqueue if team else single_enqueue)(order.pk), robust=True)
            else:
                from .services import refund_to_customer
                from .state_machine import transition
                from .models import OrderStatusLog
                from .settlement import SettlementError
                from rest_framework.exceptions import ValidationError
                refund_result = None

                def refund(locked):
                    nonlocal refund_result
                    if needs_integrated_processing(locked):
                        from .points_service_timeout import refund_staff_review_locked
                        refund_result = refund_staff_review_locked(
                            locked, operator=request.legacy_user, operator_account=request.account,
                            reason=data['reason'], grab_single_state=state if order.required_provider_count == 1 else None,
                            grab_team_state=state if order.required_provider_count == 2 else None,
                            opening_intent=opening if not state else None)
                    else:
                        refund_result = refund_to_customer(locked, reason=data['reason'],
                            operator=request.legacy_user, operator_account=request.account)
                    locked.cancel_reason = data['reason']
                try:
                    with transaction.atomic():
                        order = transition(order.pk, Order.Status.CANCELLED, operator=request.legacy_user,
                            operator_account=request.account, action=OrderStatusLog.Action.CANCEL,
                            reason=data['reason'], side_effect=refund,
                            update_fields=['cancel_reason', 'payment_status', 'refunded_at'])
                except (SettlementError, ValidationError):
                    return Response({'code': 409, 'msg': '订单原付款或退款凭证待核对，暂不能取消'}, status=409)
                if state:
                    state.status, state.reason = 'CANCELLED', data['reason'][:255]
                    state.save(update_fields=['status', 'reason', 'updated_at'])
                from django.utils import timezone
                completed_application = OrderGrabReviewCancellation.objects.filter(
                    order=order, status='PENDING').update(status='CANCELLED', completed_at=timezone.now())
                from .notifier import notify_order_update
                transaction.on_commit(lambda: notify_order_update(order), robust=True)
            audit = AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
                operator_name=(request.account.nickname or request.account.username)[:150], method='POST',
                path=request.path[:255], resource='grab-review', object_id=str(order.pk),
                request_body=dict(data), status_code=200)
            if data['action'] == 'CANCEL' and completed_application:
                from site_messages.order_customer_cancel_notice import prepare
                prepare(order, refund=refund_result, review_audit=audit)
            request._request._business_audit_recorded = True
        return Response({'code': 0, 'data': {'order_id': order_id, 'action': data['action']}})
