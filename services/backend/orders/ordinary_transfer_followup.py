"""Build a later agreement from immutable transferred-order participation."""
from collections import defaultdict
from dataclasses import asdict
import hashlib
import json

from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import (OrderSegmentPersonSettlement, OrderTransferNegotiationEvidence,
                     OrdinaryTransferFollowupRound)


def applies(order):
    from .ordinary_transfer_capability import enabled
    from .points_service_payment import needs_integrated_processing
    from .models import OrdinaryPostRefundTransferRevision
    from .negotiation_zero import marked_execution_id
    zero_id = marked_execution_id(order) if order else None
    prior_zero_transfer = bool(zero_id and OrdinaryPostRefundTransferRevision.objects.filter(order=order,
        snapshot__zero_pretransfer_execution_id=zero_id).exists())
    prior_positive_transfer = OrdinaryPostRefundTransferRevision.objects.filter(order=order,
        snapshot__positive_pretransfer_execution_id__isnull=False).exists()
    return bool(enabled() and not needs_integrated_processing(order)
                and (OrderTransferNegotiationEvidence.objects.filter(order=order).exists()
                     or prior_zero_transfer or prior_positive_transfer))


def _hash(proposal_id, facts):
    payload = {'proposal_id': proposal_id, 'facts': facts}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True,
                                     separators=(',', ':')).encode()).hexdigest()


