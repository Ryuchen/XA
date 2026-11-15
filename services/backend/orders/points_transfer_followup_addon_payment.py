"""Current-read proof for a new add-on after two transferred-order refunds."""
from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as RestValidationError


def facts(payment, revision, *, receipt_id=None):
    from users.receipt_usage_sources import _require_current_unlocked_reads
    from .addon_payment import _verify_intent, verify_receipt
    from .models import OrderOriginalShareTransfer, PointsNegotiationPosting
    _require_current_unlocked_reads()
    order, intent, tx = payment.intent.order, payment.intent, payment.buyer_transaction
    second = revision.posting
    terms = getattr(second.proposal, 'points_terms', None)
    first = PointsNegotiationPosting.objects.filter(points_refund_id=terms.prior_refund_id if terms else None).first()
    if (not first or first.points_refund.redemption.order_id != order.pk
            or second.points_refund.redemption.order_id != order.pk or not tx.created_at
            or tx.created_at <= second.created_at or not payment.paid_at or payment.paid_at > tx.created_at
            or payment.intent.buyer_id != order.customer_account_id
            or intent.segment.attribution.order_id != order.pk
            or intent.segment.provider_account_id != intent.provider_id
            or not intent.segment.started_at or intent.segment.started_at > tx.created_at
            or (intent.segment.released_at and intent.segment.released_at <= tx.created_at)
            or not OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
                                                             created_at__lt=tx.created_at).exists()
            or payment.refund_records.filter(created_at__lte=tx.created_at).exists()
            or payment.handovers.filter(status='ACCEPTED', accepted_at__lte=tx.created_at).exists()):
        raise ValidationError('转单双次退款后加购付款履约来源异常')
    for posting in (first, second):
        selected = set(posting.proposal.affected_segment_ids or [])
        if (intent.provider_id in posting.proposal.affected_accounts
                and (not selected or intent.segment_id in selected)):
            raise ValidationError('已终止履约段不能承接新加购')
    try:
        _verify_intent(intent, order)
        verify_receipt(payment)
    except RestValidationError as exc:
        raise ValidationError('转单双次退款后加购付款凭证异常') from exc
    allocations = list(payment.allocations.select_related('lot').order_by('pk'))
    return {'version': 1, 'order_id': order.pk, 'payment_id': payment.pk,
            'second_posting_id': second.pk, 'followup_revision_id': revision.pk,
            'intent_id': intent.pk, 'buyer_id': intent.buyer_id, 'provider_id': intent.provider_id,
            'segment_id': intent.segment_id, 'amount': intent.amount,
            'commission_snapshot': intent.commission_snapshot,
            'transaction': [tx.pk, tx.wallet_id, tx.amount, tx.balance_before, tx.balance_after,
                            tx.operator_account_id, tx.created_at.isoformat()],
            'allocations': [[row.pk, row.lot_id, row.kind, row.amount, row.lot.source_key] for row in allocations]}
