"""Bound ordinary funding replay to the real transfer events at its boundary."""
from collections import Counter

from django.core.exceptions import ValidationError


def transfers_at(order, boundary):
    from .models import OrderOriginalShareTransfer
    events = list(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order)
        .select_related('previous_segment', 'next_segment').order_by('created_at', 'pk'))
    if (not boundary or not events or any(not item.created_at or item.created_at == boundary for item in events)):
        raise ValidationError('转单与资金事件时序尚未接通')
    counts = Counter(item.previous_segment.slot for item in events)
    if (any(count > 2 for count in counts.values())
            or len({(item.previous_segment.slot, item.created_at) for item in events}) != len(events)
            or any(item.previous_segment.attribution_id != item.next_segment.attribution_id
                   or item.previous_segment.slot != item.next_segment.slot for item in events)):
        raise ValidationError('各席位转单次数或履约归属异常')
    before = [item for item in events if item.created_at < boundary]
    if not before:
        raise ValidationError('资金事件发生前没有真实转单')
    return before


def segment_lines_at(order, boundary, *, allow_unstarted=False):
    """Rebuild immutable segment economics before a later transfer, without locks."""
    from django.db.models import Q
    from .models import (NegotiationExecution, OrderSegmentFinancialSnapshot, OrderSegmentTransferBasis,
                         OrderOriginalShareTransfer)
    from .segment_settlement_plan import SegmentAmount
    from .settlement import compute_provider_income
    from .transfer_economics import allocate_same_rule_fixed_seat, allocate_transfer_basis
    events = transfers_at(order, boundary)
    attribution = order.original_attribution
    coverage = attribution.financial_coverage
    all_events = list(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution=attribution)
        .select_related('previous_segment', 'next_segment').order_by('pk'))
    all_segments = list(attribution.segments.order_by('pk'))
    all_snapshots = {row.segment_id: row for row in OrderSegmentFinancialSnapshot.objects.filter(
        segment__attribution=attribution)}
    basis_facts = {row.transfer_id: row for row in OrderSegmentTransferBasis.objects.filter(transfer__in=all_events)}
    if (coverage.source != 'WALLET_NO_POINTS' or coverage.deal_amount != order.amount
            or len(all_snapshots) != len(all_segments) or len(basis_facts) != len(all_events)
            or len({event.previous_segment_id for event in all_events}) != len(all_events)
            or len({event.next_segment_id for event in all_events}) != len(all_events)):
        raise ValidationError('转单履约与成交快照不完整')
    all_outgoing = {event.previous_segment_id: event for event in all_events}
    for event in all_events:
        old, new, split = all_snapshots[event.previous_segment_id], all_snapshots[event.next_segment_id], basis_facts[event.pk]
        previous, incoming = event.previous_segment, event.next_segment
        if (previous.released_at != event.created_at or incoming.accepted_at != event.created_at
                or not event.operator_id or not isinstance(event.agreement, str) or len(event.agreement.strip()) < 3
                or previous.original_share != event.retained_share
                or event.original_share_before != event.retained_share + event.transferred_share
                or (all_outgoing[incoming.pk].original_share_before if incoming.pk in all_outgoing
                    else incoming.original_share) != event.transferred_share):
            raise ValidationError('转单原价份额或时点异常')
        try:
            expected = allocate_transfer_basis(old.incoming_basis, event.original_share_before,
                                                event.retained_share, event.transferred_share)
        except ValueError as exc:
            raise ValidationError('转单成交基数异常') from exc
        if ((split.basis_before, split.retained_basis, split.transferred_basis, split.remainder_numerator)
                != (expected.total, expected.retained, expected.transferred, expected.remainder_numerator)
                or old.retained_basis != expected.retained or new.incoming_basis != expected.transferred):
            raise ValidationError('转单成交基数凭证异常')
        if old.commission_type == 'FIXED' and new.commission_type == 'FIXED' and old.fixed_rule_total == new.fixed_rule_total:
            try:
                _, fee, shortfall = allocate_same_rule_fixed_seat(coverage.deal_amount, old.fixed_rule_total,
                    old.commission_fixed, expected.retained, expected.transferred)
            except (TypeError, ValueError) as exc:
                raise ValidationError('转单固定抽成异常') from exc
            if new.commission_fixed != fee or split.fixed_fee_store_shortfall != shortfall:
                raise ValidationError('转单固定抽成凭证异常')
        elif split.fixed_fee_store_shortfall is not None:
            raise ValidationError('转单固定抽成差额来源异常')
        elif new.commission_type == 'FIXED':
            if (new.fixed_rule_total is None or new.commission_fixed !=
                    (new.fixed_rule_total * expected.transferred + coverage.deal_amount - 1) // coverage.deal_amount):
                raise ValidationError('转入固定抽成异常')
    segments = list(attribution.segments.filter(accepted_at__lt=boundary).order_by('pk'))
    snapshots = all_snapshots
    outgoing = {event.previous_segment_id: event for event in events}
    lines = []
    for segment in segments:
        snap = snapshots.get(segment.pk)
        if not snap:
            raise ValidationError('历史履约段抽成快照缺失')
        if (snap.commission_type not in ('FIXED', 'PERCENT') or not 0 <= snap.commission_rate <= 100
                or snap.income_at_entry != compute_provider_income(snap.incoming_basis, snap.commission_type,
                                                                  snap.commission_rate, snap.commission_fixed)
                or (snap.commission_type == 'PERCENT' and (snap.commission_fixed or snap.fixed_rule_total is not None))):
            raise ValidationError('历史履约段接入收益异常')
        event = outgoing.get(segment.pk)
        if event:
            basis = snap.retained_basis
        elif segment.released_at and segment.released_at < boundary:
            ended = NegotiationExecution.objects.filter(Q(refund__order=order) | Q(zero_order=order),
                shares__account_id=segment.provider_account_id, accepted_at=segment.released_at,
                termination_processed_at__lt=boundary).exists()
            if ended and segment.started_at and segment.started_at < boundary:
                basis = snap.incoming_basis
            elif not segment.started_at or segment.started_at >= boundary:
                basis = 0
            else:
                raise ValidationError('历史履约段终止来源异常')
        else:
            basis = snap.incoming_basis
        if basis is None or basis < 0 or basis > snap.incoming_basis:
            raise ValidationError('历史履约段成交基数异常')
        if basis and (not segment.started_at or segment.started_at >= boundary):
            if not allow_unstarted or segment.pk not in {event.next_segment_id for event in events}:
                raise ValidationError('历史收益缺少本人开始履约')
        if snap.commission_type == 'FIXED':
            fee = (snap.fixed_rule_total * basis // coverage.deal_amount if event else
                   0 if not basis else snap.commission_fixed)
        else:
            fee = basis * snap.commission_rate // 100
        if fee > basis:
            raise ValidationError('历史履约段抽成异常')
        lines.append(SegmentAmount(segment.pk, segment.provider_account_id, basis, fee, basis - fee))
    return tuple(lines)
