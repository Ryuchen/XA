"""Current-read evidence for partially refunded add-ons before a transfer handover."""
from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as RestValidationError


def verify_zero_transfer_revision(order, transfer):
    """Verify the real pre-refund transfer version with no allocated receipt."""
    from .models import OrderPointsTransferFundingRevision, PointsNegotiationPosting
    from .points_transfer_refund_evidence import _facts_without_receipt_locked
    from .points_funding_plan import PointsFundingPlanError
    _facts_without_receipt_locked(order)
    revision = OrderPointsTransferFundingRevision.objects.filter(order=order, last_transfer=transfer).first()
    if not revision or revision.allocations != [] or revision.releases.exists():
        raise ValidationError('无到账转单资金版本缺失或释放记录异常')
    try:
        first = PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order)\
            .order_by('created_at', 'pk').first()
        if first:
            from .points_transfer_refund_access import verification_scope
            from .points_transfer_refund_evidence import verify_transfer_revisions_after_refund_locked
            with verification_scope(order):
                verify_transfer_revisions_after_refund_locked(order.pk, first.participants,
                    refund_id=first.points_refund.refund_id)
        else:
            from .points_transfer_revision import verify_revisions_locked
            verify_revisions_locked(order)
    except (RestValidationError, PointsFundingPlanError) as exc:
        raise ValidationError('无到账转单历史资金版本异常') from exc
    return revision


def facts(payment, handover, *, receipt_id):
    """Prove earlier refunds under a receipt witness or the zero-allocation order lock."""
    from users.receipt_usage_ledger import _locked_receipt
    from users.receipt_usage_sources import _require_current_unlocked_reads
    from .models import OrderAddonFundingReleaseRun, OrderOriginalShareTransfer, OrderAddonPayment, OrderAddonHandover
    from .addon_funding_release import verify_run
    from .points_addon_receipt_usage import facts as refund_facts

    if receipt_id is None:
        from .points_transfer_refund_evidence import _facts_without_receipt_locked
        _facts_without_receipt_locked(payment.intent.order)
    else:
        _locked_receipt(receipt_id)
    _require_current_unlocked_reads()
    order = payment.intent.order
    transfers = list(OrderOriginalShareTransfer.objects.filter(previous_segment=handover.source_segment,
        created_at__lt=handover.created_at).order_by('created_at', 'pk'))
    prior = list(payment.refund_records.filter(created_at__lt=handover.accepted_at)
                 .select_related('proposal', 'order', 'payment__intent').order_by('created_at', 'pk'))
    all_prior = list(order.addon_refunds.filter(created_at__lt=handover.accepted_at)
                     .select_related('proposal', 'order', 'payment__intent').order_by('created_at', 'pk'))
    earlier_handovers = list(OrderAddonHandover.objects.filter(payment__intent__order=order, status='ACCEPTED',
        accepted_at__lt=handover.accepted_at).order_by('accepted_at', 'pk'))
    from .points_transfer_pending_handover_events import is_unaffected
    if (payment.intent.order_id != order.pk or handover.payment_id != payment.pk
            or not handover.created_at or not handover.accepted_at or handover.previous_id
            or not order.addon_intents.exists()
            or OrderAddonPayment.objects.filter(intent__order=order).count() != order.addon_intents.count()
            or len(transfers) != 1 or not all_prior
            or order.refund_records.filter(created_at__lte=handover.accepted_at).exists()
            or len({row.created_at for row in all_prior}) != len(all_prior)
            or any(row.created_at == transfers[0].created_at or row.created_at == handover.created_at
                   or (row.created_at > handover.created_at and not is_unaffected(row, handover)) for row in all_prior)
            or any(item.created_at <= row.created_at <= item.accepted_at and not is_unaffected(row, item)
                   for row in all_prior for item in earlier_handovers)
            or payment.refund_records.filter(created_at=handover.accepted_at).exists()):
        raise ValidationError('接替前退款的付款或事件顺序尚未接通')
    if any(row.created_at < transfers[0].created_at for row in all_prior):
        if receipt_id is None:
            verify_zero_transfer_revision(order, transfers[0])
        else:
            # The same receipt witnesses the original transfer version.
            from types import SimpleNamespace
            from .models import OrderPointsFundingAllocation, OrderPointsTransferFundingRevision
            from .points_transfer_receipt_usage import facts as transfer_facts
            revision = OrderPointsTransferFundingRevision.objects.filter(order=order,
                last_transfer=transfers[0]).first()
            allocation = OrderPointsFundingAllocation.objects.filter(redemption__order=order,
                receipt_id=receipt_id, created_at__lt=transfers[0].created_at).order_by('pk').first()
            if not allocation and revision and revision.allocations == []:
                allocation = OrderPointsFundingAllocation.objects.filter(redemption__order=order,
                    receipt_id=receipt_id, created_at__gt=transfers[0].created_at).order_by('pk').first()
            if not revision or not allocation:
                raise ValidationError('接替前退款缺少原转单到账版本')
            transfer_facts(SimpleNamespace(revision=revision, allocation=allocation, pk=0, amount=0,
                                           read_receipt_id=receipt_id))
    events = []
    for refund in all_prior:
        run = OrderAddonFundingReleaseRun.objects.filter(refund=refund).first()
        if not run or not isinstance(run.points_addon_snapshot, dict):
            raise ValidationError('接替前退款缺少真实资金释放凭证')
        try:
            source = refund_facts(refund, read_receipt_id=receipt_id)
            verify_run(run, _skip_usage=True, witness_receipt_id=receipt_id)
        except RestValidationError as exc:
            raise ValidationError('接替前退款或释放凭证异常') from exc
        boundary = handover.created_at if refund.created_at < handover.created_at else handover.accepted_at
        if source != run.points_addon_snapshot or run.created_at >= boundary:
            raise ValidationError('接替前退款与资金事件顺序不一致')
        events.append([refund.pk, refund.created_at.isoformat(), run.pk, run.points_addon_hash,
                       run.released_amount])
    basis = payment.intent.amount - sum(item.amount for item in prior)
    income = payment.provider_income - sum(payment.refund_earning_deductions.filter(
        refund__created_at__lt=handover.created_at).values_list('amount', flat=True))
    if basis <= 0 or not 0 <= income <= basis or handover.source_basis_before != basis:
        raise ValidationError('接替前退款后的剩余责任或收益不一致')
    result = {'payment_id': payment.pk, 'transfer_id': transfers[0].pk,
              'prior_refunds': events, 'remaining_basis': basis, 'remaining_income': income}
    if any(row.created_at > handover.created_at for row in all_prior):
        from .points_transfer_pending_handover_events import verify_accepted_history
        result['unaffected_pending_refunds'] = verify_accepted_history(handover, receipt_id=receipt_id)
    return result
