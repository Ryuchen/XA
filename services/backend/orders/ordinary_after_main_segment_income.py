"""Replay unpaid main earnings for an add-on refund after the first settlement agreement."""
from django.core.exceptions import ValidationError


def deduction_at(order, execution, shares, account_id, boundary, amount, *, followup=None):
    from .addon_main_segment_deduction import verified_deductions
    from .models import (OrderOriginalShareTransfer, OrderParticipationSegment,
                         OrderSegmentFinancialSnapshot, OrderSegmentPersonSettlement, OrderTransferNegotiationEvidence,
                         OrdinaryTransferFollowupRound)

    rounds = list(OrdinaryTransferFollowupRound.objects.filter(order=order,
            execution__termination_processed_at__lt=boundary).order_by('sequence'))
    if rounds and (not followup or rounds[-1].pk != followup.pk
                   or [item.sequence for item in rounds] != list(range(1, len(rounds) + 1))):
        raise ValidationError('追加主单协商后的加购承担待核对')
    if OrderSegmentPersonSettlement.objects.filter(manifest__order=order, account_id=account_id,
                                                   settled_at__lt=boundary).exists():
        return []
    transfers = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order)
    if transfers.filter(created_at=boundary).exists():
        raise ValidationError('转单与加购退款时序无法区分')
    transferred = transfers.filter(created_at__lt=boundary).exists()
    if transferred:
        from .ordinary_transfer_event_scope import segment_lines_at
        lines = [row for row in segment_lines_at(order, boundary) if row.account_id == account_id]
        income = {row.segment_id: row.income for row in lines}
        if len(lines) != len(income):
            raise ValidationError('历史履约段重复')
    else:
        snapshots = OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order,
            segment__provider_account_id=account_id, segment__accepted_at__lt=boundary,
            segment__started_at__isnull=False, segment__started_at__lt=boundary)
        income = {row.segment_id: row.income_at_entry for row in snapshots}
    if not income:
        raise ValidationError('加购退款本人主单历史收益缺失')

    matching = [row for row in shares if row.account_id == account_id]
    included = {}
    if matching:
        if len(matching) != 1:
            raise ValidationError('首次协商本人保留收益异常')
        evidence = OrderTransferNegotiationEvidence.objects.filter(order=order, execution=execution).first()
        if evidence:
            selected = [item['id'] for item in evidence.snapshot.get('segments', [])
                if item['id'] in (execution.proposal.affected_segment_ids or [])
                and item['account_id'] == account_id and item.get('started_at')]
        else:
            selected = list(OrderParticipationSegment.objects.filter(pk__in=income,
                provider_account_id=account_id, accepted_at__lte=execution.accepted_at)
                .values_list('pk', flat=True))
        previous = verified_deductions(order, before=execution.created_at)
        from .ordinary_retained_segment_shares import retained, pending_for
        try:
            plan = execution.proposal.ordinary_segment_pending_plan
            pending = pending_for({'segment_pending_plan': plan}, account_id, selected) if plan is not None else None
            resolved = retained(selected, income, previous, matching[0], pending=pending)
        except ValueError as exc:
            raise ValidationError('首次协商本人逐段保留收益待核对') from exc
        for segment_id, value in resolved.items():
            income[segment_id] = value
            included[segment_id] = previous.get(segment_id, 0)

    closed_segments = set()
    terminated_segments = set(selected) if matching else set()
    for round_row in rounds:
        closed = round_row.facts.get('segments', {}).get(str(account_id), [])
        if any(segment in terminated_segments for segment in closed):
            raise ValidationError('追加协商不能重新打开已终止的履约段')
        terminated_segments.update(closed)
        closed_segments.update(closed)
        if closed:
            later_shares = [share for share in round_row.execution.shares.all() if share.account_id == account_id]
            if len(later_shares) != 1:
                raise ValidationError('追加协商本人终止段或保留收益异常')
            previous = verified_deductions(order, before=round_row.execution.created_at)
            from .ordinary_retained_segment_shares import retained, pending_for
            try:
                pending = pending_for(round_row.facts, account_id, closed)
                resolved = retained(closed, income, previous, later_shares[0], pending=pending)
            except ValueError as exc:
                raise ValidationError('追加协商本人逐段保留收益待核对') from exc
            for segment_id, value in resolved.items():
                income[segment_id] = value
                included[segment_id] = previous.get(segment_id, 0)
                closed_segments.discard(segment_id)

    used = verified_deductions(order, before=boundary)
    segments = list(OrderParticipationSegment.objects.filter(pk__in=income, provider_account_id=account_id)
                    .order_by('-accepted_at', '-pk').values_list('pk', flat=True))
    if len(segments) != len(income):
        raise ValidationError('加购退款本人历史履约段归属异常')
    remaining, parts = amount, []
    for segment_id in segments:
        prior = included.get(segment_id, 0)
        later_used = used.get(segment_id, 0) - prior
        available = income[segment_id] - later_used
        if later_used < 0 or available < 0:
            raise ValidationError('加购退款历史主单收益扣减异常')
        take = min(remaining, available) if segment_id not in closed_segments else 0
        if take:
            parts.append((segment_id, take))
            remaining -= take
    return parts
