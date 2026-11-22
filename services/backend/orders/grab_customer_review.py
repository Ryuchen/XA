"""Customer request for staff handling of an unresolved grab order."""
from rest_framework.response import Response
from stores.db import transaction
from .grab_round_facts import GrabRoundReviewRequired, latest_round_state
from .models import Order, OrderGrabPoolOpeningIntent, OrderGrabReceiptProbe, OrderGrabReviewCancellation


class PendingGrabCancellationReview(ValueError):
    """The order's original review request must be completed by the review flow."""


def has_pending_review(order_id, *, lock=False):
    rows = OrderGrabReviewCancellation.objects.filter(order_id=order_id, status='PENDING')
    return (rows.select_for_update() if lock else rows).exists()


def require_no_pending_review(order_id):
    """Recheck while holding the order lock, before a competing operation."""
    if has_pending_review(order_id, lock=True):
        raise PendingGrabCancellationReview('老板已提交取消申请，请在抢单核对中处理')


def replay_cancellation(request, order_id):
    if request.account.account_type != 'BOSS' or not OrderGrabReviewCancellation.objects.filter(
            order_id=order_id, customer=request.account).exists():
        return None
    return request_cancellation(request, order_id, request_key=request.data.get('client_request_id'), replay_only=True)


def default_timed_cancel_key(order_id, account_id, *, team):
    prefix = 'cancel-team' if team else 'cancel'
    return f'{prefix}-{order_id}-{account_id}'


def request_cancellation(request, order_id, *, request_key, replay_only=False):
    reason = request.data.get('reason') or ''
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id, customer_account=request.account).first()
        if not order or request.account.account_type != 'BOSS':
            return Response({'code': 404, 'msg': '订单不存在'}, status=404)
        existing = OrderGrabReviewCancellation.objects.select_for_update().filter(order=order).first()
        if replay_only and existing is None:
            return None
        if replay_only and request_key == '' and order.required_provider_count == 2:
            request_key = None
        if request_key is not None and (
                not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 64):
            return Response({'code': 400, 'msg': '取消请求编号无效'}, status=400)
        if not isinstance(reason, str) or len(reason.strip()) > 255:
            return Response({'code': 400, 'msg': '取消原因不能超过 255 字'}, status=400)
        reason = reason.strip()
        if existing:
            if existing.customer_id != request.account.pk:
                return Response({'code': 404, 'msg': '订单不存在'}, status=404)
            if request_key is not None and request_key == existing.request_key and reason != existing.reason:
                return Response({'code': 409, 'errorCode': 'REQUEST_ID_CONFLICT',
                                 'msg': '请求编号已用于其他取消原因'}, status=409)
        if existing is None or request_key != existing.request_key:
            timed_key = request_key or default_timed_cancel_key(order.pk, request.account.pk,
                                                               team=order.required_provider_count == 2)
            prior = list(OrderGrabReceiptProbe.objects.select_for_update().filter(round__pool__order=order,
                actor=request.account, request_key=timed_key, kind='BOSS_CANCEL').order_by('pk')[:2])
            if prior:
                expected_payloads = ({'reason': reason},) if reason else ({'reason': ''}, {})
                if len(prior) != 1 or prior[0].payload not in expected_payloads:
                    return Response({'code': 409, 'errorCode': 'REQUEST_ID_CONFLICT',
                                     'msg': '请求编号已用于其他登记'}, status=409)
                from .grab_receipt_probe import _effective_state
                if _effective_state(prior[0], None) == 'AFTER':
                    return Response({'code': 409, 'errorCode': 'GRAB_CANCEL_CLOSED',
                                     'msg': '订单取消时限已到'}, status=409)
                if prior[0].state == 'AFTER' or (existing and not replay_only):
                    return Response({'code': 409, 'errorCode': 'GRAB_CANCEL_RECEIPT_REVIEW',
                                     'msg': '原取消登记结果待核对'}, status=409)
                if replay_only:
                    return None  # Let the timed flow continue processing its own receipt.
        if existing:
            return Response({'code': 0, 'msg': '取消申请已提交' if existing.status == 'PENDING' else '订单已取消',
                             'data': {'status': existing.status}})
        if order.status != Order.Status.PENDING:
            return Response({'code': 409, 'msg': '订单状态已变化，请刷新后查看'}, status=409)
        try:
            state = latest_round_state(order.pk, seats=order.required_provider_count, lock=True)
        except GrabRoundReviewRequired:
            state, unresolved = None, True
        else:
            unresolved = not state and OrderGrabPoolOpeningIntent.objects.select_for_update().filter(
                order=order, state='REVIEW').exists()
        if state and state.status in ('OPEN', 'PARKED', 'REVIEW'):
            from .grab_receipt_probe import _effective_state
            from .grab_clock_contract import production_enabled
            from .grab_round_opening import opening_bounds
            rows = OrderGrabReceiptProbe.objects.filter(round=state.round).select_related('pass_evidence')
            unresolved = state.status == 'REVIEW' or any(
                _effective_state(row, getattr(row, 'pass_evidence', None)) in ('PENDING', 'REVIEW')
                for row in rows)
            if production_enabled():
                try:
                    opening_bounds(state.round)
                except ValueError:
                    unresolved = True
        if not unresolved:
            return Response({'code': 409, 'msg': '订单无需人工核对，请使用正常取消流程'}, status=409)
        OrderGrabReviewCancellation.objects.create(order=order, customer=request.account,
                                                  request_key=request_key, reason=reason)
        from .notifier import notify_order_update
        transaction.on_commit(lambda: notify_order_update(order, refresh_pool=True), robust=True)
    return Response({'code': 0, 'msg': '已提交客服核对，处理完成后通知你', 'data': {'status': 'PENDING'}})
