"""Read-only evidence for a transferred free safety-box negotiation."""
from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .settlement import SettlementError


def preview_locked(order, case, scope):
    """Separate the active segment from this person's prior completed segments.

    This calculation does not create a proposal or authorize settlement. The
    caller holds the order and case locks for the duration of the read.
    """
    from .subsidy_segment_settlement import enabled as settlement_enabled
    from .subsidy_transfer_capability import stage_enabled
    if not (stage_enabled('VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PREVIEW_TESTING')
            and settlement_enabled()):
        raise ValidationError('安全箱转单协商核对尚未开放')
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('安全箱转单协商须在订单事务内核对')
    now = timezone.now()
    if (order.status not in (order.Status.IN_SERVICE, order.Status.COMPLETED)
            or order.payment_status != order.PaymentStatus.PAID
            or order.settled_at or order.refund_records.exists()
            or case.status != 'OPEN' or case.order_id != order.pk or case.account_id != order.customer_account_id
            or not order.in_service_at or not case.created_at or not case.client_request_id
            or not order.in_service_at <= case.created_at <= now
            or (order.status == order.Status.IN_SERVICE and order.completed_at)
            or (order.status == order.Status.COMPLETED and
                (not order.completed_at or not order.in_service_at <= order.completed_at <= now))):
        raise ValidationError('订单或售后状态已变化，请重新核对')
    if order.status == order.Status.COMPLETED:
        from engagement.confirmation import aftersale_deadline
        deadline = aftersale_deadline(order)
        if not deadline or case.created_at >= deadline:
            raise ValidationError('售后申请时间超出订单售后期')
    from .models import (OrderOriginalShareTransfer, OrderParticipationSegment,
                         SubsidyAgreementExecution)
    from .subsidy_execution import table_available
    if not table_available():
        raise ValidationError('安全箱协商记录尚未就绪')
    if (not OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
            or SubsidyAgreementExecution.objects.filter(order=order).exists()):
        raise ValidationError('转单或已有协商记录待核对')
    from .financial_lock import lock_order_money
    from .subsidies import _locked_subsidy_shares
    from .segment_settlement_plan import build_segment_settlement_plan
    lock_order_money()
    try:
        shares = _locked_subsidy_shares(order)
        plan = build_segment_settlement_plan(order, _allow_subsidy=True,
                                             _preview_unstarted=order.status == order.Status.IN_SERVICE)
    except SettlementError as exc:
        raise ValidationError(str(exc)) from exc
    if any(share.paid_at or share.assignment.settled_at for share in shares):
        raise ValidationError('已支付补贴须先核对')
    active = list(OrderParticipationSegment.objects.select_for_update().filter(
        attribution__order=order, released_at__isnull=True))
    if (len(active) != 1 or scope != [active[0].provider_account_id]
            or active[0].provider_account_id != order.provider_account_id
            or not active[0].started_at or active[0].started_at > case.created_at):
        raise ValidationError('协商范围必须是已开始履约的当前陪玩')
    account_id = active[0].provider_account_id
    lines = [line for line in plan.segments if line.account_id == account_id]
    current = [line for line in lines if line.segment_id == active[0].pk]
    if len(current) != 1:
        raise ValidationError('当前履约段补贴待核对')
    previous = sum(line.income for line in lines if line.segment_id != active[0].pk)
    total = previous + current[0].income
    share = next((row for row in shares if row.assignment.provider_account_id == account_id), None)
    if not share or share.amount != total:
        raise ValidationError('陪玩分段补贴与原份额不一致')
    return {'account_id': account_id, 'segment_id': active[0].pk,
            'prior_income_units': previous, 'current_original_income_units': current[0].income,
            'original_total_income_units': total}