def calculation_locked(order, case, refund_units):
    """Rebuild outstanding earnings; no funds or reservations are changed."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('转单追加协商须在订单资金事务内计算')
    from .financial_lock import lock_order_money
    lock_order_money()
    if (order.settled_at or order.consumption_confirmed_at or case.status != 'OPEN'
            or case.account_id != order.customer_account_id or not case.client_request_id
            or type(refund_units) is not int or refund_units < 0 or refund_units % 10):
        raise ValidationError('追加售后或退款金额待核对')
    first = (OrderTransferNegotiationEvidence.objects.select_for_update().select_related('execution__refund')
             .filter(order=order).order_by('pk').first())
    if first:
        first_execution = first.execution
    else:
        from .models import OrderOriginalShareTransfer
        from .ordinary_zero_pretransfer import verify as verify_pretransfer_zero
        transfer = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).order_by('created_at', 'pk').first()
        first_execution = verify_pretransfer_zero(order, before=transfer.created_at) if transfer else None
        if not first_execution and transfer and order.refund_records.exists():
            from .ordinary_positive_pretransfer import verify as verify_positive_pretransfer
            from django.core.exceptions import ValidationError as DjangoValidationError
            try:
                first_execution = verify_positive_pretransfer(order, before=transfer.created_at)
            except DjangoValidationError as exc:
                raise ValidationError('转单前正退款原协商待核对') from exc
    if not first_execution or not first_execution.termination_processed_at:
        raise ValidationError('前次转单协商尚未结案')
    from .negotiation_execution import verify_execution
    from .negotiation_termination import verify_termination_evidence
    if first:
        from .negotiation_transfer_evidence import verify_locked
        verify_locked(first_execution)
        if first_execution.refund_id:
            verify_execution(first_execution)
            verify_termination_evidence(first_execution)
        else:
            from .negotiation_zero import verify_zero_execution, verify_zero_termination
            verify_zero_execution(first_execution)
            verify_zero_termination(first_execution)
    prior = [first_execution]
    rounds = list(OrdinaryTransferFollowupRound.objects.select_for_update().filter(
        order=order, execution__isnull=False).select_related('execution').order_by('sequence'))
    previous = first_execution
    for number, row in enumerate(rounds, start=1):
        if (row.sequence != number or row.previous_id != previous.pk or row.order_id != order.pk
                or row.facts_hash != _hash(row.proposal_id, row.facts)
                or not row.execution.termination_processed_at):
            raise ValidationError('追加协商轮次凭证待核对')
        if row.execution.zero_order_id:
            from .ordinary_transfer_followup_zero import verify_execution as verify_zero, verify_termination as verify_zero_end
            verify_zero(row.execution)
            verify_zero_end(row.execution)
        else:
            verify_execution(row.execution)
            verify_termination_evidence(row.execution)
        prior.append(row.execution)
        previous = row.execution
    if not case.created_at or case.created_at < previous.termination_processed_at:
        raise ValidationError('追加售后早于前次结案')
    from .segment_settlement_plan import build_segment_settlement_plan
    from .settlement import SettlementError
    try:
        current_plan = build_segment_settlement_plan(order, allow_recorded=True, _allow_recorded_refunds=True)
    except SettlementError as exc:
        raise ValidationError('当前履约段或转单抽成凭证待核对') from exc
    incomes, weights, segment_ids = defaultdict(int), defaultdict(int), defaultdict(list)
    for line in current_plan.segments:
        incomes[line.account_id] += line.income
        segment_ids[line.account_id].append(line.segment_id)
    for segment in order.original_attribution.segments.select_for_update().order_by('pk'):
        if segment.started_at and segment.original_share:
            weights[segment.provider_account_id] += segment.original_share
    from .addon_main_deduction import deductions_at
    addon_deductions = deductions_at(order)
    from .addon_main_segment_deduction import verified_deductions
    segment_addon_deductions = verified_deductions(order)
    derived_addon_deductions = defaultdict(int)
    for line in current_plan.segments:
        derived_addon_deductions[line.account_id] += segment_addon_deductions.get(line.segment_id, 0)
    if any(derived_addon_deductions[account] != amount for account, amount in addon_deductions.items()):
        raise ValidationError('加购退款的本人履约段扣减汇总不一致')
    for account, amount in addon_deductions.items():
        incomes[account] -= amount
    for receipt in prior:
        for share in receipt.shares.select_for_update().order_by('pk'):
            incomes[share.account_id] -= share.pending_deduction
    scope = case.affected_provider_account_ids
    from .aftersale_segment_scope import resolve_locked
    selected = resolve_locked(order, scope, case.affected_segment_ids)
    if not selected:
        raise ValidationError('追加售后缺少受影响履约段')
    settled = set(OrderSegmentPersonSettlement.objects.filter(manifest__order=order).values_list('account_id', flat=True))
    if (not isinstance(scope, list) or not scope or len(set(scope)) != len(scope)
            or any(type(account) is not int or account <= 0 or account not in incomes
                   or account in settled or not weights[account] for account in scope)
            or any(value < 0 for value in incomes.values())):
        raise ValidationError('已结案、已结算或无有效履约段的陪玩不能重新协商')
    target_income = defaultdict(int)
    target_segments = defaultdict(list)
    target_weights = defaultdict(int)
    segment_rows = {segment.pk: segment for segment in order.original_attribution.segments.filter(pk__in=selected)}
    for line in current_plan.segments:
        if line.segment_id in selected:
            target_income[line.account_id] += line.income
            target_segments[line.account_id].append(line.segment_id)
            target_weights[line.account_id] += segment_rows[line.segment_id].original_share
    for account in scope:
        target_income[account] -= sum(segment_addon_deductions.get(segment, 0)
                                      for segment in target_segments[account])
        if target_income[account] < 0 or not target_weights[account]:
            raise ValidationError('所选履约段收益或原价份额待核对')
    refunds = list(order.refund_records.select_for_update().order_by('pk'))
    if {row.pk for row in refunds} != {row.refund_id for row in prior if row.refund_id}:
        raise ValidationError('主单已有未纳入逐轮协商的退款')
    prior_total = sum(row.amount for row in refunds)
    if prior_total + refund_units > order.amount:
        raise ValidationError('追加退款超过原订单未退金额')
    from .refund_math import RefundCalculationError, provider_refund_burden
    if refund_units:
        try:
            burden = provider_refund_burden(refund_equivalent_units=refund_units,
                                            original_price_weights={account: target_weights[account] for account in scope})
        except RefundCalculationError as exc:
            raise ValidationError(str(exc)) from exc
        amounts, rounding = burden.amounts, burden.rounding_to_store_units
    else:
        amounts, rounding = {account: 0 for account in scope}, 0
    terms = []
    for account in sorted(scope):
        amount, income = amounts[account], target_income[account]
        terms.append({'account_id': account, 'original_income_units': income, 'burden_units': amount,
                      'retained_units': max(income - amount, 0), 'additional_units': max(amount - income, 0)})
    # Freeze the same per-person unpaid segment order used by actual add-on
    # refund deductions. Both parties see and confirm this plan before posting.
    line_by_segment = {line.segment_id: line for line in current_plan.segments}
    ordered = list(order.original_attribution.segments.filter(pk__in=selected)
                   .order_by('-accepted_at', '-pk'))
    segment_pending_plan = []
    for term in terms:
        remaining = min(term['burden_units'], term['original_income_units'])
        for segment in ordered:
            if segment.provider_account_id != term['account_id']:
                continue
            available = line_by_segment[segment.pk].income - segment_addon_deductions.get(segment.pk, 0)
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
    from .negotiation_funding import funding_preview
    preview = funding_preview(order, terms)
    preview.pop('calculated_at', None)
    sources = list(order.funding_allocations.select_for_update().order_by('pk').values(
        'id', 'lot_id', 'kind', 'amount', 'refunded_amount'))
    if (not sources or sum(row['amount'] for row in sources) != order.amount
            or sum(row['refunded_amount'] for row in sources) != prior_total):
        raise ValidationError('追加协商的原付款来源待核对')
    settled_facts = list(OrderSegmentPersonSettlement.objects.select_for_update().filter(
        manifest__order=order).order_by('pk').values('id', 'account_id', 'amount', 'segment_ids'))
    return {'order_id': order.pk, 'previous_execution_id': previous.pk, 'sequence': len(rounds) + 1,
            'prior_refund_units': prior_total, 'provider_terms': terms,
            'rounding_to_store_units': rounding,
            'segments': {str(account): sorted(target_segments[account]) for account in sorted(scope)},
            'segment_lines': [asdict(line) for line in current_plan.segments],
            'outstanding_income': {str(account): amount for account, amount in sorted(incomes.items())},
            'prior_execution_ids': [row.pk for row in prior],
            'funding_sources': sources, 'settled_facts': settled_facts,
            'addon_main_deductions': {str(account): amount for account, amount in addon_deductions.items()},
            'segment_pending_plan': segment_pending_plan,
            'provider_funding_preview': preview}


def bind_locked(proposal, facts):
    return OrdinaryTransferFollowupRound.objects.create(order_id=facts['order_id'], proposal=proposal,
        previous_id=facts['previous_execution_id'], sequence=facts['sequence'], facts=facts,
        facts_hash=_hash(proposal.pk, facts))


def verify_draft_locked(proposal, current):
    row = OrdinaryTransferFollowupRound.objects.select_for_update().filter(proposal=proposal).first()
    if not row or row.facts_hash != _hash(proposal.pk, row.facts):
        raise ValidationError('追加协商逐轮快照缺失或异常')
    # Wallet availability is a quote and is recalculated when funds are
    # actually debited; agreed earnings and original sources must not change.
    original = dict(row.facts)
    checked = dict(current)
    original.pop('provider_funding_preview', None)
    checked.pop('provider_funding_preview', None)
    if original != checked:
        raise ValidationError('履约段或付款来源已变化，请重新确认方案')
    return row
