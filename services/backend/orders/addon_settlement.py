"""Settlement of paid add-ons with their service order's funding and clocks."""
from django.conf import settings
from rest_framework.exceptions import ValidationError

from .addon_funding import CombinedAddonFunding, include_paid_addons
from .models import OrderAddonPayment, OrderAddonSettlement
from .points_funding_plan import PointsFundingPlanError
from .settlement import SettlementError
from wallet.models import Transaction


def enabled(order=None):
    from .ordinary_addon_production import eligible as ordinary_addon_eligible
    from .negotiation_zero import ordinary_funding_enabled
    from .points_business_access import permits
    from .ordinary_transfer_capability import permits as ordinary_transfer_permits
    from .ordinary_funding_continuation import addon_settlement_active
    return bool((order is not None and any(permits(order, phase=phase, kind='addon') for phase in ('funding', 'settlement')))
                or (order is not None and permits(order, phase='settlement', kind='transfer'))
                or getattr(settings, 'ADDON_SETTLEMENT_INTERNAL_TESTING', False)
                or (order is not None and addon_settlement_active(order))
                or (order is not None and (ordinary_addon_eligible(order) or ordinary_funding_enabled(order)
                    or ordinary_transfer_permits(order, allow_addon_refund=True, allow_addon_handover=True))))


def has_paid_addons(order):
    return OrderAddonPayment.objects.filter(intent__order=order).exists()


def combined_terms(order, main_plan):
    if not has_paid_addons(order):
        return None
    if not enabled(order):
        raise SettlementError('加购收益结算尚未开放')
    try:
        combined = include_paid_addons(order, main_plan)
    except (PointsFundingPlanError, ValidationError) as exc:
        raise SettlementError(str(exc)) from exc
    if not isinstance(combined, CombinedAddonFunding):
        raise SettlementError('加购资金计划不存在')
    payments = OrderAddonPayment.objects.filter(pk__in=combined.addon_payment_ids).select_related('intent__segment__attribution')
    if any(payment.intent.segment.attribution.order_id != order.pk for payment in payments):
        raise SettlementError('加购履约归属不属于主单')
    if any(payment.inviter_id != order.inviter_account_id for payment in payments):
        raise SettlementError('加购推荐人归属待核对')
    return combined


def settlement_facts(main, combined):
    """All cash sources and destinations, in settlement field order."""
    if combined is None:
        return (main.customer_coin_units, main.principal_units, 0, main.gift_units,
                main.compensation_units, main.additional_store_funding_units,
                main.provider_income_units, main.inviter_income_units, main.store_income_units)
    return (main.customer_coin_units + combined.addon_paid_units,
            main.principal_units + combined.addon_principal_units,
            combined.addon_provider_recovery_units,
            main.gift_units + combined.addon_gift_units,
            main.compensation_units + combined.addon_compensation_units,
            combined.additional_store_funding_units,
            main.provider_income_units - sum(amount for _, amount in combined.main_deductions)
                + combined.addon_provider_income_units,
            main.inviter_income_units + combined.addon_inviter_income_units,
            combined.store_income_units)


def verify_addon_records(order, settlement, parts, persons, *, segment_ids=None):
    """Already posted earnings must still equal the current payment facts."""
    expected = {(part.payment_id, part.segment_id): part for part in parts}
    query = settlement.addon_settlements.select_for_update().select_related(
        'payment__intent', 'report', 'income_transaction__wallet')
    if segment_ids is not None:
        query = query.filter(segment_id__in=segment_ids)
    rows = list(query)
    main_by_report = {person.report_id: person for person in persons}
    for row in rows:
        part, tx = expected.get((row.payment_id, row.segment_id)), row.income_transaction
        if (not part or row.amount != part.provider_income_units or row.report_id not in main_by_report
                or row.report.order_id != order.pk or row.report.provider_account_id != part.provider_account_id
                or main_by_report[row.report_id].segment_id != part.segment_id):
            raise SettlementError('已结算加购收益已变化，请核对')
        if row.amount:
            if (not tx or tx.order_id != order.pk or tx.wallet.account_id != part.provider_account_id
                    or tx.tx_type != Transaction.TxType.INCOME or tx.status != Transaction.Status.SUCCESS
                    or tx.amount != row.amount or tx.balance_after != tx.balance_before + tx.amount):
                raise SettlementError('加购收益流水待核对')
        elif tx:
            raise SettlementError('零收益加购不应产生收入流水')
    return {(row.payment_id, row.segment_id) for row in rows}


def settle_person_addons(order, settlement, parts, *, snapshot, assignment, report, profile, now, trace_id):
    """Pay the person's add-ons only after the same report and clocks mature."""
    from .services import _credit_wallet
    from wallet.income import hold_provider_income
    settled = set(settlement.addon_settlements.values_list('payment_id', 'segment_id'))
    for part in parts:
        if part.provider_account_id != assignment.provider_account_id or (part.payment_id, part.segment_id) in settled:
            continue
        if part.segment_id != snapshot.segment_id:
            raise SettlementError('加购履约段与陪玩报单不一致')
        income = _credit_wallet(order=order, amount=part.provider_income_units, tx_type=Transaction.TxType.INCOME,
            remark=f'订单收入：{order.order_no} 加购 {part.payment_id}', event='order.addon_settle.provider',
            trace_id=trace_id, user_id=assignment.provider_id, account_id=assignment.provider_account_id,
            log_after_commit=True)
        if income:
            hold_provider_income(income, profile=profile, hold_hours=0, deposit_percent=order.service_deposit_percent,
                                 apply_without_hold=True)
        from .models import OrderPointsRefundSettlement
        parent = ({'refund_settlement': settlement} if isinstance(settlement, OrderPointsRefundSettlement)
                  else {'order_settlement': settlement})
        OrderAddonSettlement.objects.create(payment_id=part.payment_id, segment_id=part.segment_id, **parent,
            report=report, amount=part.provider_income_units, income_transaction=income, settled_at=now)
