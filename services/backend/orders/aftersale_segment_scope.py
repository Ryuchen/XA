"""Resolve a subsequent case to its exact, still-open participation segments."""
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import OrderParticipationSegment, OrderTransferNegotiationEvidence, OrdinaryTransferFollowupRound


def closed_segments_locked(order):
    from .points_service_payment import needs_integrated_processing
    from .models import OrderOriginalShareTransfer
    if (needs_integrated_processing(order) and OrderOriginalShareTransfer.objects.filter(
            previous_segment__attribution__order=order).exists()):
        return _points_segments_locked(order, None, None, closed_only=True)
    evidence = (OrderTransferNegotiationEvidence.objects.select_for_update().select_related('execution__proposal')
                .filter(order=order, execution__termination_processed_at__isnull=False).first())
    if not evidence:
        from .ordinary_transfer_followup import applies
        from .models import OrderOriginalShareTransfer
        transfer = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).order_by('created_at', 'pk').first()
        if not transfer or not applies(order):
            return None
        if not transaction.get_connection().in_atomic_block:
            raise RuntimeError('售后履约段必须在订单事务内确定')
        from .financial_lock import lock_order_money
        from .ordinary_zero_pretransfer import verify as verify_pretransfer_zero
        lock_order_money()
        first = verify_pretransfer_zero(order, before=transfer.created_at)
        if not first and order.refund_records.exists():
            from .ordinary_positive_pretransfer import verify as verify_positive_pretransfer
            first = verify_positive_pretransfer(order, before=transfer.created_at)
        if not first:
            raise ValidationError('首次零退款终止凭证缺失')
        closed = set(OrderParticipationSegment.objects.filter(attribution__order=order,
            provider_account_id__in=first.proposal.affected_accounts, released_at=first.accepted_at,
            started_at__isnull=False).values_list('pk', flat=True))
        if not closed:
            raise ValidationError('首次零退款终止履约段缺失')
        rounds = OrdinaryTransferFollowupRound.objects.select_for_update().filter(
            order=order, execution__termination_processed_at__isnull=False).select_related('proposal', 'execution')
        for row in rounds:
            if row.execution.refund_id:
                from .negotiation_execution import verify_execution
                from .negotiation_termination import verify_termination_evidence
            else:
                from .ordinary_transfer_followup_zero import verify_execution, verify_termination as verify_termination_evidence
            verify_execution(row.execution)
            verify_termination_evidence(row.execution)
            for account in row.proposal.affected_accounts:
                closed.update(row.facts.get('segments', {}).get(str(account), []))
        return closed
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('售后履约段必须在订单事务内确定')
    from .financial_lock import lock_order_money
    lock_order_money()
    from .negotiation_execution import verify_execution
    from .negotiation_termination import verify_termination_evidence
    if evidence.execution.refund_id:
        verify_execution(evidence.execution)
        verify_termination_evidence(evidence.execution)
    else:
        from .negotiation_zero import verify_zero_execution, verify_zero_termination
        verify_zero_execution(evidence.execution)
        verify_zero_termination(evidence.execution)
    chosen = evidence.execution.proposal.affected_segment_ids
    closed = set(chosen) if chosen is not None else {
        segment['id'] for segment in evidence.snapshot.get('segments', [])
        if segment.get('account_id') in evidence.execution.proposal.affected_accounts and segment.get('started_at')}
    rounds = OrdinaryTransferFollowupRound.objects.select_for_update().filter(
        order=order, execution__termination_processed_at__isnull=False).select_related('proposal')
    for row in rounds:
        if row.execution.refund_id:
            verify_execution(row.execution)
            verify_termination_evidence(row.execution)
        else:
            from .ordinary_transfer_followup_zero import verify_execution as verify_zero, verify_termination as verify_zero_end
            verify_zero(row.execution)
            verify_zero_end(row.execution)
        for account in row.proposal.affected_accounts:
            closed.update(row.facts.get('segments', {}).get(str(account), []))
    return closed


def resolve_locked(order, accounts, requested):
    if requested is not None and (not isinstance(requested, list) or not requested
            or any(type(item) is not int or item <= 0 for item in requested)
            or len(set(requested)) != len(requested)):
        raise ValidationError('请选择有效且不重复的履约段')
    closed = closed_segments_locked(order)
    if closed is None:
        from .models import OrderOriginalShareTransfer
        from .points_service_payment import needs_integrated_processing
        from .ordinary_transfer_capability import permits
        first_ordinary_transfer = (not needs_integrated_processing(order)
            and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
            and permits(order))
        if not first_ordinary_transfer:
            if requested is not None:
                raise ValidationError('当前售后暂不支持按履约段选择')
            return None
        closed = set()
    query = OrderParticipationSegment.objects.select_for_update().filter(
        attribution__order=order, started_at__isnull=False)
    if accounts:
        query = query.filter(provider_account_id__in=accounts)
    candidates = list(query.order_by('pk'))
    eligible = {row.pk: row for row in candidates if row.pk not in closed}
    if not accounts:
        accounts = sorted({row.provider_account_id for row in eligible.values()})
        if not accounts:
            raise ValidationError('本单没有可重新申请售后的履约段')
    if requested is None:
        selected = []
        for account in accounts:
            matches = [row.pk for row in eligible.values() if row.provider_account_id == account]
            if len(matches) != 1:
                raise ValidationError('请明确选择该陪玩本次售后对应的履约段')
            selected.extend(matches)
    else:
        selected = sorted(requested)
    if (any(pk not in eligible for pk in selected)
            or {eligible[pk].provider_account_id for pk in selected} != set(accounts)):
        raise ValidationError('所选履约段不属于本次受影响陪玩，或该段已确认终止')
    return sorted(selected)


