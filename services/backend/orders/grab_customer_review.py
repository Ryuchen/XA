"""Customer request for staff handling of an unresolved grab order."""
from rest_framework.response import Response
from stores.db import transaction
from .models import (Order, OrderGrabPoolOpeningIntent, OrderGrabReceiptProbe,
                     OrderGrabReviewCancellation, OrderGrabSingleRoundState,
                     OrderGrabTeamRoundState)


def request_cancellation(request, order_id):
    reason = request.data.get('reason') or ''
    if not isinstance(reason, str) or len(reason.strip()) > 255:
        return Response({'code': 400, 'msg': '取消原因不能超过 255 字'}, status=400)
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id, customer_account=request.account).first()
        if not order or request.account.account_type != 'BOSS':
            return Response({'code': 404, 'msg': '订单不存在'}, status=404)
        existing = OrderGrabReviewCancellation.objects.select_for_update().filter(order=order).first()
        if existing:
            return Response({'code': 0, 'msg': '取消申请已提交' if existing.status == 'PENDING' else '订单已取消',
                             'data': {'status': existing.status}})
        if order.status != Order.Status.PENDING:
            return Response({'code': 409, 'msg': '订单状态已变化，请刷新后查看'}, status=409)
        model = OrderGrabSingleRoundState if order.required_provider_count == 1 else OrderGrabTeamRoundState
        state = (model.objects.select_for_update().filter(round__pool__order=order)
                 .select_related('round').order_by('-round__number').first())
        opening = OrderGrabPoolOpeningIntent.objects.select_for_update().filter(order=order).first()
        unresolved = bool(not state and opening and opening.state == 'REVIEW')
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
        OrderGrabReviewCancellation.objects.create(order=order, customer=request.account, reason=reason.strip())
    return Response({'code': 0, 'msg': '已提交客服核对，处理完成后通知你', 'data': {'status': 'PENDING'}})
