"""As-of add-on original-share facts, including posted individual burdens."""
from collections import defaultdict

from .models import OrderParticipationSegment


def addon_segment_facts(payment, as_of):
    """Return verified net amount per participation segment, or None for review.

    The latest handover snapshot already contains earlier refund_basis reductions;
    restore those amounts before applying the recorded segment burdens once.
    """
    intent = payment.intent
    handover = (payment.handovers.filter(status='ACCEPTED', accepted_at__lte=as_of)
                .order_by('-version').first())
    base = defaultdict(int)
    if handover:
        shares = list(handover.shares.select_related('segment__attribution').order_by('position'))
        if (handover.accepted_at < payment.paid_at or not shares
                or sum(item.is_current for item in shares) != 1
                or handover.decisions.count() != 3
                or handover.decisions.exclude(accepted=True).exists()):
            return None
        for item in shares:
            if item.segment.attribution.order_id != intent.order_id or item.basis <= 0:
                return None
            base[item.segment_id] += item.basis
    else:
        base[intent.segment_id] = intent.amount
    burdens = defaultdict(int)
    refunds = list(payment.refund_records.select_related('proposal', 'buyer_transaction').filter(
        created_at__lte=as_of).order_by('created_at', 'pk'))
    for refund in refunds:
        if handover and refund.created_at == handover.accepted_at:
            return None  # Cross-table event order cannot be established at this precision.
        proposal = refund.proposal
        people = list(proposal.participants.order_by('pk'))
        if (proposal.status != 'ACCEPTED' or proposal.payment_id != payment.pk
                or proposal.amount != refund.amount or not people
                or refund.buyer_transaction.tx_type != 'REFUND'
                or refund.buyer_transaction.status != 'SUCCESS'
                or refund.buyer_transaction.amount != refund.amount
                or refund.buyer_transaction.wallet_id != payment.buyer_transaction.wallet_id
                or sum(person.burden for person in people) != refund.amount + refund.rounding_surplus):
            return None
        total_basis = 0
        for person in people:
            if not isinstance(person.share_plan, list) or not person.share_plan:
                return None
            seen = set()
            person_basis = person_burden = person_original = 0
            for row in person.share_plan:
                if (not isinstance(row, dict) or set(row) != {'segment_id', 'basis', 'income', 'refund_basis', 'burden_capacity', 'burden'}
                        or any(type(row[key]) is not int or row[key] < 0 for key in row)
                        or row['segment_id'] in seen or row['refund_basis'] > row['basis']
                        or row['burden_capacity'] > row['basis'] or row['burden'] > row['burden_capacity']
                        or row['income'] > row['basis']):
                    return None
                seen.add(row['segment_id'])
                person_basis += row['basis']
                person_burden += row['burden']
                person_original += row['refund_basis']
                burdens[row['segment_id']] += row['burden']
                if handover and refund.created_at <= handover.accepted_at:
                    base[row['segment_id']] += row['refund_basis']
            if (person_basis != person.basis or person_burden + person.excess_burden != person.burden
                    or person.addon_pending_deduction + person.main_pending_deduction
                    + person.wallet_deduction + person.deposit_deduction != person.burden):
                return None
            total_basis += person_original
            # The account and order are checked for each plan below.
            if OrderParticipationSegment.objects.filter(pk__in=seen, provider_account_id=person.account_id,
                    attribution__order_id=intent.order_id).count() != len(seen):
                return None
        if total_basis != refund.amount:
            return None
    before_refunds = sum(refund.amount for refund in refunds
                         if handover is None or refund.created_at <= handover.accepted_at)
    expected = intent.amount if handover is None else intent.amount - before_refunds
    if handover and sum(item.basis for item in shares) != expected:
        return None
    segment_ids = set(base) | set(burdens)
    segments = {item.pk: item for item in OrderParticipationSegment.objects.filter(pk__in=segment_ids)
                .select_related('attribution')}
    if len(segments) != len(segment_ids) or any(row.attribution.order_id != intent.order_id for row in segments.values()):
        return None
    if not handover and (segments[intent.segment_id].provider_account_id != intent.provider_id
                         or segments[intent.segment_id].attribution.order_id != intent.order_id):
        return None
    return [(segments[pk], max(0, base[pk] - burdens[pk])) for pk in segment_ids]
