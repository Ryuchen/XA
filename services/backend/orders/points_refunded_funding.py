"""Funding basis after one fully evidenced points negotiation."""
from django.conf import settings
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import (Order, OrderOriginalShareTransfer, OrderPointsFundingReleaseEvidence,
                     PointsNegotiationPosting)
from .points_checkout_test import require_isolated_checkout
from .points_funding_plan import PointsFundingPlanError


def later_addon_runs(order, posting, *, before=None):
    """Verify add-on events after refund one and before a later main refund."""
    from .models import OrderAddonFundingReleaseRun, OrderAddonPayment, OrderAddonRefund, OrderAddonHandover
    following = PointsNegotiationPosting.objects.filter(
        proposal__points_terms__prior_refund_id=posting.points_refund_id).order_by('created_at', 'pk').first()
    payments = OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__gt=posting.created_at)
    refunds = OrderAddonRefund.objects.filter(order=order, created_at__gt=posting.created_at)
    handovers = OrderAddonHandover.objects.filter(payment__intent__order=order, status='ACCEPTED',
                                                 accepted_at__gt=posting.created_at)
    if before is not None:
        if (payments.filter(buyer_transaction__created_at=before).exists()
                or refunds.filter(created_at=before).exists() or handovers.filter(accepted_at=before).exists()):
            raise ValidationError('加购事件与历史凭证时间无法区分')
        payments = payments.filter(buyer_transaction__created_at__lt=before)
        refunds = refunds.filter(created_at__lt=before)
        handovers = handovers.filter(accepted_at__lt=before)
    if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
        handovers = handovers.none()
    if following:
        if (payments.filter(buyer_transaction__created_at=following.created_at).exists()
                or refunds.filter(created_at=following.created_at).exists()
                or handovers.filter(accepted_at=following.created_at).exists()):
            raise ValidationError('加购与后续主单退款时序无法区分')
        payments = payments.filter(buyer_transaction__created_at__lt=following.created_at)
        refunds = refunds.filter(created_at__lt=following.created_at)
        handovers = handovers.filter(accepted_at__lt=following.created_at)
    payments = list(payments.select_related('buyer_transaction'))
    refunds, handovers = list(refunds), list(handovers)
    payment_ids, refund_ids = {item.pk for item in payments}, {item.pk for item in refunds}
    handover_ids = {item.pk for item in handovers}
    runs = list(OrderAddonFundingReleaseRun.objects.filter(Q(payment_id__in=payment_ids) | Q(refund_id__in=refund_ids)
                                                          | Q(handover_id__in=handover_ids))
                .select_related('refund', 'payment__intent').order_by('pk'))
    if (len(runs) != len(payments) + len(refunds) + len(handovers)
            or {row.payment_id for row in runs if row.payment_id} != payment_ids
            or {row.refund_id for row in runs if row.refund_id} != refund_ids
            or {row.handover_id for row in runs if row.handover_id} != handover_ids
            or any(sum((bool(row.payment_id), bool(row.refund_id), bool(row.handover_id))) != 1 for row in runs)
            or any(row.refunded_main_snapshot is None for row in runs)
            or any(row.handover_id and (not isinstance(row.points_handover_snapshot, dict)
                       or row.points_handover_snapshot.get('kind') != 'SAME_TEAM_V1') for row in runs)):
        raise ValidationError('主单退款后的加购资金版本缺失')
    event_times = {('payment', item.pk): item.buyer_transaction.created_at for item in payments}
    event_times.update({('refund', item.pk): item.created_at for item in refunds})
    event_times.update({('handover', item.pk): item.accepted_at for item in handovers})
    times = list(event_times.values())
    if any(not at for at in times) or len(set(times)) != len(times):
        raise ValidationError('加购付款或退款事件时序无法区分')
    def event_key(row):
        return ('payment', row.payment_id) if row.payment_id else ('refund', row.refund_id) if row.refund_id else ('handover', row.handover_id)
    runs.sort(key=lambda row: event_times[event_key(row)])
    if any(row.created_at < event_times[event_key(row)]
           for row in runs) or any(left.pk >= right.pk or left.created_at >= event_times[event_key(right)]
                                   for left, right in zip(runs, runs[1:])):
        raise ValidationError('加购资金版本与实际事件顺序不一致')
    from .addon_funding_release import verify_run
    for run in runs:
        verify_run(run)
    return runs


def plan_refunded_order(order_id, *, allow_finalized=False):
    """Return verified post-refund cash needs, not an allocation or payout."""
    plan, _, _ = first_refund_funding_basis(order_id, allow_finalized=allow_finalized)
    return plan


def first_refund_funding_basis(order_id, *, allow_finalized=False):
    """Return the plan, its release evidence and the verified later add-on runs."""
    from .points_business_access import permits
    formal = (permits(phase='payment', kind='addon') or any(permits(phase=phase, kind=kind)
              for kind in ('service', 'addon') for phase in ('funding', 'refund', 'report', 'settlement')))
    if not formal:
        try:
            require_isolated_checkout()
        except ValidationError as exc:
            raise PointsFundingPlanError('退款后补资尚未开放') from exc
    if (not transaction.get_connection().in_atomic_block
            or (not formal and (not getattr(settings, 'SERVICE_POINTS_REFUNDED_FUNDING_TESTING', False)
                or not getattr(settings, 'SERVICE_POINTS_FUNDING_RELEASE_TESTING', False)))):
        raise PointsFundingPlanError('退款后补资尚未开放')
    from .points_negotiation_termination import enabled, verify_termination_locked
    if not enabled() or type(order_id) is not int or order_id <= 0:
        raise PointsFundingPlanError('退款后补资条件不满足')
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if formal and (not order or not (permits(order, phase='payment', kind='addon') or any(
            permits(order, phase=phase, kind=kind) for kind in ('service', 'addon')
            for phase in ('funding', 'refund', 'report', 'settlement')))):
        raise PointsFundingPlanError('积分退款资金上下文与订单不一致')
    if (not order or order.status not in (Order.Status.IN_SERVICE, Order.Status.COMPLETED)
            or order.source_order_id or Order.objects.filter(source_order_id=order.pk).exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
            or order.refund_records.count() != 1):
        raise PointsFundingPlanError('退款后订单、加购或转单记录待核对')
    if order.settled_at:
        from .models import OrderPointsRefundSettlement
        if not allow_finalized or not OrderPointsRefundSettlement.objects.filter(
                posting__points_refund__redemption__order=order, finalized_at=order.settled_at).exists():
            raise PointsFundingPlanError('退款后订单已经结算，请先核对')
    from .financial_lock import lock_order_money
    lock_order_money()
    postings = list(PointsNegotiationPosting.objects.select_for_update().filter(points_refund__redemption__order=order))
    if len(postings) != 1:
        raise PointsFundingPlanError('退款后协商记录待核对')
    try:
        posting = verify_termination_locked(postings[0])
        evidence = OrderPointsFundingReleaseEvidence.objects.filter(posting=posting).first()
        if not evidence:
            raise ValidationError('退款后补资释放凭证缺失')
        from .points_funding_release import verify_release_locked
        original = verify_release_locked(evidence)
        runs = later_addon_runs(order, posting)
        if not runs:
            return original, evidence, runs
        from .points_negotiation_preview import RefundedFunding
        return RefundedFunding(**runs[-1].refunded_main_snapshot['funding']), evidence, runs
    except ValidationError as exc:
        raise PointsFundingPlanError('退款后资金与协商凭证待核对') from exc
