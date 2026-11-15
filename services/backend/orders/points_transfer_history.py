"""Rebuild funding at a particular transfer boundary without posting money.

This is a prerequisite for verifying future allocation-release revisions.
The most recent history and original payment are first reconciled by the
normal isolated transfer planner; earlier economics are then reconstructed
from the immutable entry snapshots and audited transfer basis records.
"""
from dataclasses import dataclass

from django.conf import settings
from django.db.models import Sum

from stores.db import transaction
from .models import (Order, OrderOriginalShareTransfer, OrderPointsRedemption,
                     OrderSegmentFinancialSnapshot, OrderSegmentTransferBasis)
from .points_funding_plan import PointsFundingPlan, PointsFundingPlanError, plan_service_funding
from .points_transfer_funding import plan_points_transfer
from .settlement import ProviderShare


@dataclass(frozen=True)
class HistoricTransferFunding:
    through_transfer_id: int
    segment_shares: tuple
    funding: PointsFundingPlan


def plan_historic_points_transfer(order_id, *, through_transfer_id, allow_recorded=False):
    """Reconstruct the entry funding (0) or a verified transfer prefix.

    A result is evidence for review, not permission to release or settle.
    All database reads occur in the caller's tenant transaction and store.
    """
    from .points_transfer_refund_access import permits
    if ((not getattr(settings, 'SERVICE_POINTS_TRANSFER_FUNDING_REVISION_TESTING', False)
            and not (permits(phase='refund') or permits(phase='transfer')))
            or not transaction.get_connection().in_atomic_block
            or type(order_id) is not int or order_id <= 0
            or type(through_transfer_id) is not int or through_transfer_id < 0):
        raise PointsFundingPlanError('转单历史资金核对尚未开放或参数无效')
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if not order:
        raise PointsFundingPlanError('订单不存在')
    if ((permits(phase='refund') and not permits(order, phase='refund'))
            or (permits(phase='transfer') and not permits(order, phase='transfer'))):
        raise PointsFundingPlanError('转单历史上下文不属于当前订单')
    # This independently verifies the latest order, payment, segment history
    # and all entry commission snapshots before any past state is derived.
    current = plan_points_transfer(order.pk, preview_unstarted=True, allow_recorded=allow_recorded)
    events = list(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order)
                  .select_related('previous_segment', 'next_segment').order_by('pk'))
    if not events or (through_transfer_id and through_transfer_id not in {event.pk for event in events}):
        raise PointsFundingPlanError('转单历史版本不存在')
    incoming = {event.next_segment_id: event for event in events}
    outgoing = {event.previous_segment_id: event for event in events}
    if len(incoming) != len(events) or len(outgoing) != len(events):
        raise PointsFundingPlanError('转单历史存在重复关联')
    previous_time = None
    for event in events:
        parent = incoming.get(event.previous_segment_id)
        if ((parent and parent.pk >= event.pk)
                or (previous_time is not None and event.created_at < previous_time)):
            raise PointsFundingPlanError('转单历史顺序不一致')
        previous_time = event.created_at
    if through_transfer_id:
        boundary = next(event for event in events if event.pk == through_transfer_id)
        if any(event.pk > boundary.pk and event.created_at == boundary.created_at for event in events):
            # One transfer request may atomically change multiple slots at the
            # same timestamp; an intermediate state was never committed.
            raise PointsFundingPlanError('请选择整次转单完成后的历史版本')
    snapshots = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order)
                     .select_related('segment').order_by('segment_id'))
    basis_rows = {item.transfer_id: item for item in OrderSegmentTransferBasis.objects.filter(transfer__in=events)}
    shares, lines = [], []
    for snap in snapshots:
        segment = snap.segment
        arrived = incoming.get(segment.pk)
        if arrived and arrived.pk > through_transfer_id:
            continue
        outgoing_event = outgoing.get(segment.pk)
        ended = outgoing_event is not None and outgoing_event.pk <= through_transfer_id
        if ended:
            allocation = basis_rows.get(outgoing_event.pk)
            if allocation is None:
                raise PointsFundingPlanError('转单历史成交份额缺失')
            basis = allocation.retained_basis
        elif segment.released_at and outgoing_event is None:
            # A pre-service reassignment retains no service-order earnings.
            if segment.started_at or snap.retained_basis != 0:
                raise PointsFundingPlanError('服务前改派资金依据不一致')
            basis = 0
        else:
            # Later transfers may have changed retained_basis. At this prefix
            # that transfer had not happened, so use the original entry basis.
            basis = snap.incoming_basis
        if snap.commission_type == 'FIXED':
            fee = ((snap.fixed_rule_total * basis // current.funding.deal_units) if ended
                   else 0 if segment.released_at and outgoing_event is None else snap.commission_fixed)
        else:
            fee = basis * snap.commission_rate // 100
        if fee > basis:
            raise PointsFundingPlanError('转单历史抽成超过成交份额')
        income = basis - fee
        shares.append(ProviderShare(settlement_base=basis, commission_type=snap.commission_type,
            commission_rate=snap.commission_rate, commission_fixed=fee if snap.commission_type == 'FIXED' else 0,
            provider_income=income))
        lines.append((segment.pk, segment.provider_account_id, basis, fee, income))
    receipt = OrderPointsRedemption.objects.select_for_update().get(order=order)
    sources = {item['kind']: item['total'] for item in order.funding_allocations.values('kind').annotate(total=Sum('amount'))}
    rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    funding = plan_service_funding(deal_units=receipt.deal_units, point_discount_units=receipt.discount_units,
        shares=shares, inviter_rate=rate, principal_units=sources.get('PREPAID', 0),
        gift_units=sources.get('GIFT', 0), compensation_units=sources.get('COMPENSATION', 0))
    return HistoricTransferFunding(through_transfer_id, tuple(lines), funding)
