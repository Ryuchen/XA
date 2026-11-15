"""Resolve the negotiation ending each service segment, not merely its account."""
from .models import OrderTransferNegotiationEvidence, OrdinaryTransferFollowupRound


def endings(order, receipts):
    by_id = {receipt.pk: receipt for receipt in receipts}
    result = {}
    first = (OrderTransferNegotiationEvidence.objects.filter(order=order)
             .select_related('execution__proposal').first())
    if first and first.execution_id in by_id:
        receipt = by_id[first.execution_id]
        for segment in first.snapshot.get('segments', []):
            if (segment.get('id') in (receipt.proposal.affected_segment_ids or [])
                    and segment.get('started_at') and segment.get('account_id') in receipt.proposal.affected_accounts):
                result[segment['id']] = receipt
    if not first:
        from .models import OrderOriginalShareTransfer, OrderParticipationSegment
        from .ordinary_zero_pretransfer import verify as verify_pretransfer_zero
        transfer = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).order_by('created_at', 'pk').first()
        receipt = verify_pretransfer_zero(order, before=transfer.created_at) if transfer else None
        if not receipt and transfer and order.refund_records.exists():
            from .ordinary_positive_pretransfer import verify as verify_positive_pretransfer
            receipt = verify_positive_pretransfer(order, before=transfer.created_at)
        if receipt and receipt.pk in by_id:
            segments = list(OrderParticipationSegment.objects.filter(attribution__order=order,
                provider_account_id__in=receipt.proposal.affected_accounts, released_at=receipt.accepted_at,
                started_at__isnull=False))
            for segment in segments:
                result[segment.pk] = by_id[receipt.pk]
    for row in (OrdinaryTransferFollowupRound.objects.filter(order=order, execution_id__in=by_id)
                .select_related('proposal').order_by('sequence')):
        for account in row.proposal.affected_accounts:
            for segment_id in row.facts.get('segments', {}).get(str(account), []):
                result[segment_id] = by_id[row.execution_id]
    return result
