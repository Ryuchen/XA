"""Combine a service order and its paid add-ons for shared store funding.

The existing platform receipt is allocated to an entire service order.  A
positive balance on one part may cover a shortfall on another, but does not
change any participant's individual commission calculation.  This calculation
does not release income; add-on settlement still has its own separate gate.
"""
from collections import defaultdict
from dataclasses import dataclass

from django.conf import settings
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import OrderAddonPayment
from .points_funding_plan import PointsFundingPlanError


@dataclass(frozen=True)
class AddonFundingPart:
    payment_id: int
    provider_account_id: int
    segment_id: int
    provider_income_units: int


@dataclass(frozen=True)
class CombinedAddonFunding:
    main: object
    addon_payment_ids: tuple
    addon_parts: tuple
    addon_paid_units: int
    addon_principal_units: int
    addon_gift_units: int
    addon_compensation_units: int
    addon_provider_income_units: int
    addon_inviter_income_units: int
    addon_provider_recovery_units: int
    main_deductions: tuple
    store_income_units: int
    additional_store_funding_units: int


def include_paid_addons(order, main_plan, *, allow_unassigned=False, allow_open_aftersale=False):
    """Add verified purchases and their actual refund recoveries to the main plan."""
    from .addon_payment import payment_table_exists, verify_order_debit_links, verify_receipt
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('整单补资必须在门店事务内计算')
    available = payment_table_exists()
    verify_order_debit_links(order, table_exists=available, lock=True)
    if not available:
        return main_plan
    payments = list(OrderAddonPayment.objects.select_for_update().filter(intent__order=order)
                    .select_related('intent', 'buyer_transaction__wallet').order_by('pk'))
    if not payments:
        return main_plan
    from .ordinary_addon_production import eligible as ordinary_addon_eligible
    from .points_business_access import permits
    formal = any(permits(order, phase=phase, kind='addon')
                 for phase in ('payment', 'handover', 'funding', 'refund', 'settlement'))
    from .ordinary_funding_continuation import addon_settlement_active
    formal = formal or addon_settlement_active(order)
    if not formal and not order.refund_records.exists():
        formal = any(permits(order, phase=phase, kind='transfer')
                     for phase in ('transfer', 'report', 'refund', 'settlement'))
    if not formal and not order.refund_records.exists():
        from .ordinary_transfer_capability import permits as ordinary_transfer_permits
        formal = ordinary_transfer_permits(order, allow_addon_refund=True, allow_addon_handover=True)
    if not (formal or getattr(settings, 'ADDON_ORDER_FUNDING_INTERNAL_TESTING', False)
            or ordinary_addon_eligible(order, allow_open=allow_open_aftersale)):
        raise PointsFundingPlanError('加购整单补资尚未开放')
    if order.refund_records.exists():
        raise PointsFundingPlanError('主单退款后的加购补资尚未接通')
    try:
        for payment in payments:
            verify_receipt(payment)
    except ValidationError as exc:
        raise PointsFundingPlanError('加购付款或退款的资金依据尚未接通') from exc
    from .addon_handover import assert_no_pending_handover, current_responsibilities
    parts = []
    paid = principal = gift = compensation = inviter_income = recovery = 0
    main_deductions = defaultdict(int)
    deduction_events = []
    for payment in payments:
        try:
            assert_no_pending_handover(payment)
            _, responsibilities = current_responsibilities(payment, lock=True)
        except ValidationError as exc:
            raise PointsFundingPlanError(str(exc)) from exc
        from .models import OrderParticipationSegment
        current_segments = [row['segment_id'] for row in responsibilities if row['is_current']]
        if not allow_unassigned and current_segments and OrderParticipationSegment.objects.filter(
                pk__in=current_segments, released_at__isnull=False).exists():
            raise PointsFundingPlanError('主单陪玩已转出，请先确认加购接替')
        refunds = list(payment.refund_records.select_for_update().order_by('pk'))
        deductions = list(payment.refund_earning_deductions.select_for_update().select_related('refund'))
        if any(deduction.refund.payment.intent.order_id != order.pk for deduction in deductions):
            raise PointsFundingPlanError('加购收益扣减记录待核对')
        for refund in refunds:
            if refund.main_pending_deduction:
                for person in refund.proposal.participants.filter(main_pending_deduction__gt=0):
                    main_deductions[person.account_id] += person.main_pending_deduction
                    deduction_events.append((person.account_id, refund.created_at))
            if refund.deposit_deduction:
                from wallet.models import RefundDepositDeficit, get_platform_wallet
                from wallet.refund_deposit import _verify_direct_deficit
                deficits = list(RefundDepositDeficit.objects.select_related('profile').filter(addon_refund=refund))
                if sum(row.amount for row in deficits) != refund.deposit_deduction:
                    raise PointsFundingPlanError('加购退款押金扣减记录待核对')
                try:
                    for deficit in deficits:
                        _verify_direct_deficit(deficit, get_platform_wallet(for_update=True))
                except ValueError as exc:
                    raise PointsFundingPlanError('加购退款押金扣减记录待核对') from exc
        sources = {kind: sum(row.amount - row.refunded_amount for row in payment.allocations.filter(kind=kind))
                   for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
        net_principal = sources['PREPAID']
        net_coin = payment.intent.amount - sum(row.amount for row in refunds)
        retained = sum(row['income'] for row in responsibilities)
        if min(*sources.values(), net_coin, retained) < 0 or sum(sources.values()) != net_coin:
            raise PointsFundingPlanError('加购退款后的资金金额无效')
        if sum(row['basis'] for row in responsibilities) != net_coin:
            raise PointsFundingPlanError('加购履约份额与退款后金额不一致')
        grouped = {}
        for row in responsibilities:
            key = (row['segment_id'], row['provider_id'])
            grouped[key] = grouped.get(key, 0) + row['income']
        parts.extend(AddonFundingPart(payment.pk, account_id, segment_id, amount)
                     for (segment_id, account_id), amount in grouped.items())
        paid += net_coin
        principal += net_principal
        gift += sources['GIFT']
        compensation += sources['COMPENSATION']
        recovery += sum(row.wallet_deduction + row.deposit_deduction for row in refunds)
    provider_income = sum(part.provider_income_units for part in parts)
    if main_deductions:
        from .models import (OrderOriginalShareTransfer, OrderPointsSettlementPerson, OrderProvider,
                             OrderSegmentFinancialSnapshot, OrderSegmentPersonSettlement)
        transferred = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
        if transferred:
            from .points_service_payment import needs_integrated_processing
            from .segment_settlement_plan import build_segment_settlement_plan
            from .settlement import SettlementError
            try:
                points_order = needs_integrated_processing(order)
                plan = build_segment_settlement_plan(order, allow_recorded=True,
                    _allow_points=points_order, _preview_unstarted=points_order)
            except SettlementError as exc:
                raise PointsFundingPlanError('转单主单收益待核对') from exc
            limits = defaultdict(int)
            for line in plan.segments:
                limits[line.account_id] += line.income
            settled_at = {row.account_id: row.settled_at for row in
                          OrderSegmentPersonSettlement.objects.filter(manifest__order=order,
                              account_id__in=main_deductions)}
            assignments = {}
        else:
            snapshots = list(OrderSegmentFinancialSnapshot.objects.filter(
                segment__attribution__order=order, segment__provider_account_id__in=main_deductions,
                segment__released_at__isnull=True).select_related('segment'))
            limits = {row.segment.provider_account_id: row.income_at_entry for row in snapshots}
            assignments = {row.provider_account_id: row for row in OrderProvider.objects.filter(
                order=order, provider_account_id__in=main_deductions, released_at__isnull=True)}
            settled_at = {row.segment.provider_account_id: row.settled_at for row in
                          OrderPointsSettlementPerson.objects.filter(settlement__order=order,
                              segment__provider_account_id__in=main_deductions).select_related('segment')}
        if (not set(main_deductions).issubset(limits)
                or (not transferred and (len(snapshots) != len(main_deductions)
                                         or set(assignments) != set(main_deductions)))
                or any(amount > limits[account] for account, amount in main_deductions.items())
                or sum(main_deductions.values()) > main_plan.provider_income_units
                or any(account in assignments and assignments[account].settled_at
                       and assignments[account].settled_at <= at for account, at in deduction_events)
                or any(account in settled_at and settled_at[account] <= at for account, at in deduction_events)):
            raise PointsFundingPlanError('加购退款的主单待结算收益待核对')
    # The main order may already require funds, or may have cash remaining
    # after its own provider and inviter shares. Both belong to the same order.
    cash_margin = (main_plan.store_income_units - main_plan.additional_store_funding_units
                   + principal + recovery - provider_income - inviter_income + sum(main_deductions.values()))
    from .points_service_payment import needs_integrated_processing
    ordinary_rounding = (sum(order.addon_refunds.values_list('rounding_surplus', flat=True))
                         if not needs_integrated_processing(order) else 0)
    reserved = ordinary_rounding + getattr(main_plan, 'rounding_reserved_units', 0)
    funding = max(reserved - cash_margin, 0)
    return CombinedAddonFunding(main=main_plan, addon_payment_ids=tuple(payment.pk for payment in payments),
        addon_parts=tuple(parts), addon_paid_units=paid,
        addon_principal_units=principal, addon_gift_units=gift,
        addon_compensation_units=compensation, addon_provider_income_units=provider_income,
        addon_inviter_income_units=inviter_income, addon_provider_recovery_units=recovery,
        main_deductions=tuple(sorted(main_deductions.items())),
        store_income_units=cash_margin + funding,
        additional_store_funding_units=funding)
