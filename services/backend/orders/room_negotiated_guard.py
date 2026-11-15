"""Verify that a room may finish through an already accepted negotiation."""
from rest_framework.exceptions import ValidationError

from .models import NegotiationExecution
from .refunds import authorized_refund


def verified_room_subsidy_termination(order, execution_id, operator):
    """Verify the single active safety-box participant and all parties' consent."""
    if not execution_id or not operator or not operator.is_active or not operator.can_login or not authorized_refund(operator):
        return False
    from .models import SubsidyAgreementExecution
    execution = SubsidyAgreementExecution.objects.select_for_update().select_related('proposal__case', 'order').filter(
        pk=execution_id, order=order, operator=operator).first()
    if not execution or execution.completed_before_agreement:
        return False
    from .subsidy_execution import verify_execution
    try:
        affected = verify_execution(execution, provisional=True)
    except (ValidationError, ValueError):
        return False
    active = list(order.providers.filter(released_at__isnull=True).values_list('provider_account_id', flat=True))
    return len(active) == 1 and active[0] in affected


def verified_room_termination(order, execution_id, operator):
    """Called under the order/money lock during the atomic termination only."""
    if (not execution_id or not operator or not operator.is_active or not operator.can_login
            or not authorized_refund(operator)):
        return False
    execution = NegotiationExecution.objects.select_for_update().select_related('proposal__case').filter(
        pk=execution_id, proposal__case__order=order, operator=operator).first()
    if (not execution or execution.termination_processed_at
            or execution.completed_by_negotiation or execution.proposal.case.status != 'OPEN'):
        return False
    try:
        if execution.refund_id:
            from .negotiation_execution import verify_execution
            shares = verify_execution(execution)
        elif execution.zero_order_id == order.pk:
            from .negotiation_zero import verify_zero_execution
            shares = verify_zero_execution(execution)
        else:
            return False
    except (ValidationError, ValueError):
        return False
    if not shares or order.providers.filter(released_at__isnull=True).exists():
        return False
    from .models import OrdinaryTransferFollowupRound
    followup = OrdinaryTransferFollowupRound.objects.select_for_update().filter(execution=execution, order=order).first()
    if not followup:
        return all(share.assignment.released_at == execution.accepted_at for share in shares)
    # A later agreement may include both an earlier transferred-out segment
    # and the current segment. The former must retain its actual transfer
    # time, rather than being rewritten to this agreement's acceptance time.
    from .models import OrderOriginalShareTransfer, OrderParticipationSegment
    ids = execution.proposal.affected_segment_ids
    if (not isinstance(ids, list) or not ids or any(type(item) is not int or item <= 0 for item in ids)
            or len(set(ids)) != len(ids)):
        return False
    segments = list(OrderParticipationSegment.objects.select_for_update().filter(
        attribution__order=order, pk__in=ids))
    accounts = {share.account_id for share in shares}
    if (len(segments) != len(ids) or {item.provider_account_id for item in segments} != accounts
            or not any(item.released_at == execution.accepted_at for item in segments)
            or any(not item.started_at or not item.released_at or item.released_at < item.started_at
                   or item.released_at > execution.accepted_at for item in segments)
            or any(not share.assignment.released_at or share.assignment.released_at > execution.accepted_at
                   for share in shares)):
        return False
    prior = [item for item in segments if item.released_at < execution.accepted_at]
    transfers = dict(OrderOriginalShareTransfer.objects.filter(previous_segment__in=prior)
                     .values_list('previous_segment_id', 'created_at'))
    return all(transfers.get(item.pk) == item.released_at for item in prior)


def verified_room_points_termination(order, posting_id, operator):
    """Verify an isolated points refund and all affected party confirmations."""
    if (not posting_id or not operator or not operator.is_active or not operator.can_login
            or not authorized_refund(operator)):
        return False
    from .models import PointsNegotiationPosting
    posting = PointsNegotiationPosting.objects.select_for_update().select_related('proposal__case').filter(
        pk=posting_id, proposal__case__order=order, operator=operator).first()
    if (not posting or posting.termination_processed_at or posting.completed_by_negotiation
            or posting.proposal.case.status != 'OPEN'):
        return False
    from .points_negotiation_execution import verify_posting_locked
    try:
        posting = verify_posting_locked(posting)
        shares = list(posting.shares.select_related('assignment'))
        from .models import OrderOriginalShareTransfer
        if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
            from .points_negotiation_termination import _verify_transfer_termination_segments
            _verify_transfer_termination_segments(order, posting, shares)
            releases_valid = True
        else:
            releases_valid = all(share.assignment.released_at == posting.accepted_at for share in shares)
    except (ValidationError, ValueError):
        return False
    return bool(shares and not order.providers.filter(released_at__isnull=True).exists()
                and releases_valid)


def verified_room_followup_termination(order, link_id, operator):
    """Require both reconciled negotiations before completing a room order."""
    if (not link_id or not operator or not operator.is_active or not operator.can_login
            or not authorized_refund(operator)):
        return False
    from .models import NegotiationFollowupLink
    link = NegotiationFollowupLink.objects.select_for_update().select_related(
        'execution__proposal__case').filter(pk=link_id, order=order, execution__operator=operator).first()
    if not link:
        return False
    current = link.execution
    if current.termination_processed_at or current.completed_by_negotiation or current.proposal.case.status != 'OPEN':
        return False
    from .negotiation_followup_accounting import verify_followup_accounting
    try:
        _, share = verify_followup_accounting(link)
    except (ValidationError, ValueError):
        return False
    return (share.assignment.released_at == current.accepted_at
            and not order.providers.filter(released_at__isnull=True).exists())