def _points_segments_locked(order, accounts, requested, *, closed_only=False):
    """Bind the actual points service segments, preserving prior closed episodes."""
    from .points_transfer_refund_access import permits
    if not permits(order, phase='refund') or not transaction.get_connection().in_atomic_block:
        raise ValidationError('积分转单售后范围须在已核验的订单资金事务中确定')
    if not accounts and not closed_only:
        raise ValidationError('请选择受影响陪玩和对应履约段')
    if order.refund_records.count() >= 3:
        from .points_round_funding import current_locked
        state = current_locked(order)
        closed = state['closed_segment_ids']
        if closed_only:
            return closed
        candidates = list(OrderParticipationSegment.objects.select_for_update().filter(
            attribution__order=order, provider_account_id__in=accounts, started_at__isnull=False,
            released_at__isnull=True).order_by('pk'))
        eligible = {row.pk: row for row in candidates if row.pk not in closed}
        if requested is None:
            selected = []
            for account in accounts:
                matches = [row.pk for row in eligible.values() if row.provider_account_id == account]
                if len(matches) != 1:
                    raise ValidationError('请明确选择该陪玩本次售后对应的履约段')
                selected.extend(matches)
        else:
            selected = sorted(requested)
        if (any(pk not in eligible for pk in selected)
                or {eligible[pk].provider_account_id for pk in selected} != set(accounts)):
            raise ValidationError('所选履约段不属于本次受影响陪玩，或该段已确认终止')
        return sorted(selected)
    from .models import PointsNegotiationPosting
    from .points_transfer_funding import plan_points_transfer
    from .points_funding_plan import PointsFundingPlanError
    try:
        if order.refund_records.exists():
            if order.refund_records.count() == 2:
                from .points_transfer_followup_funding import plan_locked
                plan_locked(order.pk, allow_recorded=bool(order.settled_at),
                            allow_finalized=bool(order.settled_at), allow_in_service=True)
            else:
                from .points_transfer_refunded_funding import plan_refunded_transfer
                plan_refunded_transfer(order.pk, allow_finalized=True)
        else:
            plan_points_transfer(order.pk, preview_unstarted=True, allow_recorded=True)
    except PointsFundingPlanError as exc:
        raise ValidationError('积分转单履约份额待核对') from exc
    postings = list(PointsNegotiationPosting.objects.select_for_update().filter(
        points_refund__redemption__order=order).order_by('points_refund__refund_id'))
    if len(postings) != order.refund_records.count() or len(postings) > 2:
        raise ValidationError('积分转单退款范围凭证不完整')
    closed = set()
    for index, posting in enumerate(postings):
        if index == 0:
            from .points_negotiation_termination import verify_termination_locked
            verify_termination_locked(posting, allow_verified_later_final=len(postings) > 1)
        else:
            from .points_transfer_followup_termination import verify_closed_or_settled_locked
            verify_closed_or_settled_locked(posting)
        chosen = posting.proposal.affected_segment_ids
        if chosen is None:
            frozen = posting.participants.get('facts', {}) if isinstance(posting.participants, dict) else {}
            chosen = [item['id'] for item in frozen.get('segments', []) if item.get('started_at')
                      and item.get('account_id') in posting.proposal.affected_accounts]
        if not isinstance(chosen, list) or not chosen or any(type(pk) is not int or pk <= 0 for pk in chosen):
            raise ValidationError('积分转单已确认履约段凭证异常')
        closed.update(chosen)
    if closed_only:
        return closed
    candidates = list(OrderParticipationSegment.objects.select_for_update().filter(
        attribution__order=order, provider_account_id__in=accounts, started_at__isnull=False).order_by('pk'))
    eligible = {row.pk: row for row in candidates if row.pk not in closed}
    if requested is None:
        selected = []
        for account in accounts:
            matches = [row.pk for row in eligible.values() if row.provider_account_id == account]
            if len(matches) != 1:
                raise ValidationError('请明确选择该陪玩本次售后对应的履约段')
            selected.extend(matches)
    else:
        selected = sorted(requested)
    if (any(pk not in eligible for pk in selected)
            or {eligible[pk].provider_account_id for pk in selected} != set(accounts)):
        raise ValidationError('所选履约段不属于本次受影响陪玩，或该段已确认终止')
    return sorted(selected)
