"""Posted points refunds and participant burdens used by grab ranking."""
from copy import copy
from datetime import datetime

from django.db import DatabaseError

from .grab_refunded_facts import VerifiedCoinRefund
from .models import OrderPointsRedemption, OrderPointsRefund, OrderRefund, PointsNegotiationPosting
from .refund_math import RefundCalculationError, payment_refund


def points_refund_fact(order, *, as_of):
    """Read persisted payment and refund facts without locking other orders."""
    if not isinstance(as_of, datetime) or as_of.utcoffset() is None or order.payment_status not in ('PARTIALLY_REFUNDED', 'REFUNDED'):
        return None
    try:
        receipt = OrderPointsRedemption.objects.select_related('payment__wallet', 'ledger').filter(order=order).first()
        if not receipt:
            return None
        tx, ledger = receipt.payment, receipt.ledger
        marker = order.funding_terms.get('points_redemption_v1') if isinstance(order.funding_terms, dict) else None
        if (marker != {'receipt_id': receipt.pk} or receipt.paid_units != order.amount
                or receipt.deal_units != receipt.paid_units + receipt.discount_units
                or tx.order_id != order.pk or tx.tx_type != 'PAY' or tx.status != 'SUCCESS'
                or tx.wallet.account_id != order.customer_account_id or tx.wallet.user_id != order.customer_id
                or tx.amount != -receipt.paid_units or tx.balance_after != tx.balance_before + tx.amount
                or tx.reversal_of_id or tx.reversals.exists() or tx.created_at > as_of
                or ledger.order_id != order.pk or ledger.account_id != order.customer_account_id
                or ledger.kind != 'ORDER_POINTS_REDEEM' or ledger.amount != -receipt.points_hundredths
                or ledger.event_key != f'order:{order.pk}:points-redemption' or ledger.created_at > as_of):
            return None
        rows = list(OrderRefund.objects.filter(order=order).order_by('pk'))
        proofs = {row.refund_id: row for row in OrderPointsRefund.objects.filter(redemption=receipt)
                  .select_related('coin_transaction', 'points_ledger')}
        if not rows or set(proofs) != {row.pk for row in rows}:
            return None
        equivalent = coin = points = 0
        for row in rows:
            proof = proofs[row.pk]
            if row.created_at > as_of or proof.created_at > as_of or row.created_at < tx.created_at:
                return None
            expected = payment_refund(paid_coin_units=receipt.paid_units, paid_point_hundredths=receipt.points_hundredths,
                point_value_units=receipt.discount_units, refund_equivalent_units=proof.equivalent_units,
                previously_refunded_equivalent_units=equivalent, previously_refunded_coin_units=coin,
                previously_refunded_point_hundredths=points)
            if ((proof.coin_units, proof.points_hundredths, proof.is_final) !=
                    (expected.coin_units, expected.point_hundredths, expected.is_final)
                    or row.amount != proof.coin_units or proof.coin_units !=
                    proof.prepaid_units + proof.gift_units + proof.compensation_units):
                return None
            returned, point_return = proof.coin_transaction, proof.points_ledger
            if proof.coin_units and (not returned or returned.wallet_id != tx.wallet_id or returned.order_id != order.pk
                    or returned.tx_type != 'REFUND' or returned.status != 'SUCCESS' or returned.amount != proof.coin_units
                    or returned.balance_after != returned.balance_before + returned.amount or returned.reversal_of_id
                    or returned.reversals.exists() or returned.operator_account_id != row.operator_account_id
                    or returned.operator_id != row.operator_id or returned.created_at > as_of):
                return None
            if proof.points_hundredths and (not point_return or point_return.order_id != order.pk
                    or point_return.account_id != order.customer_account_id or point_return.kind != 'ORDER_POINTS_RETURN'
                    or point_return.event_key != f'order:{order.pk}:points-return:{row.pk}'
                    or point_return.amount != proof.points_hundredths or point_return.operator_id != row.operator_account_id
                    or point_return.created_at > as_of):
                return None
            equivalent += proof.equivalent_units
            coin += proof.coin_units
            points += proof.points_hundredths
        final = proofs[rows[-1].pk].is_final
        if final != (order.payment_status == 'REFUNDED'):
            return None
        return VerifiedCoinRefund(equivalent, final, tuple(row.pk for row in rows))
    except (DatabaseError, RefundCalculationError, TypeError, ValueError):
        return None


def points_burdens(order, refund_ids, members):
    """Use actual accepted allocation; return burdens and termination times."""
    allowed = set(members)
    totals, ended = {account_id: 0 for account_id in allowed}, {}
    postings = list(PointsNegotiationPosting.objects.filter(points_refund__refund_id__in=refund_ids)
                    .select_related('proposal__case', 'points_refund__refund').prefetch_related('shares__assignment', 'proposal__decisions'))
    if len(postings) != len(refund_ids):
        return None
    for posting in postings:
        from .negotiation_proposals import _digest
        from .points_negotiation_execution import participant_digest
        proposal, proof = posting.proposal, posting.points_refund
        shares, decisions = list(posting.shares.all()), list(proposal.decisions.all())
        episode = copy(proposal.case)
        episode.created_at, episode.client_request_id = proposal.case_opened_at, proposal.case_request_id
        terms = {term.get('account_id'): term for term in proposal.provider_terms} if isinstance(proposal.provider_terms, list) and all(isinstance(term, dict) for term in proposal.provider_terms) else {}
        if (proposal.status != 'ACCEPTED' or proposal.case.order_id != order.pk
                or proposal.terms_hash != _digest(episode, proposal.version, proposal.affected_accounts,
                                                  proposal.refund_units, proposal.provider_terms, proposal.explanation,
                                                  proposal.affected_segment_ids)
                or posting.participants_hash != participant_digest(proposal, posting.participants)
                or set(terms) != {row.account_id for row in shares}
                or proof.refund.order_id != order.pk or proposal.refund_units != proof.equivalent_units
                or posting.terms_hash != proposal.terms_hash or posting.accepted_at != proposal.accepted_at
                or not shares or len({row.account_id for row in shares}) != len(shares)
                or any(row.account_id not in allowed or row.assignment.order_id != order.pk
                       or row.assignment.provider_account_id != row.account_id or row.burden % 10
                       or row.burden != row.pending_deduction + row.wallet_deduction + row.deposit_deduction
                       or (row.original_income, row.burden, row.retained_income) !=
                          (terms[row.account_id].get('original_income_units'), terms[row.account_id].get('burden_units'),
                           terms[row.account_id].get('retained_units'))
                       for row in shares)
                or sum(row.burden for row in shares) != proof.equivalent_units + posting.rounding_to_store_units
                or {(row.account_id, row.role) for row in decisions if row.accepted} !=
                   {(order.customer_account_id, 'BOSS')} | {(row.account_id, 'PROVIDER') for row in shares}
                or any(row.accepted is not True for row in decisions)):
            return None
        for share in shares:
            totals[share.account_id] += share.burden
            if share.assignment.released_at == posting.accepted_at:
                ended[share.account_id] = posting.accepted_at
    return dict(totals), ended
