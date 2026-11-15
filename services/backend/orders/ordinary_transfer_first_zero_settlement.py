"""Settle a transferred ordinary order after its first zero-refund agreement."""
from collections import defaultdict
from dataclasses import asdict, replace
from datetime import timedelta

from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import OrderAddonPayment, OrderTransferNegotiationEvidence
from .segment_settlement_plan import build_segment_settlement_plan
from .settlement import SettlementError


def build_locked(order, *, now=None):
    if not transaction.get_connection().in_atomic_block or order.status != order.Status.COMPLETED:
        raise SettlementError('整单尚未完成或订单事务未锁定')
    evidence = OrderTransferNegotiationEvidence.objects.select_for_update().select_related('execution').filter(order=order).first()
    if not evidence or evidence.execution.refund_id or not evidence.execution.zero_order_id:
        raise SettlementError('首次零退款协商凭证缺失')
    from .negotiation_zero import verify_zero_execution, verify_zero_termination
    try:
        shares = verify_zero_execution(evidence.execution)
        verify_zero_termination(evidence.execution)
        from .negotiation_transfer_evidence import _segment_facts
        facts, _ = _segment_facts(order)
        if {item['id'] for item in facts} != {item['id'] for item in evidence.snapshot['segments']}:
            raise ValidationError('零退款后发生转单，需要新的资金版本')
        plan = build_segment_settlement_plan(order, allow_recorded=True, _allow_recorded_refunds=True)
        from engagement.funding_wallet import validate_order_funding_sources
        sources = list(order.funding_allocations.select_for_update().select_related('lot').order_by('pk'))
        validate_order_funding_sources(order, sources)
        paid = {kind: sum(row.amount for row in sources if row.kind == kind)
                for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
        if sum(paid.values()) != order.amount or any(row.refunded_amount for row in sources):
            raise ValidationError('零退款原付款来源待核对')
        from .points_funding_evidence import _verified_allocations_for_locked_order
        from .addon_funding_release import addon_release_totals
        allocations = _verified_allocations_for_locked_order(order)
        releases = addon_release_totals(allocations)
        allocated = sum(row.amount - releases[row.pk] for row in allocations)
        from .points_negotiation_preview import funding_after_refund
        main = funding_after_refund(principal_units=paid['PREPAID'], gift_units=paid['GIFT'],
            compensation_units=paid['COMPENSATION'], returned_prepaid_units=0, returned_gift_units=0,
            returned_compensation_units=0, provider_income_units=sum(line.income for line in plan.segments),
            pending_deduction_units=0, provider_external_units=0,
            inviter_rate=order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0,
            allocated_units=allocated)
        from .addon_funding_history import combined_at
        history = combined_at(order, main, timezone.now(), segment_shares=[
            (line.segment_id, line.account_id, line.basis, line.fee, line.income) for line in plan.segments])
        if history.get('addon_history'):
            from .refunded_addon_funding import combine_refunded_history
            by_account = defaultdict(int)
            for line in plan.segments:
                by_account[line.account_id] += line.income
            funding = combine_refunded_history(main, history, main_retained_by_account=by_account)
        else:
            funding = main
    except (ValidationError, ValueError) as exc:
        raise SettlementError('零退款逐段资金凭证待核对') from exc
    if funding.remaining_units or funding.release_units:
        raise SettlementError('门店补资尚未足额或多余分配尚未释放')
    from .addon_handover import assert_no_pending_handover, current_responsibilities
    parts = defaultdict(int)
    affected = {share.account_id for share in shares}
    selected = set(evidence.execution.proposal.affected_segment_ids or [])
    for payment in OrderAddonPayment.objects.select_for_update().filter(intent__order=order).order_by('pk'):
        try:
            assert_no_pending_handover(payment)
            _, responsibilities = current_responsibilities(payment, lock=True)
            for item in responsibilities:
                parts[(payment.pk, item['segment_id'], item['provider_id'])] += item['income']
                if item['is_current'] and item['segment_id'] in selected:
                    from .addon_completion import verify_accepted
                    verify_accepted(order, payment, evidence.execution, item)
        except ValidationError as exc:
            raise SettlementError('加购完成或交接尚未确认') from exc
    deductions = dict(history.get('main_deductions', []))
    now, people = now or timezone.now(), []
    for person in plan.persons:
        blockers = [item for item in person.blockers if item != '售后等待期尚未结束']
        start = (evidence.execution.accepted_at if person.segment_ids and
                 all(pk in selected for pk in person.segment_ids) else order.completed_at)
        if not start or now < start + timedelta(hours=order.service_aftersale_hours):
            blockers.append('售后等待期尚未结束')
        income = person.income - deductions.get(person.account_id, 0)
        if income < 0:
            raise SettlementError('加购退款扣减超过本人收益')
        people.append(replace(person, income=income, blockers=tuple(blockers)))
    addon_parts = [{'payment_id': payment, 'segment_id': segment, 'provider_account_id': account,
                    'provider_income_units': amount} for (payment, segment, account), amount in sorted(parts.items())]
    if sum(item.income for item in people) + sum(item['provider_income_units'] for item in addon_parts) != funding.provider_income_units:
        raise SettlementError('零退款逐段收益与整单资金不守恒')
    details = asdict(funding)
    details.update(addons=addon_parts, addon_history=history.get('addon_history', []), first_zero_execution_id=evidence.execution_id)
    return replace(plan, persons=tuple(people), inviter_income=funding.inviter_income_units,
                   store_income=funding.store_income_units), details
