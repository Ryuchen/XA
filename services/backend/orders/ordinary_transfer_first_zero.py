"""Zero-refund first agreement for an already transferred ordinary order."""
from copy import copy

from rest_framework.exceptions import ValidationError
from stores.db import transaction


def verify_execution(execution):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('转单零退款凭证须在订单事务内核验')
    from .negotiation_proposals import _verified, _decisions
    from .negotiation_execution import _allocation_hash
    from .negotiation_transfer_evidence import verify_locked
    proposal = execution.proposal
    case = copy(proposal.case)
    case.created_at, case.client_request_id = proposal.case_opened_at, proposal.case_request_id
    _verified(case, proposal)
    _decisions(case, proposal, lock=True)
    if (not execution.zero_order_id or execution.zero_order_id != proposal.case.order_id or execution.refund_id
            or execution.customer_transaction_id or proposal.refund_units != 0 or proposal.status != 'ACCEPTED'
            or execution.terms_hash != proposal.terms_hash or execution.accepted_at != proposal.accepted_at
            or not execution.zero_request_id or execution.rounding_to_store_units
            or not isinstance(execution.zero_allocation, list)
            or execution.allocation_hash != _allocation_hash(execution.zero_allocation)):
        raise ValidationError('转单零退款协商凭证异常')
    evidence = verify_locked(execution)
    if not evidence:
        raise ValidationError('转单零退款履约段快照缺失')
    terms = {item['account_id']: item for item in proposal.provider_terms}
    shares = list(execution.shares.select_for_update().select_related('assignment').order_by('account_id'))
    if [share.account_id for share in shares] != sorted(terms):
        raise ValidationError('转单零退款陪玩范围异常')
    for share in shares:
        term = terms[share.account_id]
        if (share.assignment.order_id != execution.zero_order_id or share.assignment.provider_account_id != share.account_id
                or share.original_income != term['original_income_units'] or share.retained_income != term['retained_units']
                or share.original_income != share.retained_income or any((share.burden, share.pending_deduction,
                    share.wallet_deduction, share.deposit_deduction, share.wallet_transaction_id, share.deposit_deficit_id))):
            raise ValidationError('转单零退款陪玩收益异常')
    return shares
