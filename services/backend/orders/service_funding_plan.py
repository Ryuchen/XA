"""Verified cash-source funding plan for ordinary service orders."""
from club_accounts.models import LegacyAccountMap
from engagement.funding_wallet import validate_order_funding_sources
from engagement.models import OrderFundingAllocation, OrderPointAllocation
from rest_framework.exceptions import ValidationError
from stores.db import transaction
from wallet.models import Transaction
from .models import Order, OrderProvider, OrderStatusLog
from .points_funding_plan import PointsFundingPlanError, plan_service_funding
from .settlement import ProviderShare
from django.conf import settings


def plan_verified_ordinary_order(order_id, *, allow_finalized=False, _allow_ordinary_transfer=False):
    """Lock and verify an unrefunded, untransferred order; never move money.

    Every payment source must be recorded; an unknown source cannot safely be
    treated as principal when determining the store's funding obligation.
    """
    if not transaction.get_connection().in_atomic_block:
        raise PointsFundingPlanError('服务订单补资测算必须在门店事务内执行')
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if (not order or order.is_gift_snapshot or order.source_order_id
            or order.status not in (Order.Status.GRABBED, Order.Status.IN_SERVICE, Order.Status.COMPLETED)
            or order.payment_status != Order.PaymentStatus.PAID or not order.customer_account_id
            or order.refund_records.exists()):
        raise PointsFundingPlanError('订单状态、加购、退款或转单记录须先核对')
    transferred = order.status_logs.filter(action=OrderStatusLog.Action.TRANSFER).exists()
    if transferred and not (_allow_ordinary_transfer or getattr(settings, 'SERVICE_ORDINARY_TRANSFER_FUNDING_TESTING', False)):
        from .ordinary_transfer_capability import permits as ordinary_transfer_permits
        if not ordinary_transfer_permits(order, allow_addon_refund=True, allow_addon_handover=True):
            raise PointsFundingPlanError('普通转单补资尚未开放')
    if order.settled_at:
        from .models import OrderPointsSettlement
        from .models import OrderSegmentSettlementManifest
        finalized = (OrderPointsSettlement.objects.filter(order=order, redemption__isnull=True,
                     finalized_at=order.settled_at).exists() or (transferred and
                     OrderSegmentSettlementManifest.objects.filter(order=order, finalized_at=order.settled_at).exists()))
        if not allow_finalized or not finalized:
            raise PointsFundingPlanError('订单已经结算，请先核对')
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order):
        raise PointsFundingPlanError('积分订单必须使用积分结算口径')
    if order.customer_id and not LegacyAccountMap.objects.filter(
            account_id=order.customer_account_id, legacy_user_id=order.customer_id).exists():
        raise PointsFundingPlanError('老板账号归属待核对')
    payments = list(Transaction.objects.select_for_update().filter(order=order, tx_type=Transaction.TxType.PAY,
                                                                   status=Transaction.Status.SUCCESS).select_related('wallet'))
    if (len(payments) != 1 or payments[0].wallet.account_id != order.customer_account_id
            or payments[0].wallet.user_id != order.customer_id or payments[0].amount != -order.amount
            or payments[0].reversal_of_id or payments[0].reversals.exists()
            or payments[0].balance_after != payments[0].balance_before + payments[0].amount):
        raise PointsFundingPlanError('服务订单付款流水待核对')
    if transferred:
        from .models import OrderSegmentFinancialSnapshot
        from .segment_settlement_plan import build_segment_settlement_plan
        from .settlement import SettlementError
        try:
            segment_plan = build_segment_settlement_plan(order, allow_recorded=True)
        except SettlementError as exc:
            raise PointsFundingPlanError('转单逐段收益待核对') from exc
        snapshots = {row.segment_id: row for row in OrderSegmentFinancialSnapshot.objects.select_for_update()
                     .filter(segment__attribution__order=order)}
        shares = []
        for line in segment_plan.segments:
            snapshot = snapshots.get(line.segment_id)
            if not snapshot:
                raise PointsFundingPlanError('转单收益快照缺失')
            shares.append(ProviderShare(line.basis, snapshot.commission_type, snapshot.commission_rate,
                line.fee if snapshot.commission_type == 'FIXED' else 0, line.income))
    else:
        from .models import OrderPrestartHandoff
        has_prestart = OrderPrestartHandoff.objects.filter(order=order).exists()
        if has_prestart:
            from .ordinary_prestart_addon import supports
            if not supports(order):
                raise PointsFundingPlanError('服务前改派的陪玩及抽成快照待核对')
        elif order.providers.filter(released_at__isnull=False).exists():
            raise PointsFundingPlanError('订单存在未核对的历史陪玩份额')
        assignments = list(OrderProvider.objects.select_for_update().filter(order=order,
            released_at__isnull=True).order_by('pk'))
        if (len(assignments) != order.required_provider_count or any(row.released_at or row.settled_at
                or not row.provider_id or not row.provider_account_id for row in assignments)
                or len({row.provider_account_id for row in assignments}) != len(assignments)):
            raise PointsFundingPlanError('订单陪玩或履约份额待核对')
        if any(not LegacyAccountMap.objects.filter(account_id=row.provider_account_id, legacy_user_id=row.provider_id).exists()
               for row in assignments):
            raise PointsFundingPlanError('陪玩账号归属待核对')
        shares = [ProviderShare(row.settlement_base, row.commission_type, row.commission_rate,
                                row.commission_fixed, row.provider_income) for row in assignments]
        if sum(row.provider_income for row in assignments) != order.provider_income:
            raise PointsFundingPlanError('订单和陪玩收益快照不一致')

    sources = list(OrderFundingAllocation.objects.select_for_update().select_related('lot').filter(order=order))
    legacy = list(OrderPointAllocation.objects.select_for_update().select_related('lot').filter(order=order))
    principal = gift = compensation = 0
    if sources:
        if legacy:
            raise PointsFundingPlanError('订单存在两套付款来源，须先核对')
        try:
            validate_order_funding_sources(order, sources)
        except ValidationError as exc:
            raise PointsFundingPlanError('订单付款来源待核对') from exc
        if any(row.refunded_amount for row in sources) or sum(row.amount for row in sources) != order.amount:
            raise PointsFundingPlanError('订单付款来源金额待核对')
        principal = sum(row.amount for row in sources if row.kind == 'PREPAID')
        gift = sum(row.amount for row in sources if row.kind == 'GIFT')
        compensation = sum(row.amount for row in sources if row.kind == 'COMPENSATION')
    else:
        raise PointsFundingPlanError('订单缺少完整付款来源，请先核对')
    rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    plan = plan_service_funding(deal_units=order.amount, point_discount_units=0, shares=shares, inviter_rate=rate,
                                principal_units=principal, gift_units=gift, compensation_units=compensation)
    if not transferred and (plan.inviter_income_units != order.inviter_commission
            or order.shop_income != max(order.amount - order.provider_income - order.inviter_commission, 0)):
        raise PointsFundingPlanError('订单既有分账快照待核对')
    return plan
