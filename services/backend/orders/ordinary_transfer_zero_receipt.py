"""Read-only source proof for a first zero-refund transferred agreement."""
from copy import copy
from dataclasses import asdict

from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as RestValidationError


def verify(execution):
    from .ordinary_retained_segment_shares import verify_execution_plan
    try:
        verify_execution_plan(execution)
    except ValueError as exc:
        raise ValidationError('零退款逐段承担凭证异常') from exc
    from .models import OrderOriginalShareTransfer, OrderTransferNegotiationEvidence
    from .negotiation_execution import _allocation_hash
    from .negotiation_proposals import _decisions, _verified
    from .negotiation_transfer_evidence import _digest
    from .ordinary_transfer_event_scope import segment_lines_at
    from .settlement import SettlementError
    order, proposal = execution.zero_order, execution.proposal
    if not order:
        raise ValidationError('零退款订单来源缺失')
    episode = copy(proposal.case)
    episode.created_at, episode.client_request_id = proposal.case_opened_at, proposal.case_request_id
    try:
        _verified(episode, proposal)
        _decisions(episode, proposal)
    except (RestValidationError, ValueError, TypeError) as exc:
        raise ValidationError('零退款协商确认异常') from exc
    evidence = OrderTransferNegotiationEvidence.objects.filter(execution=execution, order=order).first()
    if (not evidence or evidence.snapshot_hash != _digest(evidence.snapshot)
            or evidence.snapshot.get('order_id') != order.pk
            or evidence.snapshot.get('provider_terms') != proposal.provider_terms
            or not execution.termination_processed_at or execution.refund_id or execution.customer_transaction_id
            or proposal.status != 'ACCEPTED' or proposal.refund_units or execution.rounding_to_store_units
            or execution.terms_hash != proposal.terms_hash or execution.accepted_at != proposal.accepted_at
            or not execution.zero_request_id or not isinstance(execution.zero_allocation, list)
            or execution.allocation_hash != _allocation_hash(execution.zero_allocation)):
        raise ValidationError('零退款转单协商来源异常')
    if evidence.snapshot.get('ordinary_paid_settlement_snapshot') != proposal.ordinary_paid_settlement_snapshot:
        raise ValidationError('零退款转单协商已付快照不一致')
    from .ordinary_first_paid_settlement import verify_receipt_snapshot
    try:
        verify_receipt_snapshot(order, execution)
    except RestValidationError as exc:
        raise ValidationError('零退款转单协商已付流水待核对') from exc
    from .ordinary_transfer_event_scope import transfers_at
    transfers = transfers_at(order, execution.created_at)
    if {item.get('id') for item in evidence.snapshot.get('transfers', [])} != {item.pk for item in transfers}:
        raise ValidationError('零退款转单事件版本异常')
    try:
        lines = segment_lines_at(order, execution.created_at)
    except SettlementError as exc:
        raise ValidationError('零退款转单分段收益异常') from exc
    if [asdict(line) for line in lines] != evidence.snapshot.get('lines'):
        raise ValidationError('零退款转单分段版本异常')
    from engagement.funding_wallet import validate_order_funding_sources
    from engagement.models import OrderFundingAllocation, OrderPointAllocation
    from wallet.models import Transaction
    sources = list(OrderFundingAllocation.objects.filter(order=order).select_related('lot'))
    try:
        validate_order_funding_sources(order, sources)
    except RestValidationError as exc:
        raise ValidationError('零退款原付款来源异常') from exc
    payments = list(Transaction.objects.filter(order=order, tx_type='PAY', status='SUCCESS').select_related('wallet'))
    if (OrderPointAllocation.objects.filter(order=order).exists() or sum(row.amount for row in sources) != order.amount
            or len(payments) != 1 or payments[0].amount != -order.amount
            or payments[0].wallet.account_id != order.customer_account_id or payments[0].wallet.user_id != order.customer_id
            or payments[0].balance_after != payments[0].balance_before + payments[0].amount
            or payments[0].reversal_of_id or payments[0].reversals.exists()):
        raise ValidationError('零退款原付款流水异常')
    terms = {item['account_id']: item for item in proposal.provider_terms}
    shares = list(execution.shares.select_related('assignment'))
    if not shares or set(terms) != {share.account_id for share in shares}:
        raise ValidationError('零退款陪玩范围异常')
    from .addon_main_segment_deduction import verified_deductions
    prior = verified_deductions(order, before=execution.created_at)
    selected = set(proposal.affected_segment_ids or [])
    income = {}
    for line in lines:
        if line.segment_id in selected:
            income[line.account_id] = income.get(line.account_id, 0) + line.income - prior.get(line.segment_id, 0)
    if selected != {line.segment_id for line in lines if line.segment_id in selected}:
        raise ValidationError('零退款受影响履约段异常')
    for share in shares:
        term = terms[share.account_id]
        if (share.assignment.order_id != order.pk or share.assignment.provider_account_id != share.account_id
                or share.original_income != term['original_income_units'] or share.retained_income != term['retained_units']
                or income.get(share.account_id) != share.original_income
                or share.original_income != share.retained_income
                or any((share.burden, share.pending_deduction, share.wallet_deduction, share.deposit_deduction,
                        share.wallet_transaction_id, share.deposit_deficit_id))):
            raise ValidationError('零退款陪玩收益异常')
    return execution
