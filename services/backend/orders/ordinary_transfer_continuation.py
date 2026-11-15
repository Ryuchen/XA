"""Continue fulfilment after a closed ordinary transferred-order negotiation."""
from rest_framework.exceptions import ValidationError
from .models import OrderTransferNegotiationEvidence, OrdinaryTransferFollowupRound


def applies(order):
    from .models import OrdinaryPostRefundTransferRevision
    from .points_service_payment import needs_integrated_processing
    if not order or needs_integrated_processing(order):
        return False
    return (OrderTransferNegotiationEvidence.objects.filter(order=order).exists()
            or OrdinaryPostRefundTransferRevision.objects.filter(order=order,
                snapshot__positive_pretransfer_execution_id__isnull=False).exists()
            or OrdinaryPostRefundTransferRevision.objects.filter(order=order,
                snapshot__zero_pretransfer_execution_id__isnull=False).exists())


def verify_locked(order):
    """Called under the order lock, before taking participation or wallet locks."""
    from .ordinary_transfer_capability import permits
    if not permits(order, allow_addon_refund=True, allow_addon_handover=True):
        raise ValidationError('转单协商后的履约凭证待核对')
    from .financial_lock import lock_order_money
    lock_order_money()
    first = (OrderTransferNegotiationEvidence.objects.select_for_update()
             .select_related('execution__refund', 'execution__proposal')
             .filter(order=order).first())
    from .negotiation_transfer_evidence import verify_locked as verify_first
    from .negotiation_execution import verify_execution
    from .negotiation_termination import verify_termination_evidence
    from .negotiation_zero import verify_zero_execution, verify_zero_termination
    if first:
        if not first.execution.termination_processed_at:
            raise ValidationError('转单协商尚未结案')
        verify_first(first.execution)
        previous = first.execution
        if previous.refund_id:
            verify_execution(previous)
            verify_termination_evidence(previous)
        else:
            verify_zero_execution(previous)
            verify_zero_termination(previous)
    else:
        from .models import OrderOriginalShareTransfer, OrdinaryPostRefundTransferRevision
        transfer = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order)
        transfer = transfer.order_by('created_at', 'pk').first()
        revision = OrdinaryPostRefundTransferRevision.objects.filter(order=order,
            snapshot__positive_pretransfer_execution_id__isnull=False).order_by('pk').first()
        if revision:
            marker = revision.snapshot.get('positive_pretransfer_execution_id')
            from .ordinary_positive_pretransfer import verify as verify_pretransfer
        else:
            revision = OrdinaryPostRefundTransferRevision.objects.filter(order=order,
                snapshot__zero_pretransfer_execution_id__isnull=False).order_by('pk').first()
            marker = revision.snapshot.get('zero_pretransfer_execution_id') if revision else None
            from .ordinary_zero_pretransfer import verify as verify_pretransfer
        if not transfer or not marker:
            raise ValidationError('转单前协商凭证缺失')
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            previous = verify_pretransfer(order, before=transfer.created_at)
        except DjangoValidationError as exc:
            raise ValidationError('转单前协商凭证待核对') from exc
        if not previous or previous.pk != marker:
            raise ValidationError('转单前协商与资金版本不一致')
    from .ordinary_transfer_followup import _hash
    rows = (OrdinaryTransferFollowupRound.objects.select_for_update()
            .filter(order=order, execution__isnull=False).select_related('execution').order_by('sequence'))
    for number, row in enumerate(rows, 1):
        if (row.sequence != number or row.previous_id != previous.pk
                or row.facts_hash != _hash(row.proposal_id, row.facts)
                or not row.execution.termination_processed_at):
            raise ValidationError('追加协商轮次凭证待核对')
        previous = row.execution
        if previous.refund_id:
            verify_execution(previous)
            verify_termination_evidence(previous)
        else:
            verify_zero_execution(previous)
            verify_zero_termination(previous)
    return True
