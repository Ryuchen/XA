"""Handle the buyer's timed cancellation of a single or team grab order."""
from rest_framework.response import Response

from .grab_receipt_probe import GrabRequestConflict, record_intent


def _cancelled():
    return Response({'code': 0, 'msg': '订单已取消，资金及权益已按规则退回', 'data': {'status': 'CANCELLED'}})


def cancel(request, order_id, *, state, enabled, resolve, team):
    if (not state or request.account.account_type != 'BOSS'
            or state.round.pool.order.customer_account_id != request.account.pk):
        return Response({'code': 404, 'msg': '订单不存在'}, status=404)
    if not enabled:
        return Response({'code': 503, 'msg': '订单取消暂需客服核对，请联系客服'}, status=503)

    key = request.data.get('client_request_id')
    if key is None or (team and not key):
        prefix = 'cancel-team' if team else 'cancel'
        key = f'{prefix}-{order_id}-{request.account.pk}'
    if not isinstance(key, str) or not key.strip() or len(key) > 64:
        return Response({'code': 400, 'msg': '取消请求编号无效'}, status=400)
    reason = request.data.get('reason') or ''
    if not isinstance(reason, str):
        return Response({'code': 400, 'msg': '取消原因无效'}, status=400)

    active = state.status in ('OPEN', 'PARKED')
    try:
        result = record_intent(state.round_id, request.account.pk, 'BOSS_CANCEL', key,
                               {'reason': reason.strip()[:255]}, single_flow=not team, team_flow=team,
                               replay_only=not active)
    except GrabRequestConflict as exc:
        return Response({'code': 409, 'errorCode': 'REQUEST_ID_CONFLICT', 'msg': str(exc)}, status=409)
    except ValueError as exc:
        return Response({'code': 400, 'msg': str(exc)}, status=400)

    if result and result.state == 'AFTER':
        return Response({'code': 409, 'errorCode': 'GRAB_CANCEL_CLOSED', 'msg': '订单取消时限已到'}, status=409)
    if state.status == 'CANCELLED':
        return _cancelled()
    if result and not result.current_round:
        return Response({'code': 409, 'msg': '取消登记属于旧轮，请查看原订单结果'}, status=409)
    if state.status == 'REVIEW' or (active and result.state != 'BEFORE'):
        from .grab_customer_review import request_cancellation
        return request_cancellation(request, order_id)
    if not active:
        return Response({'code': 409, 'msg': '订单已分配或正在核对，请查看订单状态'}, status=409)
    if resolve(order_id) != 'CANCELLED':
        return Response({'code': 409, 'msg': '取消请求已登记，请查看订单状态'}, status=409)
    return _cancelled()
