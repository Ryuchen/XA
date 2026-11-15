"""Isolated, read-only reconciliation for a negotiated ordinary transfer refund.

The result is not a refund authorization: no proposal or money endpoint uses
this helper until the corresponding posting and settlement path is complete.
"""
from collections import defaultdict
from dataclasses import asdict
import os

from django.conf import settings
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import AfterSale, Order, OrderOriginalShareTransfer
from .refund_math import RefundCalculationError, provider_refund_burden
from .segment_settlement_plan import build_segment_settlement_plan
from .settlement import SettlementError


def _require_isolated():
    from .ordinary_transfer_capability import enabled as production_enabled
    if production_enabled():
        return
    database = transaction.get_connection().settings_dict
    if (not getattr(settings, 'ORDINARY_TRANSFER_REFUND_PREVIEW_INTERNAL_TESTING', False)
            or os.environ.get('DJANGO_SETTINGS_MODULE') not in ('config.test_settings', 'config.migration_test_settings',
                                                               'config.multitenant_test_settings')
            or database.get('ENGINE') != 'django.db.backends.sqlite3'
            or database.get('NAME') not in (':memory:', 'file:memorydb_default?mode=memory&cache=shared',
                                            'file:memorydb_store_b?mode=memory&cache=shared')):
        raise ValidationError('普通转单协商退款核对尚未开放')


def agreement_testing_available(order):
    from .ordinary_transfer_capability import enabled as production_enabled
    if not (production_enabled() or getattr(settings, 'ORDINARY_TRANSFER_REFUND_AGREEMENT_INTERNAL_TESTING', False)):
        return False
    try:
        _require_isolated()
    except ValidationError:
        return False
    return (not order.refund_records.exists() and OrderOriginalShareTransfer.objects.filter(
        previous_segment__attribution__order=order).exists())


