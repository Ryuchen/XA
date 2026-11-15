"""Verify the actual paid add-ons relevant to one membership cycle."""
from collections import defaultdict

from rest_framework.exceptions import ValidationError


def all_relevant_refunded(order, account):
    """Check this cycle's fully refunded shares without closing another's."""
    from orders.addon_payment import verify_receipt
    from orders.addon_handover import current_responsibilities, verify_first_handover, verify_later_handover
    from orders.models import OrderAddonPayment, OrderParticipationSegment
    from orders.aftersale_scope import applies_to
    from orders.addon_refund_share_allocation import verify as verify_share_allocation

    for payment in OrderAddonPayment.objects.select_for_update().filter(intent__order=order).select_related(
            'intent__segment', 'intent__order', 'buyer_transaction__wallet').order_by('pk'):
        segments = list(OrderParticipationSegment.objects.filter(attribution__order=order,
            provider_account_id=account.pk).values_list('pk', 'provider_business_cycle'))
        current_ids = {pk for pk, cycle in segments if cycle == account.business_cycle}
        referenced = {payment.intent.segment_id}
        referenced.update(payment.handovers.filter(status='ACCEPTED').values_list('shares__segment_id', flat=True))
        # Refund responsibility must also refer back to the real participant
        # segment; merely seeing the account in a proposal is not enough.
        for participant in payment.refund_records.filter(proposal__participants__account_id=account.pk
                ).values_list('proposal__participants__share_plan', flat=True):
            if not isinstance(participant, list) or any(not isinstance(row, dict) or 'segment_id' not in row
                                                        for row in participant):
                raise ValidationError('加购退款参与范围待核对')
            referenced.update(row['segment_id'] for row in participant)
        if any(pk in referenced and cycle is None for pk, cycle in segments):
            raise ValidationError('加购陪玩原业务周期待核对')
        involved = bool(current_ids.intersection(referenced))
        if not involved:
            continue
        if order.aftersales.filter(status='OPEN').exists() and any(
                applies_to(case, account.pk) for case in order.aftersales.filter(status='OPEN')):
            return False
        verify_receipt(payment)
        latest, shares = current_responsibilities(payment, lock=True)
        if latest:
            (verify_later_handover if latest.previous_id else verify_first_handover)(payment, latest)
        if any(row['provider_id'] == account.pk and row['segment_id'] in current_ids
               and (row['basis'] > 0 or row['income'] > 0)
               for row in shares):
            return False
        # A fully transferred, unperformed share needs no refund. The latest
        # verified handover recursively proves each preceding decision and
        # frozen responsibility. Every referenced segment in this membership
        # must itself have transferred with zero retained work; a missing
        # current share alone is not proof that it was relinquished.
        zero_retained_sources = set()
        handover = latest
        while handover:
            if handover.source_segment_id in current_ids and handover.retained_basis == 0:
                zero_retained_sources.add(handover.source_segment_id)
            handover = handover.previous
        own_segments = current_ids.intersection(referenced)
        fully_transferred = bool(latest and own_segments and own_segments.issubset(zero_retained_sources)
            and not payment.refund_proposals.filter(status__in=['PENDING', 'ACCEPTED'],
                refund__isnull=True, participants__account_id=account.pk).exists())
        allocations = list(payment.allocations.select_for_update().all())
        refunds = list(payment.refund_records.select_related('proposal', 'buyer_transaction').order_by('pk'))
        # A different provider's surviving share need not be refunded. Prove
        # this participant's refund and all actual original-source postings.
        if not allocations:
            return False
        if not refunds:
            if fully_transferred:
                continue
            return False
        sources = {row.pk: row for row in allocations}
        restored = defaultdict(int)
        own_refund_basis = 0
        for refund in refunds:
            tx, plan = refund.buyer_transaction, refund.proposal.source_plan
            if (tx.reversal_of_id or tx.reversals.exists() or not isinstance(plan, list)
                    or not plan or refund.proposal.amount != refund.amount
                    or tx.operator_account_id != refund.operator_id
                    or not refund.proposal.accepted_at or refund.proposal.accepted_at > refund.created_at
                    or refund.created_at < payment.paid_at):
                raise ValidationError('加购退款原路凭证待核对')
            verify_share_allocation(refund)
            for person in refund.proposal.participants.filter(account=account):
                if not isinstance(person.share_plan, list):
                    raise ValidationError('加购退款参与范围待核对')
                for entry in person.share_plan:
                    if not isinstance(entry, dict) or type(entry.get('refund_basis')) is not int:
                        raise ValidationError('加购退款本人份额待核对')
                    if entry.get('segment_id') in current_ids:
                        own_refund_basis += entry['refund_basis']
            seen, total = set(), 0
            for part in plan:
                if not isinstance(part, dict):
                    raise ValidationError('加购退款来源待核对')
                allocation_id, kind, amount = part.get('allocation_id'), part.get('kind'), part.get('amount_raw')
                source = sources.get(allocation_id)
                if (not source or allocation_id in seen or kind != source.kind
                        or type(amount) is not int or amount <= 0):
                    raise ValidationError('加购退款来源待核对')
                seen.add(allocation_id)
                total += amount
                restored[allocation_id] += amount
            if total != refund.amount:
                raise ValidationError('加购退款来源金额待核对')
        if any(restored[row.pk] != row.refunded_amount or row.refunded_amount > row.amount for row in allocations):
            raise ValidationError('加购退款与原付款来源不一致')
        if not own_refund_basis and not fully_transferred and sum(row.amount for row in refunds) != payment.intent.amount:
            # No remaining responsibility alone is insufficient: a transferred
            # share must not be mistaken for a refund of this provider's share.
            return False
    return True
