"""预约状态机：预约生命周期的唯一切换入口。

与 ``orders.state_machine`` 同构但刻意独立：预约和订单是两套生命周期，
共用一张转换表会让「接单/开始服务」这类订单专属动作漏进预约，
校验形同虚设。这里只维护预约自己的白名单。

状态流转全图::

    PENDING --confirm--> CONFIRMED --convert--> CONVERTED(终态)
       |                     |
       |                     +--cancel--> CANCELLED(终态)
       |                     +--expire--> EXPIRED(终态)
       +--reject--> REJECTED(终态)
       +--cancel--> CANCELLED(终态)
       +--expire--> EXPIRED(终态)
"""

from typing import Callable, Dict, List, Optional, Tuple

from django.db import transaction
from django.utils import timezone

from .models import Reservation, ReservationStatusLog


class ReservationStateMachineError(Exception):
    """预约状态机异常基类。"""


class IllegalReservationTransitionError(ReservationStateMachineError):
    """非法的预约状态转换。"""


# 合法状态转换白名单：(from_status, to_status) -> Action
RESERVATION_TRANSITIONS: Dict[Tuple[str, str], str] = {
    (Reservation.Status.PENDING, Reservation.Status.CONFIRMED):
        ReservationStatusLog.Action.CONFIRM,
    (Reservation.Status.PENDING, Reservation.Status.REJECTED):
        ReservationStatusLog.Action.REJECT,
    (Reservation.Status.PENDING, Reservation.Status.CANCELLED):
        ReservationStatusLog.Action.CANCEL,
    (Reservation.Status.PENDING, Reservation.Status.EXPIRED):
        ReservationStatusLog.Action.EXPIRE,
    (Reservation.Status.CONFIRMED, Reservation.Status.CANCELLED):
        ReservationStatusLog.Action.CANCEL,
    (Reservation.Status.CONFIRMED, Reservation.Status.CONVERTED):
        ReservationStatusLog.Action.CONVERT,
    (Reservation.Status.CONFIRMED, Reservation.Status.EXPIRED):
        ReservationStatusLog.Action.EXPIRE,
}

# 状态 -> 自动维护的时间戳字段
_STATUS_TIMESTAMP_FIELDS: Dict[str, str] = {
    Reservation.Status.CONFIRMED: 'confirmed_at',
    Reservation.Status.REJECTED: 'rejected_at',
    Reservation.Status.CANCELLED: 'cancelled_at',
    Reservation.Status.CONVERTED: 'converted_at',
    Reservation.Status.EXPIRED: 'expired_at',
}


def can_transition_reservation(from_status: str, to_status: str) -> bool:
    """目标转换是否在白名单内。"""
    return (from_status, to_status) in RESERVATION_TRANSITIONS


@transaction.atomic
def transition_reservation(
    reservation_id: int,
    to_status: str,
    *,
    operator=None,
    operator_account=None,
    action: Optional[str] = None,
    reason: str = '',
    pre_check: Optional[Callable[[Reservation], None]] = None,
    side_effect: Optional[Callable[[Reservation], None]] = None,
    update_fields: Optional[List[str]] = None,
) -> Reservation:
    """统一的预约状态切换入口：加锁 -> 校验 -> 副作用 -> 落库 -> 写审计。

    Args:
        reservation_id: 预约 ID。
        to_status: 目标状态。
        operator: 操作人（legacy ``CustomUser``，可空）。
        operator_account: 业务操作人（``ClubAccount``，可空）。
        action: 审计动作，不传则按白名单推断。
        reason: 操作原因（取消/拒绝理由等），写入审计日志。
        pre_check: 前置校验回调，入参为加锁后的预约；不通过应抛异常。
        side_effect: 副作用回调（如回填 order 外键），在保存前执行。
        update_fields: 除状态与时间戳外还需要保存的字段名。

    Returns:
        Reservation: 更新后的预约实例。

    Raises:
        Reservation.DoesNotExist: 预约不存在。
        IllegalReservationTransitionError: 转换不在白名单内。
    """
    reservation = (
        Reservation.objects.select_for_update()
        .select_related('customer', 'provider', 'provider_account', 'service')
        .get(id=reservation_id)
    )

    from_status = reservation.status
    if not can_transition_reservation(from_status, to_status):
        raise IllegalReservationTransitionError(
            f'非法状态转换: {from_status} -> {to_status}'
        )

    if pre_check:
        pre_check(reservation)

    fields_to_save = ['status', 'updated_at']

    timestamp_field = _STATUS_TIMESTAMP_FIELDS.get(to_status)
    if timestamp_field:
        setattr(reservation, timestamp_field, timezone.now())
        fields_to_save.append(timestamp_field)

    reservation.status = to_status

    if side_effect:
        side_effect(reservation)

    if update_fields:
        fields_to_save.extend(update_fields)

    reservation.save(update_fields=sorted(set(fields_to_save)))

    ReservationStatusLog.objects.create(
        reservation=reservation,
        action=action or RESERVATION_TRANSITIONS[(from_status, to_status)],
        from_status=from_status,
        to_status=to_status,
        operator=operator,
        operator_account=operator_account,
        reason=reason,
    )

    return reservation


def log_reservation_event(
    reservation: Reservation,
    *,
    action: str,
    operator=None,
    operator_account=None,
    reason: str = '',
) -> ReservationStatusLog:
    """记录不涉及状态变更的事件（如创建）。"""
    return ReservationStatusLog.objects.create(
        reservation=reservation,
        action=action,
        from_status=reservation.status,
        to_status=reservation.status,
        operator=operator,
        operator_account=operator_account,
        reason=reason,
    )
