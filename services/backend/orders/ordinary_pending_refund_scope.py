"""Identify independently owned unfinished add-on refund proposals."""


def independent(order, *, changed_payment_ids, changed_accounts, allow_same_payment=False):
    from .models import OrderAddonRefundProposal
    proposals = OrderAddonRefundProposal.objects.filter(payment__intent__order=order,
        status__in=('PENDING', 'ACCEPTED'), refund__isnull=True).select_related('payment')
    for proposal in proposals:
        accounts = set(proposal.participants.values_list('account_id', flat=True))
        if not accounts or (proposal.payment_id in changed_payment_ids and not allow_same_payment):
            return False
        if proposal.payment_id in changed_payment_ids or accounts.intersection(changed_accounts):
            from .addon_refund_cross_plan import _ordinary_capture
            from .addon_refund import _participants, _source_plan
            from rest_framework.exceptions import ValidationError
            people = list(proposal.participants.order_by('account_id').values(
                'account_id', 'basis', 'burden', 'excess_burden', 'share_plan'))
            try:
                if (not isinstance(proposal.cross_pending_plan, dict)
                        or proposal.cross_pending_plan.get('version') != 'ORDINARY_TRANSFER_V1'
                        or proposal.source_plan != _source_plan(proposal.payment, proposal.amount)
                        or people != _participants(proposal.payment, proposal.case, proposal.amount,
                                                   [item['account_id'] for item in people])
                        or proposal.cross_pending_plan != _ordinary_capture(order, proposal.payment, people)):
                    return False
            except ValidationError:
                return False
    return True
