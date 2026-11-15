"""Original two-seat zero agreement, read after a later real transfer."""
from copy import copy

from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as RestValidationError


def verify(order, *, before):
    from .models import NegotiationExecution, OrderOriginalShareTransfer, OrderSegmentFinancialSnapshot
    from .negotiation_execution import _allocation_hash
    from .negotiation_proposals import _decisions, _verified
    from .negotiation_zero import marked_execution_id, verify_zero_termination
    from .settlement import compute_provider_income

    execution_id = marked_execution_id(order)
    if not execution_id:
        return None
    event = NegotiationExecution.objects.select_related('proposal__case', 'zero_order').filter(
        pk=execution_id, zero_order=order).first()
    if not event or not event.termination_processed_at:
        raise ValidationError('零退款协商来源缺失')
    if event.termination_processed_at >= before:
        # A later agreement must not be projected into an earlier transfer.
        return None
    if (event.refund_id or event.customer_transaction_id or event.proposal.status != 'ACCEPTED'
            or event.proposal.refund_units != 0 or event.accepted_at != event.proposal.accepted_at
            or event.terms_hash != event.proposal.terms_hash or event.rounding_to_store_units
            or not isinstance(event.zero_allocation, list) or len(event.zero_allocation) != 2
            or event.allocation_hash != _allocation_hash(event.zero_allocation)):
        raise ValidationError('转单前零退款协商事实异常')
    if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
                                                  created_at__lte=event.termination_processed_at).exists():
        raise ValidationError('首次零退款并非发生在转单之前')
    episode = copy(event.proposal.case)
    episode.created_at, episode.client_request_id = event.proposal.case_opened_at, event.proposal.case_request_id
    try:
        _verified(episode, event.proposal)
        _decisions(episode, event.proposal)
        verify_zero_termination(event)
    except (RestValidationError, ValueError, TypeError) as exc:
        raise ValidationError('转单前零退款确认或终止凭证异常') from exc
    snapshots = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order,
        segment__accepted_at__lt=event.accepted_at).select_related('segment'))
    if (len(snapshots) != 2 or len({row.segment.provider_account_id for row in snapshots}) != 2
            or sum(row.incoming_basis for row in snapshots) != order.amount):
        raise ValidationError('零退款原双陪履约快照异常')
    by_account = {row.segment.provider_account_id: row for row in snapshots}
    if {item.get('account_id') for item in event.zero_allocation if isinstance(item, dict)} != set(by_account):
        raise ValidationError('零退款原双陪分配身份异常')
    for item in event.zero_allocation:
        if not isinstance(item, dict):
            raise ValidationError('零退款原双陪分配异常')
        snap = by_account.get(item.get('account_id'))
        if (not snap or item.get('role') != 'PROVIDER' or item.get('amount') != 0
                or type(item.get('settled')) is not bool
                or item.get('key') != f"provider:{item.get('share_id')}"
                or not order.providers.filter(pk=item.get('share_id'), provider_account_id=item.get('account_id'),
                                              provider_id=item.get('user_id')).exists()
                or item.get('entitlement') != snap.income_at_entry or item.get('retained') != snap.income_at_entry
                or snap.income_at_entry != compute_provider_income(snap.incoming_basis, snap.commission_type,
                    snap.commission_rate, snap.commission_fixed)):
            raise ValidationError('零退款原双陪收益快照异常')
    shares = list(event.shares.select_related('assignment'))
    from .addon_main_deduction import deductions_at
    prior = deductions_at(order, before=event.created_at)
    if (len(shares) != 1 or event.proposal.affected_accounts != [shares[0].account_id]
            or shares[0].account_id not in by_account
            or by_account[shares[0].account_id].segment.released_at != event.accepted_at
            or shares[0].original_income != by_account[shares[0].account_id].income_at_entry - prior.get(shares[0].account_id, 0)
            or shares[0].original_income != shares[0].retained_income
            or any((shares[0].burden, shares[0].pending_deduction, shares[0].wallet_deduction,
                    shares[0].deposit_deduction))):
        raise ValidationError('零退款终止陪玩权益异常')
    return event
