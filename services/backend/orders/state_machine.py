"""订单状态机：集中管理状态转换、副作用与审计日志。

设计要点：
- TRANSITIONS：合法状态转换白名单
- transition()：唯一的状态切换入口，自动加锁、写日志、推送
- 副作用（钱包/EscortProfile）通过 hooks 注入，避免 state_machine 与业务模块循环依赖
"""
from typing import Callable, Dict, Optional, Tuple

from django.db import transaction
from django.utils import timezone

from .models import Order, OrderStatusLog


class StateMachineError(Exception):
    """状态机转换异常的基类"""


class IllegalTransitionError(StateMachineError):
    """非法状态转换"""


# 合法状态转换白名单：(from_status, to_status) -> Action
TRANSITIONS: Dict[Tuple[str, str], str] = {
    (Order.Status.PENDING, Order.Status.GRABBED): OrderStatusLog.Action.GRAB,
    (Order.Status.PENDING, Order.Status.CANCELLED): OrderStatusLog.Action.CANCEL,
    (Order.Status.GRABBED, Order.Status.IN_SERVICE): OrderStatusLog.Action.START,
    (Order.Status.GRABBED, Order.Status.PENDING): OrderStatusLog.Action.REJECT,
    (Order.Status.GRABBED, Order.Status.CANCELLED): OrderStatusLog.Action.CANCEL,
    (Order.Status.IN_SERVICE, Order.Status.COMPLETED): OrderStatusLog.Action.COMPLETE,
    (Order.Status.IN_SERVICE, Order.Status.CANCELLED): OrderStatusLog.Action.REFUND,
    (Order.Status.COMPLETED, Order.Status.CANCELLED): OrderStatusLog.Action.REFUND,
}


def can_transition(from_status: str, to_status: str) -> bool:
    return (from_status, to_status) in TRANSITIONS


@transaction.atomic
def transition(
    order_id: int,
    to_status: str,
    *,
    operator=None,
    operator_account=None,
    action: Optional[str] = None,
    reason: str = '',
    pre_check: Optional[Callable[[Order], None]] = None,
    side_effect: Optional[Callable[[Order], None]] = None,
    update_fields: Optional[list] = None,
    dispatch_account_ids=(),
    allow_settled_refund=False,
) -> Order:
    """统一的状态切换入口。

    Args:
        order_id: 订单 ID
        to_status: 目标状态
        operator: 操作人（可空，超时任务无操作人）
        operator_account: ClubAccount 业务操作人
        action: OrderStatusLog.Action，不传则按 TRANSITIONS 推断
        reason: 操作原因（用于审计）
        pre_check: 前置校验回调，参数为加锁后的 order；不通过应抛异常
        side_effect: 副作用回调（如钱包退款、EscortProfile 状态更新）
        update_fields: 额外需要保存的字段名（除 status 与时间戳外）
        dispatch_account_ids: 接单/派单目标账号，容量校验前锁定其名额

    Returns:
        更新后的 Order 实例

    Raises:
        Order.DoesNotExist
        IllegalTransitionError
        其他业务异常（由 pre_check / side_effect 抛出）
    """
    # 只锁订单，关联用户/服务的查询不能把无关订单串行化或反转资源锁序。
    order = Order.objects.select_for_update().get(id=order_id)

    from_status = order.status
    authorized_settled_refund = False
    if allow_settled_refund:
        from .refunds import authorized_refund
        authorized_settled_refund = (to_status == Order.Status.CANCELLED
            and action == OrderStatusLog.Action.REFUND and authorized_refund(operator_account))
        if not authorized_settled_refund:
            raise IllegalTransitionError('仅授权后台退款可流转已结算订单')
    if from_status == Order.Status.COMPLETED and not authorized_settled_refund and (
        order.settlement_mode != Order.SettlementMode.REPORT_APPROVAL or order.settled_at
    ):
        raise IllegalTransitionError('订单已结算，无法再次流转')
    if not can_transition(from_status, to_status):
        raise IllegalTransitionError(
            f'非法状态转换: {from_status} -> {to_status}'
        )

    if dispatch_account_ids:
        from .services import lock_provider_dispatch
        lock_provider_dispatch(dispatch_account_ids)

    if pre_check:
        pre_check(order)

    now = timezone.now()
    fields_to_save = ['status', 'updated_at']

    # 一旦订单离开抢单池，必须清理抢单超时点。否则延迟执行的自动取消
    # 任务可能把已经接单的订单误判为仍在等待接单。
    if from_status == Order.Status.PENDING and to_status == Order.Status.GRABBED:
        order.auto_cancel_at = None
        fields_to_save.append('auto_cancel_at')

    # 自动维护时间戳字段
    timestamp_field = _STATUS_TIMESTAMP_FIELDS.get(to_status)
    if timestamp_field:
        setattr(order, timestamp_field, now)
        fields_to_save.append(timestamp_field)

    order.status = to_status

    if to_status == Order.Status.COMPLETED and order.customer_account_id:
        # Acquire the customer's wallet before fulfillment credits other wallets.
        # This also serializes two completions crossing a VIP threshold together.
        from wallet.services import get_wallet
        from .financial_lock import lock_order_money
        lock_order_money()
        get_wallet(account_id=order.customer_account_id, for_update=True)

    if side_effect:
        side_effect(order)

    if update_fields:
        fields_to_save.extend(update_fields)

    order.save(update_fields=list(set(fields_to_save)))

    OrderStatusLog.objects.create(
        order=order,
        action=action or TRANSITIONS[(from_status, to_status)],
        from_status=from_status,
        to_status=to_status,
        operator=operator,
        operator_account=operator_account,
        reason=reason,
    )

    return order


# 状态 -> 自动维护的时间戳字段
_STATUS_TIMESTAMP_FIELDS = {
    Order.Status.GRABBED: 'grabbed_at',
    Order.Status.IN_SERVICE: 'in_service_at',
    Order.Status.COMPLETED: 'completed_at',
    Order.Status.CANCELLED: 'cancelled_at',
}


def log_only(
    order: Order,
    *,
    action: str,
    operator=None,
    operator_account=None,
    reason: str = '',
) -> None:
    """记录不涉及状态变更的事件（如订单创建、退款标记）。"""
    OrderStatusLog.objects.create(
        order=order,
        action=action,
        from_status=order.status,
        to_status=order.status,
        operator=operator,
        operator_account=operator_account,
        reason=reason,
    )
