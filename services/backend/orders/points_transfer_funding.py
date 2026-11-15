"""Reconcile a captured points-paid transfer against its real funding sources.

This calculation does not release funding, settle anyone, or authorize a
transfer. Those operations must hold the same tenant transaction and verify
the resulting facts again.
"""
from dataclasses import dataclass, replace

from django.db.models import Sum
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import Order, OrderPointsRedemption, OrderSegmentFinancialCoverage, OrderSegmentFinancialSnapshot
from .points_funding_allocation import RebalanceResult
from .points_funding_plan import PointsFundingPlan, PointsFundingPlanError, plan_service_funding
from .points_transfer_capture import locked_deal
from .segment_settlement_plan import SegmentSettlementPlan, build_segment_settlement_plan
from .settlement import ProviderShare, SettlementError


@dataclass(frozen=True)
class PointsTransferFunding:
    segments: SegmentSettlementPlan
    funding: PointsFundingPlan
    preview_only: bool = False


@dataclass(frozen=True)
class PointsTransferRebalancePreview:
    funding: PointsTransferFunding
    allocation: RebalanceResult


def preview_points_transfer_rebalance(order_id):
    """Calculate a proposed revision; do not alter a receipt or release funds."""
    from django.conf import settings
    from .points_funding_allocation import PointsFundingAllocationError, plan_rebalance
    from .points_funding_evidence import verified_order_allocations_locked
    from .models import OrderOriginalShareTransfer, OrderPointsFundingRelease
    if not getattr(settings, 'SERVICE_POINTS_TRANSFER_FUNDING_REVISION_TESTING', False):
        raise PointsFundingPlanError('积分转单补资重算尚未开放')
    if not transaction.get_connection().in_atomic_block or type(order_id) is not int or order_id <= 0:
        raise PointsFundingPlanError('积分转单补资重算须在门店事务内执行')
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if not order:
        raise PointsFundingPlanError('订单不存在')
    funding = plan_points_transfer(order_id, preview_unstarted=True)
    last_transfer = (OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order)
                     .order_by('-pk').first())
    if not last_transfer:
        raise PointsFundingPlanError('转单历史不存在')
    from .points_transfer_history import plan_historic_points_transfer
    history = plan_historic_points_transfer(order_id, through_transfer_id=last_transfer.pk)
    if (history.funding != funding.funding or history.segment_shares != tuple(
            (line.segment_id, line.account_id, line.basis, line.fee, line.income) for line in funding.segments.segments)):
        raise PointsFundingPlanError('当前资金测算与转单历史不一致')
    allocations = verified_order_allocations_locked(order)
    if OrderPointsFundingRelease.objects.filter(allocation__in=allocations).exists():
        raise PointsFundingPlanError('已有退款补资释放记录，须先核对')
    from .points_transfer_revision import revision_tables_exist, verify_revisions_locked
    released = verify_revisions_locked(order)[0] if revision_tables_exist() else {}
    try:
        result = plan_rebalance(allocations=[(item.pk, item.amount, released.get(item.pk, 0)) for item in allocations],
                                required=funding.funding.additional_store_funding_units)
    except PointsFundingAllocationError as exc:
        raise PointsFundingPlanError('积分转单补资分配待核对') from exc
    return PointsTransferRebalancePreview(funding, result)


def plan_points_transfer(order_id, *, now=None, allow_recorded=False, preview_unstarted=False):
    """Rebuild segment shares and post-discount funding from locked evidence."""
    if not transaction.get_connection().in_atomic_block or type(order_id) is not int or order_id <= 0:
        raise PointsFundingPlanError('积分转单资金核对须在门店事务内执行')
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if not order:
        raise PointsFundingPlanError('订单不存在')
    from .financial_lock import lock_order_money
    lock_order_money()
    coverage = OrderSegmentFinancialCoverage.objects.select_for_update().filter(attribution__order=order).first()
    try:
        # Existing allocations are proved separately by the funding verifier;
        # accepting them here is not permission to transfer again.
        deal = locked_deal(order, coverage, allow_existing_allocations=True, allow_segment_records=allow_recorded)
        segments = build_segment_settlement_plan(order, now=now, allow_recorded=allow_recorded,
            _allow_points=True, _preview_unstarted=preview_unstarted)
    except (ValidationError, SettlementError, ValueError) as exc:
        raise PointsFundingPlanError('积分转单付款或逐段收益待核对') from exc
    receipt = OrderPointsRedemption.objects.select_for_update().get(order=order)
    if deal != segments.deal_amount:
        raise PointsFundingPlanError('积分转单成交基数不一致')
    snapshots = {item.segment_id: item for item in OrderSegmentFinancialSnapshot.objects.select_for_update()
                 .filter(segment__attribution__order=order)}
    shares = []
    for line in segments.segments:
        snapshot = snapshots.get(line.segment_id)
        if not snapshot:
            raise PointsFundingPlanError('积分转单抽成快照缺失')
        shares.append(ProviderShare(settlement_base=line.basis, commission_type=snapshot.commission_type,
            commission_rate=snapshot.commission_rate, commission_fixed=line.fee if snapshot.commission_type == 'FIXED' else 0,
            provider_income=line.income))
    sources = {item['kind']: item['total'] for item in order.funding_allocations.values('kind')
               .annotate(total=Sum('amount'))}
    rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    funding = plan_service_funding(deal_units=deal, point_discount_units=receipt.discount_units, shares=shares,
        inviter_rate=rate, principal_units=sources.get('PREPAID', 0), gift_units=sources.get('GIFT', 0),
        compensation_units=sources.get('COMPENSATION', 0))
    # The generic segment plan calculates gross pre-points store shares. The
    # points view must instead expose only the recomputed net destinations.
    segments = replace(segments, inviter_income=funding.inviter_income_units, store_income=funding.store_income_units,
                       **({'persons': ()} if preview_unstarted else {}))
    return PointsTransferFunding(segments, funding, preview_only=preview_unstarted)
