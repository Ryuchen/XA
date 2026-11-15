"""Verify zero-refund transfer follow-up without inventing a money row."""
from copy import copy

from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import OrdinaryTransferFollowupRound


def verify_execution(execution, *, lock=True):
    if lock and not transaction.get_connection().in_atomic_block:
        raise RuntimeError('零退款追加协商须在订单事务内核对')
    rows = OrdinaryTransferFollowupRound.objects.select_related('proposal__case', 'execution')
    row = (rows.select_for_update() if lock else rows).filter(execution=execution).first()
    if not row:
        raise ValidationError('零退款逐轮凭证缺失')
    proposal, facts = row.proposal, row.facts
    from .ordinary_transfer_followup import _hash
    from .negotiation_execution import _allocation_hash
    from .negotiation_proposals import _decisions, _verified
    episode = copy(proposal.case)
    episode.created_at, episode.client_request_id = proposal.case_opened_at, proposal.case_request_id
    _verified(episode, proposal)
    _decisions(episode, proposal, lock=lock)
    if (row.facts_hash != _hash(proposal.pk, facts) or row.order_id != execution.zero_order_id
            or row.order_id != proposal.case.order_id or execution.refund_id or execution.customer_transaction_id
            or proposal.refund_units != 0 or proposal.status != 'ACCEPTED'
            or execution.accepted_at != proposal.accepted_at or execution.terms_hash != proposal.terms_hash
            or not execution.zero_request_id or execution.rounding_to_store_units
            or not isinstance(execution.zero_allocation, list)
            or execution.allocation_hash != _allocation_hash(execution.zero_allocation)
            or facts.get('provider_terms') != proposal.provider_terms
            or facts.get('segment_pending_plan') != proposal.ordinary_segment_pending_plan
            or sorted(segment for ids in facts.get('segments', {}).values() for segment in ids)
               != (proposal.affected_segment_ids or [])):
        raise ValidationError('零退款追加协商凭证异常')
    terms = {term['account_id']: term for term in proposal.provider_terms}
    shares = execution.shares.select_related('assignment').order_by('account_id')
    shares = list(shares.select_for_update() if lock else shares)
    if [share.account_id for share in shares] != sorted(terms):
        raise ValidationError('零退款陪玩确认范围异常')
    from .ordinary_retained_segment_shares import verify_plan
    try:
        verify_plan(facts, shares)
    except ValueError as exc:
        raise ValidationError('零退款逐段承担凭证异常') from exc
    for share in shares:
        term = terms[share.account_id]
        if (share.assignment.order_id != row.order_id or share.assignment.provider_account_id != share.account_id
                or share.original_income != term['original_income_units'] or share.retained_income != term['retained_units']
                or share.retained_income != share.original_income or any((share.burden, share.pending_deduction,
                    share.wallet_deduction, share.deposit_deduction, share.wallet_transaction_id, share.deposit_deficit_id))):
            raise ValidationError('零退款陪玩收益凭证异常')
    return shares


def verify_termination(execution, *, lock=True):
    verify_execution(execution, lock=lock)
    if not execution.termination_processed_at or execution.termination_processed_at < execution.accepted_at:
        raise ValidationError('零退款追加结案凭证缺失')
    case, proposal = execution.proposal.case, execution.proposal
    entries = [item for item in case.history if isinstance(item, dict) and item.get('status') == 'RESOLVED'
               and item.get('created_at') == proposal.case_opened_at.isoformat()
               and item.get('client_request_id') == proposal.case_request_id] if isinstance(case.history, list) else []
    resolved = parse_datetime(entries[0].get('resolved_at') or '') if len(entries) == 1 else None
    if (not resolved or not execution.accepted_at <= resolved <= execution.termination_processed_at
            or entries[0].get('operator_id') != execution.operator_id
            or entries[0].get('resolution') != proposal.explanation
            or entries[0].get('affected_provider_account_ids') != proposal.affected_accounts
            or entries[0].get('affected_segment_ids') != proposal.affected_segment_ids):
        raise ValidationError('零退款追加售后历史异常')
    from console.models import AdminAuditLog
    audits = list(AdminAuditLog.objects.filter(resource='aftersales', object_id=str(case.pk),
        path=f'/api/admin/aftersales/{case.pk}/negotiation-execution/', method='POST', status_code=200,
        operator_account_id=execution.operator_id))
    expected = {'proposal_id': proposal.pk, 'execution_id': execution.pk,
                'client_request_id': execution.zero_request_id, 'refund_units': 0}
    if sum(item.request_body == expected for item in audits) != 1:
        raise ValidationError('零退款追加协商审计异常')
    return execution
