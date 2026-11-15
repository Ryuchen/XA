"""Attribute confirmed ordinary refunds to the actual participation segments."""
from collections import defaultdict

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError


def facts(order, *, as_of):
    from console.deposit_clearance import _verified_refund_execution
    from .addon_refund import _allocate_refund_basis
    from .models import NegotiationExecution, OrderParticipationSegment, OrdinaryTransferFollowupRound
    from .negotiation_termination import verify_termination_evidence
    from .negotiation_zero import marked_execution_id, verify_zero_termination

    segments = list(OrderParticipationSegment.objects.filter(attribution__order=order,
        accepted_at__lte=as_of, started_at__isnull=False, started_at__lte=as_of)
        .select_related('original_transfer_out').order_by('accepted_at', 'pk'))
    if not segments:
        return None
    by_id, available = {}, {}
    for segment in segments:
        try:
            transfer = segment.original_transfer_out
        except OrderParticipationSegment.original_transfer_out.RelatedObjectDoesNotExist:
            transfer = None
        if transfer and transfer.created_at == as_of:
            return None
        amount = transfer.original_share_before if transfer and transfer.created_at > as_of else segment.original_share
        if type(amount) is not int or amount < 0 or segment.started_at < segment.accepted_at:
            return None
        by_id[segment.pk], available[segment.pk] = segment, amount

    executions = list(NegotiationExecution.objects.filter(proposal__case__order=order,
        termination_processed_at__isnull=False, termination_processed_at__lte=as_of)
        .select_related('proposal__case', 'refund').prefetch_related('shares__assignment').order_by('accepted_at', 'pk'))
    if len({item.accepted_at for item in executions}) != len(executions):
        return None
    closed, deductions, excess = set(), defaultdict(int), []
    refunds_seen = set()
    try:
        first_zero_id = marked_execution_id(order)
    except ValidationError:
        return None
    for execution in executions:
        proposal = execution.proposal
        if not execution.accepted_at or execution.accepted_at > execution.termination_processed_at:
            return None
        try:
            if execution.refund_id:
                checked = _verified_refund_execution(execution.refund)
                if not checked or checked.pk != execution.pk:
                    return None
                verify_termination_evidence(execution)
                refunds_seen.add(execution.refund_id)
            elif execution.pk == first_zero_id:
                verify_zero_termination(execution)
                from copy import copy
                from .negotiation_execution import _allocation_hash
                from .negotiation_proposals import _decisions, _verified
                episode = copy(proposal.case)
                episode.created_at, episode.client_request_id = proposal.case_opened_at, proposal.case_request_id
                _verified(episode, proposal)
                _decisions(episode, proposal)
                if (not isinstance(execution.zero_allocation, list)
                        or execution.allocation_hash != _allocation_hash(execution.zero_allocation)):
                    return None
            else:
                from .ordinary_transfer_followup_zero import verify_termination
                verify_termination(execution, lock=False)
        except (ValidationError, DjangoValidationError, ValueError, TypeError, KeyError):
            return None
        shares = list(execution.shares.all())
        terms = {item['account_id']: item for item in proposal.provider_terms}
        if (not shares or set(terms) != {share.account_id for share in shares}
                or len(terms) != len(shares) or any(share.assignment.order_id != order.pk
                    or share.assignment.provider_account_id != share.account_id
                    or share.burden != terms[share.account_id]['burden_units'] or share.burden % 10 for share in shares)
                or sum(share.burden for share in shares) !=
                   (execution.refund.amount if execution.refund_id else 0) + execution.rounding_to_store_units):
            return None
        chosen = proposal.affected_segment_ids
        if chosen is not None:
            if (not isinstance(chosen, list) or not chosen or len(chosen) != len(set(chosen))
                    or any(pk not in by_id for pk in chosen)):
                return None
            selected = [by_id[pk] for pk in chosen]
        else:
            selected = []
            for account in terms:
                candidates = [item for item in segments if item.provider_account_id == account
                              and item.started_at <= execution.accepted_at
                              and (item.released_at is None or item.released_at == execution.accepted_at)]
                if len(candidates) != 1:
                    return None
                selected.extend(candidates)
        if ({item.provider_account_id for item in selected} != set(terms)
                or any(item.pk in closed or item.started_at > execution.accepted_at for item in selected)):
            return None
        for share in shares:
            own = [item for item in selected if item.provider_account_id == share.account_id]
            if not own:
                return None
            weights = [(item.pk, available[item.pk]) for item in own if available[item.pk] > 0]
            capacity = sum(value for _, value in weights)
            applied = min(share.burden, capacity)
            if applied:
                try:
                    parts = _allocate_refund_basis(applied, weights)
                except ValidationError:
                    return None
                for segment_id, amount in parts.items():
                    available[segment_id] -= amount
                    deductions[segment_id] += amount
            if share.burden > applied:
                excess.append((share.account_id, share.burden - applied, tuple(item.pk for item in own)))
        # A confirmed closed episode cannot be selected again, including a
        # completed segment whose released_at was never used as a seat clock.
        closed.update(item.pk for item in selected)
    actual_refunds = set(order.refund_records.filter(created_at__lte=as_of).values_list('pk', flat=True))
    if actual_refunds != refunds_seen:
        return None
    return dict(deductions), closed, excess
