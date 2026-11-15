"""Calculate funding for a service order with a verified points discount.

This is a calculation only: it does not reserve or transfer funds, authorize
an order, or replace the transaction and settlement state machines. Callers
must supply the actual locked price, redemption and per-person snapshots.
All amounts are integer internal units; ten units are one store coin.
"""
from dataclasses import dataclass
from collections.abc import Sequence

from .settlement import ProviderShare, compute_provider_income


class PointsFundingPlanError(ValueError):
    pass


@dataclass(frozen=True)
class PointsFundingPlan:
    deal_units: int
    point_discount_units: int
    customer_coin_units: int
    principal_units: int
    gift_units: int
    compensation_units: int
    provider_income_units: int
    gross_store_margin_units: int
    net_store_margin_units: int
    inviter_base_units: int
    inviter_income_units: int
    store_income_units: int
    additional_store_funding_units: int


def _integer(value, name, *, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise PointsFundingPlanError(f'{name}无效')
    return value


def plan_service_funding(*, deal_units, point_discount_units, shares, inviter_rate,
                         principal_units=None, gift_units=0, compensation_units=0):
    """Reconcile provider shares, referral and store top-up to actual coin.

    ``shares`` are individually verified, already-allocated deal-price
    snapshots. In particular, callers must use the correct fixed-fee policy
    and transfer/seat allocations before invoking this function; the function
    does not guess which participants or portions should receive income.
    """
    deal = _integer(deal_units, '成交金额', minimum=10)
    discount = _integer(point_discount_units, '积分抵扣等值')
    rate = _integer(inviter_rate, '推荐佣金比例', maximum=100)
    if deal % 10 or discount % 10 or discount > deal - 10:
        raise PointsFundingPlanError('成交和积分抵扣须为整数店币且至少支付一店币')
    if not isinstance(shares, Sequence) or isinstance(shares, (str, bytes)) or not shares:
        raise PointsFundingPlanError('缺少已核实的陪玩收益份额')
    total_base = total_income = 0
    for share in shares:
        if not isinstance(share, ProviderShare):
            raise PointsFundingPlanError('陪玩收益份额格式无效')
        base = _integer(share.settlement_base, '陪玩成交份额')
        income = _integer(share.provider_income, '陪玩收益')
        share_rate = _integer(share.commission_rate, '陪玩抽成比例', maximum=100)
        fixed = _integer(share.commission_fixed, '陪玩固定抽成')
        if (share.commission_type not in ('PERCENT', 'FIXED')
                or (share.commission_type == 'PERCENT' and fixed != 0)
                or (share.commission_type == 'FIXED' and (share_rate != 0 or fixed > base))
                or income != compute_provider_income(base, share.commission_type, share_rate, fixed)):
            raise PointsFundingPlanError('陪玩抽成快照与收益不一致')
        total_base += base
        total_income += income
    if total_base != deal or total_income > deal:
        raise PointsFundingPlanError('陪玩成交份额与订单成交额不一致')
    customer_coin = deal - discount
    gift = _integer(gift_units, '赠送余额支付')
    compensation = _integer(compensation_units, '赔付余额支付')
    principal = customer_coin - gift - compensation if principal_units is None else _integer(principal_units, '本金支付')
    if (principal < 0 or any(value % 10 for value in (principal, gift, compensation))
            or principal + gift + compensation != customer_coin):
        raise PointsFundingPlanError('订单支付来源与实付金额不一致')
    gross_margin = deal - total_income
    net_margin = gross_margin - discount
    inviter_base = customer_coin
    inviter_income = inviter_base * rate // 100
    # Gift and compensation count as paid coins for referral, but the store
    # must fund them because they are not incoming cash.
    cash_margin = principal - total_income - inviter_income
    store_income = max(cash_margin, 0)
    required_funding = max(-cash_margin, 0)
    if principal + required_funding != total_income + inviter_income + store_income:
        raise PointsFundingPlanError('资金计算不守恒')
    return PointsFundingPlan(deal, discount, customer_coin, principal, gift, compensation, total_income, gross_margin, net_margin,
                             inviter_base, inviter_income, store_income, required_funding)


def plan_points_funding(**kwargs):
    """Backward-compatible name for points-enabled service orders."""
    return plan_service_funding(**kwargs)


def plan_verified_order(order_id, *, allow_finalized=False):
    """Recompute an untransferred points order from locked, persisted facts.

    This is a read-only funding plan, not an allocation or payout. Caller must
    hold a tenant transaction and keep it open through any later use. Orders
    with refunds, transfers or previously released shares need their own
    full reconciliation and are refused rather than estimated.
    """
    from rest_framework.exceptions import ValidationError
    from stores.db import transaction
    from .models import (Order, OrderOriginalAttribution, OrderOriginalShareTransfer,
                         OrderPointsRedemption, OrderProvider, OrderSegmentFinancialCoverage,
                         OrderSegmentFinancialSnapshot)
    from .segment_financial_capture import _points_shares
    from .team_rules import required_team_size
    if not transaction.get_connection().in_atomic_block:
        raise PointsFundingPlanError('补资测算必须在门店事务内执行')
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if (not order or order.status not in (Order.Status.GRABBED, Order.Status.IN_SERVICE, Order.Status.COMPLETED)
            or order.payment_status != Order.PaymentStatus.PAID or (order.settled_at and not allow_finalized)):
        raise PointsFundingPlanError('订单状态不支持补资测算')
    if order.settled_at:
        from .models import OrderPointsSettlement
        if not OrderPointsSettlement.objects.filter(order=order, finalized_at=order.settled_at).exists():
            raise PointsFundingPlanError('积分订单结算记录待核对')
    attribution = OrderOriginalAttribution.objects.select_for_update().filter(order=order).first()
    coverage = OrderSegmentFinancialCoverage.objects.select_for_update().filter(attribution=attribution).first() if attribution else None
    if (not attribution or attribution.status != 'ACTIVE' or not coverage or coverage.status != 'ACTIVE'
            or coverage.source != 'WALLET_POINTS' or order.refund_records.exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution=attribution).exists()):
        raise PointsFundingPlanError('订单履约或退款记录尚待核对')
    from .models import OrderPrestartHandoff
    if OrderPrestartHandoff.objects.filter(order=order).exists():
        from .points_prestart_funding import plan_prestart_order
        return plan_prestart_order(order, attribution, coverage)
    segments = list(attribution.segments.select_for_update().order_by('slot'))
    assignments = list(OrderProvider.objects.select_for_update().filter(order=order, released_at__isnull=True).order_by('pk'))
    if (len(segments) != required_team_size(order) or len(assignments) != len(segments)
            or any(segment.released_at for segment in segments)
            or len({segment.provider_account_id for segment in segments}) != len(segments)
            or {segment.provider_account_id for segment in segments} != {row.provider_account_id for row in assignments}):
        raise PointsFundingPlanError('订单陪玩及份额待核对')
    try:
        shares = _points_shares(order, coverage, assignments, segments)
    except (ValueError, TypeError, ValidationError, KeyError) as exc:
        raise PointsFundingPlanError('积分付款或陪玩抽成待核对') from exc
    snapshots = {row.segment_id: row for row in OrderSegmentFinancialSnapshot.objects.select_for_update()
                 .filter(segment__attribution=attribution)}
    if len(snapshots) != len(segments):
        raise PointsFundingPlanError('陪玩收益快照不完整')
    by_account = {row.provider_account_id: row for row in assignments}
    for segment in segments:
        snapshot, share, assignment = snapshots.get(segment.pk), shares[segment.provider_account_id], by_account[segment.provider_account_id]
        if (not snapshot or snapshot.retained_basis is not None
                or (snapshot.incoming_basis, snapshot.commission_type, snapshot.commission_rate,
                    snapshot.commission_fixed, snapshot.income_at_entry)
                != (share.settlement_base, share.commission_type, share.commission_rate,
                    share.commission_fixed, share.provider_income)
                or snapshot.game_commission_snapshot != assignment.game_commission_snapshot
                or snapshot.commission_source != order.commission_source
                or snapshot.fixed_rule_total != (order.commission_fixed if share.commission_type == 'FIXED' else None)):
            raise PointsFundingPlanError('陪玩收益快照不一致')
    receipt = OrderPointsRedemption.objects.get(order=order)
    rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    from django.db.models import Sum
    sources = {row['kind']: row['total'] for row in order.funding_allocations.values('kind').annotate(total=Sum('amount'))}
    return plan_points_funding(deal_units=receipt.deal_units, point_discount_units=receipt.discount_units,
                               shares=[shares[segment.provider_account_id] for segment in segments], inviter_rate=rate,
                               principal_units=sources.get('PREPAID', 0), gift_units=sources.get('GIFT', 0),
                               compensation_units=sources.get('COMPENSATION', 0))


def verified_person_income(order_id, account_id, *, allow_finalized=False):
    """Return one assigned provider's verified estimate, never a paid receipt."""
    from club_accounts.models import LegacyAccountMap
    from stores.db import transaction
    from .models import OrderProvider, OrderSegmentFinancialSnapshot
    if type(account_id) is not int or account_id <= 0:
        raise PointsFundingPlanError('陪玩账号无效')
    with transaction.atomic():
        plan_verified_order(order_id, allow_finalized=allow_finalized)
        assignment = OrderProvider.objects.filter(order_id=order_id, provider_account_id=account_id,
                                                  released_at__isnull=True).first()
        if (not assignment or not assignment.provider_id or not LegacyAccountMap.objects.filter(
                account_id=account_id, legacy_user_id=assignment.provider_id).exists()):
            raise PointsFundingPlanError('陪玩身份待核对')
        snapshot = OrderSegmentFinancialSnapshot.objects.filter(
            segment__attribution__order_id=order_id, segment__provider_account_id=account_id,
            segment__released_at__isnull=True).first()
        if not snapshot:
            raise PointsFundingPlanError('陪玩收益快照待核对')
        return snapshot.income_at_entry
