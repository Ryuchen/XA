"""Read-only evidence for ordinary wallet refunds used in ranking previews.

This is not a refund authorization. Callers need a consistent tenant DB
snapshot; a missing or ambiguous financial record yields no verified fact.
"""
from collections import defaultdict
from copy import copy
from dataclasses import dataclass
from datetime import datetime

from django.db import DatabaseError
from rest_framework.exceptions import ValidationError

from wallet.models import Transaction
from .direct_payment_models import DirectPaymentIntent
from .models import OrderRefund
from .points_service_payment import needs_integrated_processing


@dataclass(frozen=True)
class VerifiedCoinRefund:
    refunded_units: int
    is_full: bool
    refund_ids: tuple[int, ...]


def negotiated_provider_burdens(order, refund_ids, provider_ids, *, as_of):
    """Sum the paid refund responsibility recorded for each affected player.

    Refund execution already allocates each refund by original-price shares,
    including the per-player upward rounding and explicitly scoped cases.
    Only use those actual posted records; a refund without this allocation
    cannot be assigned to individual players by the ranking calculation.
    """
    from .models import NegotiationExecution

    provider_ids = set(provider_ids)
    totals = {account_id: 0 for account_id in provider_ids}
    ended = {}
    executions = list(NegotiationExecution.objects.filter(refund_id__in=refund_ids)
                      .select_related('refund', 'proposal__case').prefetch_related('shares__assignment', 'proposal__decisions'))
    if len(executions) != len(refund_ids):
        return None
    for execution in executions:
        from .negotiation_execution import _allocation_hash
        from .negotiation_proposals import _decisions, _verified
        from .negotiation_termination import verify_termination_evidence
        proposal = execution.proposal
        episode = copy(proposal.case)
        episode.created_at, episode.client_request_id = proposal.case_opened_at, proposal.case_request_id
        shares = list(execution.shares.all())
        try:
            _verified(episode, proposal)
            _decisions(episode, proposal)
            if execution.termination_processed_at:
                if execution.termination_processed_at > as_of:
                    return None
                verify_termination_evidence(execution)
        except (ValidationError, ValueError, TypeError):
            return None
        terms = {item['account_id']: item for item in proposal.provider_terms}
        if (execution.refund.order_id != order.pk or not shares or proposal.status != 'ACCEPTED'
                or execution.terms_hash != proposal.terms_hash or execution.accepted_at != proposal.accepted_at
                or execution.allocation_hash != _allocation_hash(execution.refund.negotiated_allocation)
                or set(terms) != {share.account_id for share in shares}
                or len({share.account_id for share in shares}) != len(shares)
                or any(share.account_id not in provider_ids or share.burden % 10
                       or share.assignment.order_id != order.pk
                       or share.assignment.provider_account_id != share.account_id
                       or share.burden != terms[share.account_id]['burden_units'] for share in shares)
                or sum(share.burden for share in shares) != execution.refund.amount + execution.rounding_to_store_units):
            return None
        for share in shares:
            totals[share.account_id] += share.burden
            if execution.termination_processed_at and share.assignment.released_at == execution.accepted_at:
                ended[share.account_id] = execution.accepted_at
    return totals, ended


