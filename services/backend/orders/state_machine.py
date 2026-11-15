"""订单状态机：集中管理状态转换、副作用与审计日志。

设计要点：
- TRANSITIONS：合法状态转换白名单
- transition()：唯一的状态切换入口，自动加锁、写日志、推送
- 副作用（钱包/EscortProfile）通过 hooks 注入，避免 state_machine 与业务模块循环依赖
"""
from typing import Callable, Dict, Optional, Tuple

from stores.db import transaction
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
    room_confirmation_id=None,
    room_negotiation_execution_id=None,
    room_points_negotiation_posting_id=None,
    room_points_zero_followup_id=None,
    room_negotiation_followup_id=None,
    room_subsidy_agreement_id=None,
    customer_completion_notice=False,
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
    points_assignment = False
    if to_status == Order.Status.GRABBED and order.payment_status != Order.PaymentStatus.PAID:
        raise IllegalTransitionError('订单未支付，不能接单或派单')
    if to_status == Order.Status.GRABBED:
        from .points_service_payment import needs_integrated_processing
        if needs_integrated_processing(order):
            from .points_checkout_test import assignment_enabled
            if not assignment_enabled():
                raise IllegalTransitionError('积分订单的陪玩收益尚待接通，暂不能派单')
            from .points_business_access import configured, permits
            if configured() and not (permits(order, phase='assignment', kind='service')
                                     or permits(order, phase='checkout', kind='service')):
                raise IllegalTransitionError('积分派单上下文与订单不一致')
            points_assignment = True
        from .direct_payment_models import DirectPaymentIntent
        from .direct_payment_quote import quote_matches_intent
        intent = DirectPaymentIntent.objects.filter(order=order).first()
        if intent and (intent.status != DirectPaymentIntent.Status.PAID or not quote_matches_intent(intent, order)):
            raise IllegalTransitionError('支付证据待核对，不能接单或派单')
    if to_status == Order.Status.IN_SERVICE:
        from .points_service_payment import needs_integrated_processing
        if needs_integrated_processing(order):
            from .points_checkout_test import report_enabled
            from .points_funding_plan import PointsFundingPlanError, plan_verified_order
            if not report_enabled():
                raise IllegalTransitionError('积分订单履约与结算尚未接通，暂不能开始服务')
            if (order.settlement_mode != Order.SettlementMode.REPORT_APPROVAL or order.service_clock_version is None
                    or order.service_aftersale_hours is None or order.service_freeze_hours is None
                    or order.service_deposit_percent is None):
                raise IllegalTransitionError('订单报单时限快照待核对')
            try:
                plan_verified_order(order.pk)
            except PointsFundingPlanError as exc:
                raise IllegalTransitionError('积分订单付款或收益快照待核对') from exc
    if to_status == Order.Status.COMPLETED and order.room_required_snapshot is True:
        from .models import OrderRoomConfirmation
        proof = OrderRoomConfirmation.objects.filter(pk=room_confirmation_id, request__room__order=order,
                                                     request__status='VERIFIED') if room_confirmation_id else OrderRoomConfirmation.objects.none()
        manual = (operator_account and operator_account.pk == order.customer_account_id
                  and proof.filter(status='CONFIRMED', confirmed_by=operator_account).exists())
        from django.conf import settings
        automatic = (operator_account is None and operator is None and getattr(settings, 'ROOM_AUTO_CONFIRMATION_ENABLED', False)
                     and getattr(settings, 'ROOM_COMPLETION_REVIEW_ENABLED', False)
                     and getattr(settings, 'ROOM_BOSS_CONFIRMATION_ENABLED', False)
                     and proof.filter(status='AUTO', confirmed_by__isnull=True).exists())
        from .room_negotiated_guard import (verified_room_termination, verified_room_points_termination,
                                            verified_room_followup_termination, verified_room_subsidy_termination)
        negotiated = (action == OrderStatusLog.Action.COMPLETE and verified_room_termination(
            order, room_negotiation_execution_id, operator_account))
        points_negotiated = (action == OrderStatusLog.Action.COMPLETE and verified_room_points_termination(
            order, room_points_negotiation_posting_id, operator_account))
        points_zero = False
        if action == OrderStatusLog.Action.COMPLETE and room_points_zero_followup_id:
            from .points_zero_followup import verify_record_locked
            from .models import PointsZeroFollowupTermination
            from .refunds import authorized_refund
            record = PointsZeroFollowupTermination.objects.select_for_update().filter(
                pk=room_points_zero_followup_id, order=order, operator=operator_account).first()
            if record and operator_account and authorized_refund(operator_account):
                verify_record_locked(record, pending=True)
                points_zero = True
        followup_negotiated = (action == OrderStatusLog.Action.COMPLETE and verified_room_followup_termination(
            order, room_negotiation_followup_id, operator_account))
        subsidy_negotiated = (action == OrderStatusLog.Action.COMPLETE and verified_room_subsidy_termination(
            order, room_subsidy_agreement_id, operator_account))
        if not (manual or automatic or negotiated or points_negotiated or points_zero or followup_negotiated or subsidy_negotiated):
            raise IllegalTransitionError('房间订单需经客服核实并由老板确认，结单流程尚未开放')
        from .models import OrderRoomObjection
        if OrderRoomObjection.objects.filter(room__order=order, status=OrderRoomObjection.Status.OPEN).exists():
            if not (negotiated or points_negotiated or points_zero or followup_negotiated or subsidy_negotiated):
                raise IllegalTransitionError('房间异议尚未处理，暂不能结束订单')
            if negotiated:
                from .models import NegotiationExecution
                execution = NegotiationExecution.objects.get(pk=room_negotiation_execution_id)
                proposal_id = execution.proposal_id
            elif points_negotiated:
                from .models import PointsNegotiationPosting
                proposal_id = PointsNegotiationPosting.objects.get(pk=room_points_negotiation_posting_id).proposal_id
            elif points_zero:
                proposal_id = record.proposal_id
            elif followup_negotiated:
                from .models import NegotiationFollowupLink
                execution = NegotiationFollowupLink.objects.select_related('execution').get(
                    pk=room_negotiation_followup_id).execution
                proposal_id = execution.proposal_id
            else:
                from .models import SubsidyAgreementExecution
                execution = SubsidyAgreementExecution.objects.select_related('proposal').get(pk=room_subsidy_agreement_id)
                proposal_id = execution.proposal_id
            from .room_objections import resolve_for_negotiated_termination_locked
            audit_path = (f'/api/admin/aftersales/{execution.proposal.case_id}/subsidy-negotiation-execution/'
                          if subsidy_negotiated and not (negotiated or points_negotiated or followup_negotiated)
                          else None)
            resolve_for_negotiated_termination_locked(order, proposal_id=proposal_id,
                                                      operator=operator_account, legacy_operator=operator,
                                                      audit_path=audit_path)
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

    if from_status == Order.Status.GRABBED and to_status in (Order.Status.CANCELLED, Order.Status.PENDING):
        from wallet.refund_recovery_order import cancel_unstarted
        cancel_unstarted(order)

    if to_status == Order.Status.IN_SERVICE and order.pk:
        # A paid appointment starts consuming a slot only now. No capacity
        # rejection applies: the already-confirmed booking may temporarily
        # exceed the normal limit, but its provider must appear busy.
        from .models import Reservation
        if Reservation.objects.filter(order_id=order.pk, status=Reservation.Status.CONVERTED).exists():
            from .services import mark_escort_busy
            assignments = list(order.providers.filter(released_at__isnull=True)
                               .values_list('provider_id', 'provider_account_id'))
            mark_escort_busy(provider_ids=[order.provider_id] + [row[0] for row in assignments],
                             account_ids=[order.provider_account_id] + [row[1] for row in assignments])

    if update_fields:
        fields_to_save.extend(update_fields)

    order.save(update_fields=list(set(fields_to_save)))

    if to_status == Order.Status.GRABBED:
        from .rooms import create_assigned_room_locked
        create_assigned_room_locked(order)

    from .original_attribution import enabled as original_attribution_enabled
    if original_attribution_enabled() and from_status == Order.Status.PENDING and to_status == Order.Status.GRABBED:
        from .original_attribution import assigned
        assigned(order, at=now)
        from .segment_financial_capture import subsidy_capture_enabled
        if subsidy_capture_enabled():
            from .models import OrderPlatformSubsidy, OrderSegmentFinancialCoverage
            if OrderPlatformSubsidy.objects.filter(order=order).exists():
                coverage = OrderSegmentFinancialCoverage.objects.select_for_update().filter(attribution__order=order).first()
                if not coverage or coverage.source != 'PLATFORM_SUBSIDY' or coverage.status != 'ACTIVE':
                    raise IllegalTransitionError('安全箱补贴抽成快照待核对')
        if points_assignment:
            from .models import OrderSegmentFinancialCoverage
            coverage = OrderSegmentFinancialCoverage.objects.select_for_update().filter(attribution__order=order).first()
            if not coverage or coverage.source != 'WALLET_POINTS' or coverage.status != 'ACTIVE':
                raise IllegalTransitionError('积分订单陪玩收益快照待核对')
    elif from_status == Order.Status.GRABBED and to_status == Order.Status.IN_SERVICE:
        from .original_attribution import started
        accounts = list(order.providers.filter(released_at__isnull=True).values_list('provider_account_id', flat=True))
        if not accounts and order.provider_account_id:
            accounts = [order.provider_account_id]
        source = 'STAFF_ACTION' if operator_account and operator_account.account_type == 'STAFF' else 'ORDER_START'
        started(order, accounts, at=now, source=source)

    if (from_status == Order.Status.GRABBED and to_status == Order.Status.IN_SERVICE
            and order.escort_mode == Order.EscortMode.SINGLE and operator_account
            and operator_account.pk == order.provider_account_id):
        from .work_stints import initial_entry
        try:
            initial_entry(order, operator_account.pk, at=now)
        except ValueError as exc:
            raise IllegalTransitionError(str(exc)) from exc

    status_log = OrderStatusLog.objects.create(
        order=order,
        action=action or TRANSITIONS[(from_status, to_status)],
        from_status=from_status,
        to_status=to_status,
        operator=operator,
        operator_account=operator_account,
        reason=reason,
    )

    if (from_status == Order.Status.GRABBED and to_status == Order.Status.IN_SERVICE
            and status_log.action == OrderStatusLog.Action.START and operator_account
            and operator_account.account_type == 'STAFF'):
        from site_messages.staff_start_notice import enabled, record_all
        if enabled():
            record_all(order, status_log)

    if customer_completion_notice:
        if (from_status != Order.Status.IN_SERVICE or to_status != Order.Status.COMPLETED
                or status_log.action != OrderStatusLog.Action.COMPLETE or order.room_required_snapshot is True):
            raise IllegalTransitionError('订单完成通知来源待核对')
        from site_messages.order_completion_notice import prepare
        prepare(order, status_log)

    if from_status == Order.Status.PENDING and to_status == Order.Status.CANCELLED:
        from .applications import notify_cancelled_applicants
        notify_cancelled_applicants(order)

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
