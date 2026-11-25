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

from stores.db import transaction

from .models import Reservation, ReservationStatusLog


class ReservationStateMachineError(Exception):
    """预约状态机异常基类。"""


class IllegalReservationTransitionError(ReservationStateMachineError):
    """非法的预约状态转换。"""

    def __init__(self, message, *, current_status=None):
        super().__init__(message)
        self.current_status = current_status


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
    now=None,
    clock_action: Optional[str] = None,
    clock_deadline=None,
    clock_decision_key: Optional[str] = None,
    clock_seat_number: Optional[int] = None,
    clock_request_fingerprint: str = '',
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
            f'非法状态转换: {from_status} -> {to_status}', current_status=from_status,
        )

    clock_decision = None
    if clock_action:
        from .reservation_clock import (
            prepare_decision, require_before, reservation_snapshot, validate_decision_key,
        )
        clock_decision = prepare_decision(
            reservation=reservation,
            action=clock_action,
            deadline=clock_deadline,
            actor_account=operator_account,
            seat_number=clock_seat_number,
            decision_key=validate_decision_key(clock_decision_key),
            state_before=from_status,
            before_snapshot={'reservation': reservation_snapshot(reservation)},
            request_fingerprint=clock_request_fingerprint,
        )
        if clock_decision.replayed:
            from .reservation_clock import ClockConflict
            raise ClockConflict('原预约决策已回执，禁止重复执行状态切换')
        # A lock wait may itself cross the cutoff.  Do not perform the business
        # write on an AFTER/REVIEW receipt; the API layer will expire under a
        # fresh locked observation after this transaction rolls back.
        if clock_decision.state != 'PENDING':
            require_before(clock_decision)
        now = clock_decision.lower_sample.instant
    if now is None:
        from .reservation_clock import sample, same_connection
        observed = sample()
        if not same_connection(observed):
            from .reservation_clock import ClockUnavailable
            raise ClockUnavailable('预约状态切换前数据库连接发生变化')
        now = observed.instant
    reservation._reservation_clock_now = now

    if pre_check:
        pre_check(reservation)

    fields_to_save = ['status', 'updated_at']

    timestamp_field = _STATUS_TIMESTAMP_FIELDS.get(to_status)
    if timestamp_field:
        setattr(reservation, timestamp_field, now)
        fields_to_save.append(timestamp_field)

    reservation.status = to_status

    if side_effect:
        side_effect(reservation)

    if update_fields:
        fields_to_save.extend(update_fields)

    reservation.save(update_fields=sorted(set(fields_to_save)))

    from django.conf import settings
    notice_intent = None
    notification_intents = None
    if (reservation.required_provider_count == 1 and to_status in ('CONFIRMED', 'REJECTED')
            and getattr(settings, 'RESERVATION_STATUS_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)):
        confirmed = to_status == 'CONFIRMED'
        notice_intent = {'account_id': reservation.customer_account_id,
            'business_cycle': reservation.customer_business_cycle, 'user_id': reservation.customer_id,
            'title': '预约已确认' if confirmed else '预约已拒绝',
            'preview': (f'预约 {reservation.reservation_no} 已确认，请核对报价后转为订单' if confirmed
                        else f'预约 {reservation.reservation_no} 已拒绝' + (f'：{reason}' if reason else ''))}
    if reservation.required_provider_count == 2 and to_status == 'CONFIRMED':
        from site_messages.reservation_team_confirmed import enabled, intents
        if enabled():
            notification_intents = intents(reservation)
    if (to_status == 'CANCELLED'
            and getattr(settings, 'RESERVATION_CANCELLATION_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)):
        from .reservation_event_notice import cancellation_intents
        notification_intents = cancellation_intents(reservation, operator_account, reason) or None
    if (to_status == 'EXPIRED'
            and getattr(settings, 'RESERVATION_EXPIRY_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)):
        from .reservation_event_notice import expiration_intents
        notification_intents = expiration_intents(reservation, from_status) or None
    if (to_status == 'CONVERTED'
            and getattr(settings, 'RESERVATION_CONVERSION_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)):
        from .reservation_event_notice import conversion_intents
        notification_intents = conversion_intents(reservation, operator_account) or None
    log = ReservationStatusLog.objects.create(
        reservation=reservation,
        action=action or RESERVATION_TRANSITIONS[(from_status, to_status)],
        from_status=from_status,
        to_status=to_status,
        operator=operator,
        operator_account=operator_account,
        reason=reason,
        status_notice_intent=notice_intent,
        notification_intents=notification_intents,
    )
    ReservationStatusLog.objects.filter(pk=log.pk).update(created_at=now)
    if notice_intent:
        from site_messages.reservation_status import schedule
        schedule('status', log.pk)
    if notification_intents:
        from site_messages.reservation_event import schedule
        schedule(log.pk)

    reservation._reservation_clock_decision = clock_decision
    reservation._reservation_clock_now = now
    return reservation


def log_reservation_event(
    reservation: Reservation,
    *,
    action: str,
    operator=None,
    operator_account=None,
    reason: str = '',
    now=None,
) -> ReservationStatusLog:
    """记录不涉及状态变更的事件（如创建）。"""
    if now is None:
        from .reservation_clock import sample, same_connection, ClockUnavailable
        observed = sample()
        if not same_connection(observed):
            raise ClockUnavailable('预约事件记录前数据库连接发生变化')
        now = observed.instant
    log = ReservationStatusLog.objects.create(
        reservation=reservation,
        action=action,
        from_status=reservation.status,
        to_status=reservation.status,
        operator=operator,
        operator_account=operator_account,
        reason=reason,
    )
    ReservationStatusLog.objects.filter(pk=log.pk).update(created_at=now)
    return log