def preview_locked(case_id, refund_units):
    _require_isolated()
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('转单协商核对须在门店事务内执行')
    order_id = AfterSale.objects.filter(pk=case_id).values_list('order_id', flat=True).first()
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    case = AfterSale.objects.select_for_update().filter(pk=case_id, order=order).first()
    if not order or not case:
        raise ValidationError('售后记录不存在')
    from .financial_lock import lock_order_money
    lock_order_money()
    from .ordinary_transfer_capability import enabled as production_enabled, permits as production_permits
    if production_enabled() and not production_permits(order):
        raise ValidationError('该转单退款资金组合尚未开放，请联系客服核对')
    from .points_service_payment import needs_integrated_processing
    from .models import OrderSegmentPersonSettlement
    from engagement.confirmation import aftersale_deadline
    from django.utils import timezone
    paid_people = OrderSegmentPersonSettlement.objects.filter(manifest__order=order).exists()
    deadline = aftersale_deadline(order) if order.status == Order.Status.COMPLETED else None
    paid_case = bool(paid_people and order.status == Order.Status.COMPLETED and order.completed_at
        and deadline and case.created_at and order.completed_at <= case.created_at < deadline
        and case.created_at <= timezone.now() and type(refund_units) is int and refund_units >= 0)
    if ((order.status != Order.Status.IN_SERVICE and not paid_case) or order.payment_status != Order.PaymentStatus.PAID
            or case.status != 'OPEN' or case.account_id != order.customer_account_id
            or not case.client_request_id or order.is_gift_snapshot or order.source_order_id
            or Order.objects.filter(source_order_id=order.pk, is_gift_snapshot=False).exists()
            or needs_integrated_processing(order) or order.refund_records.exists() or order.settled_at
            or order.consumption_confirmed_at or (not paid_case and order.providers.filter(settled_at__isnull=False).exists())
            or (not paid_case and order.transactions.filter(status='SUCCESS', tx_type__in=['INCOME', 'SHOP_INCOME', 'GIFT']).exists())
            or not OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        raise ValidationError('订单或售后不属于可核对的普通转单范围')
    if type(refund_units) is not int or not 0 <= refund_units <= order.amount or refund_units % 10:
        raise ValidationError('退款金额须为不超过实付的非负整数店币')
    scope = case.affected_provider_account_ids
    if (not isinstance(scope, list) or not scope or any(type(pk) is not int or pk <= 0 for pk in scope)
            or len(scope) != len(set(scope)) or order.customer_account_id in scope):
        raise ValidationError('受影响人员范围待核对')
    payments = list(order.transactions.select_for_update().filter(tx_type='PAY', status='SUCCESS',
                    reversal_of__isnull=True).select_related('wallet'))
    if (len(payments) != 1 or payments[0].amount != -order.amount or payments[0].reversals.exists()
            or payments[0].wallet.account_id != order.customer_account_id
            or payments[0].balance_after != payments[0].balance_before + payments[0].amount):
        raise ValidationError('原付款流水待核对')
    if order.funding_terms is None:
        raise ValidationError('原付款资金来源待核对')
    from engagement.funding_wallet import validate_order_funding_sources
    allocations = list(order.funding_allocations.select_for_update().select_related('lot').order_by('pk'))
    if (not allocations or sum(row.amount for row in allocations) != order.amount
            or any(row.refunded_amount for row in allocations)):
        raise ValidationError('原付款资金来源待核对')
    validate_order_funding_sources(order, allocations)
    try:
        plan = build_segment_settlement_plan(order, allow_recorded=True)
    except SettlementError as exc:
        raise ValidationError('原付款或逐段收益待核对') from exc
    from .ordinary_first_paid_settlement import capture_locked
    paid_snapshot = capture_locked(order, plan, scope)
    if paid_case and not paid_snapshot:
        raise ValidationError('已结算陪玩流水待核对')
    segments = list(order.original_attribution.segments.select_for_update().order_by('pk'))
    selected = case.affected_segment_ids
    if (not isinstance(selected, list) or not selected or len(selected) != len(set(selected))
            or any(type(pk) is not int or pk <= 0 for pk in selected)):
        raise ValidationError('请选择本次协商涉及的履约段')
    chosen = {segment.pk: segment for segment in segments if segment.pk in selected and segment.started_at}
    if set(chosen) != set(selected) or {segment.provider_account_id for segment in chosen.values()} != set(scope):
        raise ValidationError('所选履约段与受影响陪玩不一致')
    weights, incomes = defaultdict(int), defaultdict(int)
    for segment in chosen.values():
        weights[segment.provider_account_id] += segment.original_share
    for line in plan.segments:
        if line.segment_id in chosen:
            incomes[line.account_id] += line.income
    from .addon_main_deduction import deductions_at
    from .addon_main_segment_deduction import verified_deductions
    prior_addon_deductions = deductions_at(order)
    per_segment = verified_deductions(order)
    selected_addon_deductions = defaultdict(int)
    for segment_id, segment in chosen.items():
        selected_addon_deductions[segment.provider_account_id] += per_segment.get(segment_id, 0)
    for account, amount in selected_addon_deductions.items():
        incomes[account] -= amount
    if any(incomes[account] < 0 or not weights[account] for account in scope):
        raise ValidationError('所选履约段的收益或原价份额待核对')
    if refund_units:
        try:
            burden = provider_refund_burden(refund_equivalent_units=refund_units,
                                            original_price_weights={pk: weights[pk] for pk in scope})
        except RefundCalculationError as exc:
            raise ValidationError(str(exc)) from exc
        burdens, rounding = burden.amounts, burden.rounding_to_store_units
    else:
        burdens, rounding = {pk: 0 for pk in scope}, 0
    terms = []
    for pk in sorted(scope):
        original, amount = incomes[pk], burdens[pk]
        terms.append({'account_id': pk, 'original_income_units': original, 'burden_units': amount,
                      'retained_units': max(original - amount, 0), 'additional_units': max(amount - original, 0)})
    line_by_segment = {line.segment_id: line for line in plan.segments}
    ordered = sorted(chosen.values(), key=lambda segment: (segment.accepted_at, segment.pk), reverse=True)
    segment_pending_plan = []
    for term in terms:
        remaining = min(term['burden_units'], term['original_income_units'])
        for segment in ordered:
            if segment.provider_account_id != term['account_id']:
                continue
            available = line_by_segment[segment.pk].income - per_segment.get(segment.pk, 0)
            if available < 0:
                raise ValidationError('本人履约段已扣收益异常')
            take = min(remaining, available)
            if take:
                segment_pending_plan.append({'account_id': term['account_id'], 'segment_id': segment.pk, 'amount': take})
                remaining -= take
            if not remaining:
                break
        if remaining:
            raise ValidationError('本人所选履约段待结收益不足')
    # Informational only. Posting must lock and recalculate all balances.
    from .negotiation_funding import funding_preview
    recovery = funding_preview(order, terms)
    from engagement.funding_plan import FundingPlanError, RefundablePart, plan_refund
    try:
        by_id = plan_refund([RefundablePart(row.pk, row.kind, row.amount) for row in allocations], refund_units)
    except FundingPlanError as exc:
        raise ValidationError('原付款退款来源待核对') from exc
    sources = defaultdict(int)
    paid_sources = defaultdict(int)
    for row in allocations:
        sources[row.kind] += by_id[row.pk]
        paid_sources[row.kind] += row.amount
    from .points_negotiation_preview import funding_after_refund
    rows = recovery['providers']
    allocated = 0
    from .ordinary_transfer_capability import enabled as production_enabled
    if production_enabled():
        from .points_funding_evidence import _verified_allocations_for_locked_order
        from .addon_funding_release import addon_release_totals
        from .points_funding_plan import PointsFundingPlanError
        try:
            funding_allocations = _verified_allocations_for_locked_order(order)
            released = addon_release_totals(funding_allocations)
            allocated = sum(row.amount - released[row.pk] for row in funding_allocations)
        except (PointsFundingPlanError, ValidationError) as exc:
            raise ValidationError('原门店补资分配待核对') from exc
    result_funding = funding_after_refund(
        principal_units=paid_sources['PREPAID'], gift_units=paid_sources['GIFT'],
        compensation_units=paid_sources['COMPENSATION'], returned_prepaid_units=sources['PREPAID'],
        returned_gift_units=sources['GIFT'], returned_compensation_units=sources['COMPENSATION'],
        provider_income_units=sum(line.income for line in plan.segments),
        pending_deduction_units=sum(row['pending_income_units'] for row in rows),
        provider_external_units=sum(row['wallet_units'] + row['deposit_units'] + row['shortfall_units'] for row in rows),
        inviter_rate=order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0,
        allocated_units=allocated)
    if paid_case and order.addon_intents.filter(payment__isnull=False).exists():
        from .addon_funding_history import combined_at
        from .refunded_addon_funding import combine_refunded_history
        from .points_funding_plan import PointsFundingPlanError
        retained = defaultdict(int)
        for line in plan.segments:
            retained[line.account_id] += line.income
        for row in rows:
            retained[row['account_id']] -= row['pending_income_units']
        try:
            history = combined_at(order, result_funding, timezone.now(), segment_shares=[
                (line.segment_id, line.account_id, line.basis, line.fee, line.income)
                for line in plan.segments])
            result_funding = combine_refunded_history(result_funding, history, main_retained_by_account=retained)
        except (PointsFundingPlanError, ValidationError) as exc:
            raise ValidationError('已付款加购及整单补资预览待核对') from exc
    if production_enabled():
        from dataclasses import replace
        addon_rounding = (sum(order.addon_refunds.values_list('rounding_surplus', flat=True))
                          if paid_case else 0)
        extra = max(rounding + addon_rounding - result_funding.store_income_units, 0)
        if extra:
            required = result_funding.required_units + extra
            result_funding = replace(result_funding, store_income_units=result_funding.store_income_units + extra,
                required_units=required, release_units=max(allocated - required, 0),
                remaining_units=max(required - allocated, 0))
    return {'order_id': order.pk, 'case_id': case.pk, 'refund_units': refund_units,
            'provider_terms': terms, 'rounding_to_store_units': rounding,
            'segment_pending_plan': segment_pending_plan,
            'ordinary_paid_settlement_snapshot': paid_snapshot,
            'prior_addon_deductions': dict(prior_addon_deductions),
            'selected_segment_ids': sorted(selected),
            'selected_addon_deductions': dict(selected_addon_deductions),
            'provider_funding_preview': recovery,
            'return_sources': {key: sources[key] for key in ('PREPAID', 'GIFT', 'COMPENSATION')},
            'funding_if_recovered': asdict(result_funding)}