def verified_coin_refund(order, *, as_of):
    """Verify all current ordinary-wallet refunds existed by ``as_of``.

    Coin transactions have no refund FK in the legacy flow. Match their
    amount and both operator identities as multisets, then verify time order
    within each group. Refuse points, direct payment and incomplete history.
    """
    if (not isinstance(as_of, datetime) or as_of.utcoffset() is None or not order.pk
            or type(order.amount) is not int or order.amount <= 0
            or not (order.customer_id or order.customer_account_id)
            or not order.refunded_at or order.refunded_at > as_of
            or order.payment_status not in ('PARTIALLY_REFUNDED', 'REFUNDED')):
        return None
    try:
        if DirectPaymentIntent.objects.filter(order_id=order.pk).exists() or needs_integrated_processing(order):
            return None
        payments = list(Transaction.objects.filter(order_id=order.pk, tx_type='PAY', status='SUCCESS')
                        .select_related('wallet').order_by('pk'))
        if len(payments) != 1:
            return None
        payment = payments[0]
        wallet = payment.wallet
        if ((order.customer_account_id and wallet.account_id != order.customer_account_id)
                or (order.customer_id and wallet.user_id != order.customer_id)
                or payment.amount != -order.amount or payment.reversal_of_id
                or payment.balance_after != payment.balance_before + payment.amount
                or payment.created_at > as_of or payment.reversals.filter(status='SUCCESS').exists()):
            return None
        refunds = list(OrderRefund.objects.filter(order_id=order.pk).order_by('created_at', 'pk'))
        if (not refunds or any(not row.created_at or row.created_at > as_of or row.created_at < payment.created_at
                or row.amount <= 0 or row.amount % 10 for row in refunds)):
            return None
        total = sum(row.amount for row in refunds)
        if (total > order.amount or (total == order.amount) != (order.payment_status == 'REFUNDED')):
            return None
        credits = list(Transaction.objects.filter(order_id=order.pk, wallet_id=wallet.pk, tx_type='REFUND',
            status='SUCCESS', amount__gt=0).order_by('created_at', 'pk'))
        if len(credits) != len(refunds):
            return None
        expected, actual = defaultdict(list), defaultdict(list)
        for row in refunds:
            expected[(row.amount, row.operator_id, row.operator_account_id)].append(row.created_at)
        for row in credits:
            if (row.created_at > as_of or row.created_at > order.refunded_at or row.reversal_of_id
                    or row.balance_after != row.balance_before + row.amount
                    or row.reversals.filter(status='SUCCESS').exists()):
                return None
            actual[(row.amount, row.operator_id, row.operator_account_id)].append(row.created_at)
        if set(expected) != set(actual):
            return None
        for key, created in expected.items():
            posted = actual[key]
            if len(created) != len(posted) or any(left > right for left, right in zip(sorted(created), sorted(posted))):
                return None
        return VerifiedCoinRefund(total, total == order.amount, tuple(row.pk for row in refunds))
    except DatabaseError:
        # A missing evidence table is not proof of an ordinary refund.
        return None


def zero_refund_terminations(order_ids, *, as_of):
    """Read scoped zero-refund closures without locking unrelated orders."""
    from .models import NegotiationExecution
    from .negotiation_execution import _allocation_hash
    from .negotiation_proposals import _decisions, _verified
    from .negotiation_zero import verify_zero_termination

    result = {}
    executions = NegotiationExecution.objects.filter(zero_order_id__in=order_ids,
        termination_processed_at__isnull=False, termination_processed_at__lte=as_of).select_related(
        'zero_order', 'proposal__case').prefetch_related('shares__assignment', 'proposal__decisions')
    for execution in executions:
        order, proposal = execution.zero_order, execution.proposal
        shares = list(execution.shares.all())
        episode = copy(proposal.case)
        episode.created_at, episode.client_request_id = proposal.case_opened_at, proposal.case_request_id
        try:
            _verified(episode, proposal)
            _decisions(episode, proposal)
            verify_zero_termination(execution)
            terms = {item['account_id']: item for item in proposal.provider_terms}
            if (proposal.status != 'ACCEPTED' or proposal.refund_units != 0 or proposal.case.order_id != order.pk
                    or execution.terms_hash != proposal.terms_hash or execution.accepted_at != proposal.accepted_at
                    or execution.allocation_hash != _allocation_hash(execution.zero_allocation)
                    or not shares or set(terms) != {row.account_id for row in shares}
                    or any(row.assignment.order_id != order.pk or row.assignment.provider_account_id != row.account_id
                           or row.burden or row.pending_deduction or row.wallet_deduction or row.deposit_deduction
                           or row.retained_income != row.original_income
                           or terms[row.account_id]['retained_units'] != row.retained_income for row in shares)):
                continue
            for share in shares:
                if share.assignment.released_at == execution.accepted_at:
                    result.setdefault(order.pk, {})[share.account_id] = execution.accepted_at
        except (ValidationError, ValueError, TypeError, KeyError):
            continue
    return result
