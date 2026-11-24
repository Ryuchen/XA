"""Isolated reconciliation of a first refund for a transferred points order.

The output is a conditional review only: it neither authorizes a proposal nor
returns assets, debits providers, releases funding or changes any ledger.
"""
from collections import defaultdict
from dataclasses import asdict

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from club_accounts.models import LegacyAccountMap
from stores.db import transaction
from users.models import EscortProfile
from wallet.models import Transaction, Wallet
from .models import (AfterSale, Order, OrderParticipationSegment, OrderSegmentSettlementManifest,
                     OrderPointsTransferFundingRevision)
from .points_checkout_test import require_isolated_checkout, settlement_enabled
from .points_funding_allocation import PointsFundingAllocationError, plan_release
from .points_funding_evidence import verified_order_allocations_locked, verify_order_funding
from .points_funding_plan import PointsFundingPlanError
from .points_negotiation_preview import funding_after_refund
from .points_service_refund import _plan as payment_refund_plan
from .points_transfer_funding import plan_points_transfer
from .refund_math import RefundCalculationError, provider_refund_burden
from .settlement import SettlementError


def agreement_enabled():
    """The transferred-order consent path is restricted to disposable tests."""
    from .points_transfer_refund_access import permits
    if permits(phase='refund'):
        return True
    if not getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_AGREEMENT_TESTING', False):
        return False
    try:
        require_isolated_checkout()
    except ValidationError:
        return False
    from .points_negotiation_preview import agreement_enabled as base_enabled
    return bool(base_enabled() and getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_PREVIEW_TESTING', False))


def agreement_available_for_order(order):
    from .points_transfer_refund_access import permits
    if permits(phase='refund'):
        return permits(order, phase='refund')
    if not agreement_enabled():
        return False
    if getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_INTERNAL_AGREEMENT_TESTING', False):
        return True
    from .points_negotiation_termination import available_for_order
    return available_for_order(order)


def preview_locked(case_id, *, refund_equivalent_units):
    from .points_transfer_refund_access import permits, require_refund_or_isolated
    require_refund_or_isolated()
    formal = permits(phase='refund')
    if ((not formal and (not getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_PREVIEW_TESTING', False)
                         or not settlement_enabled())) or not transaction.get_connection().in_atomic_block
            or type(case_id) is not int or case_id <= 0):
        raise ValidationError('积分转单售后核对尚未开放')
    order_id = AfterSale.objects.filter(pk=case_id).values_list('order_id', flat=True).first()
    if not order_id:
        raise ValidationError('售后记录不存在')
    order = Order.objects.select_for_update().get(pk=order_id)
    if formal and not permits(order, phase='refund'):
        raise ValidationError('转单售后上下文不属于当前订单')
    case = AfterSale.objects.select_for_update().get(pk=case_id, order=order)
    from .financial_lock import lock_order_money
    lock_order_money()
    from .points_receipt_usage import prelock_transfer_refund_receipts, transfer_refund_eligible
    prelock_transfer_refund_receipts(order)
    scope = case.affected_provider_account_ids
    selected_segments = set(case.affected_segment_ids or [])
    if (case.status != 'OPEN' or order.status not in ('IN_SERVICE', 'COMPLETED')
            or order.payment_status != 'PAID' or order.refund_records.exists() or order.is_gift_snapshot
            or order.source_order_id or Order.objects.filter(source_order_id=order.pk).exists()
            or not order.in_service_at or not case.created_at or case.created_at < order.in_service_at
            or case.created_at > timezone.now()
            or case.account_id != order.customer_account_id or not order.customer_id
            or (scope is not None and (not isinstance(scope, list) or not scope
                or any(type(pk) is not int or pk <= 0 for pk in scope)
                or len(set(scope)) != len(scope) or order.customer_account_id in scope))
            or not LegacyAccountMap.objects.filter(account_id=order.customer_account_id,
                                                    legacy_user_id=order.customer_id).exists()):
        raise ValidationError('订单或售后范围尚待核对')
    if order.status == 'COMPLETED':
        from engagement.confirmation import aftersale_deadline
        deadline = aftersale_deadline(order)
        if not deadline or case.created_at >= deadline:
            raise ValidationError('售后申请时间超出订单售后期')
    if formal:
        from .points_returned_addon_refund import before_first_main_refund, require_other_seat
        returned = before_first_main_refund(order)
        if returned:
            if case.addon_payment_id:
                raise ValidationError('请单独登记主服务售后')
            require_other_seat(order, returned, case.affected_segment_ids)
    try:
        original = plan_points_transfer(order.pk, allow_recorded=True, preview_unstarted=True)
        funding = verify_order_funding(order.pk, allow_finalized=True, allow_unstarted=True,
                                       _transfer_refund_usage=transfer_refund_eligible(order))
        receipt, assets, sources = payment_refund_plan(order, None, refund_equivalent_units)
        allocations = verified_order_allocations_locked(order, _transfer_refund_usage=transfer_refund_eligible(order))
    except (PointsFundingPlanError, RefundCalculationError) as exc:
        raise ValidationError('付款、转单或补资凭证待核对') from exc
    from .points_transfer_revision import revision_tables_exist, verify_revisions_locked
    try:
        released = (verify_revisions_locked(order)[0] if revision_tables_exist()
                    and OrderPointsTransferFundingRevision.objects.filter(order=order).exists() else {})
    except PointsFundingPlanError as exc:
        raise ValidationError('转单补资释放凭证待核对') from exc
    from .models import OrderAddonPayment
    has_paid_addons = OrderAddonPayment.objects.filter(intent__order=order).exists()
    if has_paid_addons:
        from .addon_funding_release import addon_release_totals
        addon_released = addon_release_totals(allocations)
        released = {row.pk: released.get(row.pk, 0) + addon_released.get(row.pk, 0) for row in allocations}
    if sum(row.amount - released.get(row.pk, 0) for row in allocations) != funding.allocated:
        raise ValidationError('转单补资分配已变化，请重新核对')
    segments = list(OrderParticipationSegment.objects.select_for_update().filter(
        attribution__order=order).select_related('attribution').order_by('pk'))
    if (not segments or segments[0].attribution.original_amount != order.original_amount
            or sum(item.original_share for item in segments) !=
                segments[0].attribution.original_amount + segments[0].attribution.rounding_surplus
            or any(item.started_at and (item.started_at < item.accepted_at or item.started_at > timezone.now()
                or (item.released_at and item.started_at > item.released_at)) for item in segments)):
        raise ValidationError('转单原价份额待核对')
    by_segment = {item.pk: item for item in segments}
    if selected_segments and (not selected_segments.issubset(by_segment)
            or {by_segment[pk].provider_account_id for pk in selected_segments} != set(scope or [])
            or any(not by_segment[pk].started_at for pk in selected_segments)):
        raise ValidationError('所选积分转单履约段与受影响陪玩不一致')
    weights, income = defaultdict(int), defaultdict(int)
    from .addon_main_segment_deduction import verified_deductions
    prior_addon_deductions = verified_deductions(order)
    for segment in segments:
        if segment.started_at and segment.original_share and (not selected_segments or segment.pk in selected_segments):
            weights[segment.provider_account_id] += segment.original_share
    if any(not segment.started_at and segment.original_share > 0
           and (not selected_segments or segment.pk in selected_segments)
           and (scope is None or segment.provider_account_id in scope) for segment in segments):
        raise ValidationError('售后范围包含未开始履约的转入份额，承担规则待确认')
    if scope is None:
        scope = sorted(weights)
    if not scope:
        raise ValidationError('订单没有可核验的实际履约份额')
    for line in original.segments.segments:
        segment = by_segment.get(line.segment_id)
        if not segment or segment.provider_account_id != line.account_id:
            raise ValidationError('转单履约与收益归属待核对')
        if segment.started_at and (not selected_segments or segment.pk in selected_segments):
            remaining = line.income - prior_addon_deductions.get(line.segment_id, 0)
            if remaining < 0:
                raise ValidationError('加购退款已占收益超过该履约段')
            income[line.account_id] += remaining
    if not set(scope).issubset(weights):
        raise ValidationError('受影响陪玩没有可核验的履约份额')
    records = {}
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
    if manifest:
        if manifest.finalized_at or order.settled_at:
            raise ValidationError('整单已结算，售后凭证待核对')
        from .segment_settlement import _hash, _points_transfer_terms, _terms, _validate_records
        try:
            plan, points_terms = _points_transfer_terms(order)
            terms = _terms(order, plan, points_funding=points_terms)
            if points_terms is None or manifest.plan != terms or manifest.plan_hash != _hash(terms):
                raise SettlementError('逐段资金版本不一致')
            records = _validate_records(order, manifest, plan)
        except SettlementError as exc:
            raise ValidationError('已结算人员或补资凭证待核对') from exc
    elif (order.settled_at or order.providers.filter(settled_at__isnull=False).exists()
            or order.transactions.filter(status=Transaction.Status.SUCCESS,
                tx_type__in=[Transaction.TxType.INCOME, Transaction.TxType.SHOP_INCOME]).exists()):
        raise ValidationError('订单存在无法归属的收益记录')
    if set(scope).intersection(records):
        raise ValidationError('受影响陪玩收益已结算，请先核对售后时序')
    assignment_rows = list(order.providers.select_for_update())
    assignments = {row.provider_account_id: row for row in assignment_rows}
    profiles = {row.account_id: row for row in EscortProfile.objects.select_for_update().filter(account_id__in=scope)}
    if len(assignments) != len(assignment_rows) or any(pk not in assignments or pk not in profiles
           or profiles[pk].deposit_paid < 0 or profiles[pk].user_id != assignments[pk].provider_id
           or not LegacyAccountMap.objects.filter(account_id=pk, legacy_user_id=assignments[pk].provider_id).exists()
           for pk in scope):
        raise ValidationError('陪玩账号归属待核对')
    try:
        burden = provider_refund_burden(refund_equivalent_units=refund_equivalent_units,
                                        original_price_weights={pk: weights[pk] for pk in scope})
    except RefundCalculationError as exc:
        raise ValidationError(str(exc)) from exc
    terms, recoveries = [], []
    for pk in sorted(scope):
        amount, original_income = burden.amounts[pk], income.get(pk, 0)
        pending = min(original_income, amount)
        wallets = list(Wallet.objects.select_for_update().filter(
            Q(account_id=pk) | Q(user_id=assignments[pk].provider_id)).order_by('pk'))
        if len(wallets) > 1 or (wallets and (not wallets[0].is_active or wallets[0].balance < 0
                or wallets[0].account_id not in (None, pk)
                or wallets[0].user_id not in (None, assignments[pk].provider_id))):
            raise ValidationError('陪玩钱包归属或余额待核对')
        available = wallets[0].balance if wallets else 0
        wallet = min(amount - pending, available)
        deposit = min(amount - pending - wallet, profiles[pk].deposit_paid)
        terms.append({'account_id': pk, 'original_income_units': original_income, 'burden_units': amount,
                      'retained_units': max(original_income - amount, 0), 'additional_units': max(amount - original_income, 0)})
        recoveries.append({'account_id': pk, 'pending_units': pending, 'wallet_units': wallet,
                           'deposit_units': deposit, 'shortfall_units': amount - pending - wallet - deposit})
    original_funding = original.funding
    post = funding_after_refund(principal_units=original_funding.principal_units, gift_units=original_funding.gift_units,
        compensation_units=original_funding.compensation_units, returned_prepaid_units=sources.prepaid_units,
        returned_gift_units=sources.gift_units, returned_compensation_units=sources.compensation_units,
        provider_income_units=original_funding.provider_income_units,
        pending_deduction_units=sum(row['pending_units'] for row in recoveries),
        provider_external_units=sum(row['wallet_units'] + row['deposit_units'] + row['shortfall_units'] for row in recoveries),
        inviter_rate=order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0,
        allocated_units=funding.allocated, rounding_reserved_units=burden.rounding_to_store_units)
    if has_paid_addons:
        from .addon_funding_history import combined_at
        from .refunded_addon_funding import combine_refunded_history
        retained = defaultdict(int)
        segment_income = {line.segment_id: line.income for line in original.segments.segments}
        for line in original.segments.segments:
            retained[line.account_id] += line.income
        for recovery in recoveries:
            retained[recovery['account_id']] -= recovery['pending_units']
            affected = [line.segment_id for line in original.segments.segments
                        if line.account_id == recovery['account_id'] and by_segment[line.segment_id].started_at
                        and (not selected_segments or line.segment_id in selected_segments)]
            if recovery['pending_units'] and len(affected) != 1:
                raise ValidationError('加购退款后的主单扣减须明确唯一履约段')
            if recovery['pending_units']:
                segment_income[affected[0]] -= recovery['pending_units']
        if any(value < 0 for value in retained.values()):
            raise ValidationError('退款后的逐人收益异常')
        try:
            history = combined_at(order, post, timezone.now(), segment_shares=[
                (line.segment_id, line.account_id, line.basis, line.fee, segment_income[line.segment_id])
                for line in original.segments.segments])
            post = combine_refunded_history(post, history, main_retained_by_account=retained)
        except PointsFundingPlanError as exc:
            raise ValidationError('主单退款与加购历史补资待核对') from exc
    try:
        releases = plan_release(allocations=[(row.pk, row.amount, released.get(row.pk, 0)) for row in allocations],
                                amount=post.release_units)
    except PointsFundingAllocationError as exc:
        raise ValidationError('退款后补资释放测算待核对') from exc
    return {'order_id': order.pk, 'case_id': case.pk, 'redemption_id': receipt.pk,
        'refund_equivalent_units': refund_equivalent_units, 'coin_units': assets.coin_units,
        'points_hundredths': assets.point_hundredths, 'returned_prepaid_units': sources.prepaid_units,
        'returned_gift_units': sources.gift_units, 'returned_compensation_units': sources.compensation_units,
        'is_final': assets.is_final, 'provider_terms': terms, 'provider_recovery_preview': recoveries,
        'rounding_to_store_units': burden.rounding_to_store_units, 'original_required_units': funding.required,
        'conditional_funding_if_recovered': asdict(post),
        'conditional_release_parts': [asdict(part) for part in releases],
        'estimated_sufficient': all(not item['shortfall_units'] for item in recoveries),
        'executable': False, 'reason': '仅供核对；转单后的协商退款及收益结算尚未接通'}
