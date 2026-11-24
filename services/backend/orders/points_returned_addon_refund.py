"""Refund the returned segment of a paid add-on after two same-seat transfers."""
from django.conf import settings
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from .models import (OrderAddonFundingReleaseRun, OrderAddonHandover, OrderAddonPayment, OrderAddonRefund,
                     OrderOriginalShareTransfer, OrderPointsFundingAllocation, OrderSegmentSettlementManifest)


def returned_handover(order, *, payment_id=None, transfers=None, at_refund=None):
    from .points_business_access import configured
    from .points_service_payment import needs_integrated_processing
    from .points_transfer_refund_access import same_seat_addon_payments, unaffected_addon_for_return

    if (not configured() or not getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False)
            or not needs_integrated_processing(order) or order.is_gift_snapshot or order.source_order_id
            or order.room_required_snapshot):
        return None
    main_refunds = order.refund_records.all()
    if (main_refunds if at_refund is None else main_refunds.filter(created_at__lte=at_refund)).exists():
        return None
    if transfers is None:
        transfers = list(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order)
                         .order_by('pk')[:3])
    if len(transfers) != 2:
        return None
    payments = list(OrderAddonPayment.objects.filter(intent__order=order).select_related('buyer_transaction')[:3])
    if len(payments) == 2 and not same_seat_addon_payments(order, payments, transfers[0]):
        payments = [payment for payment in payments if unaffected_addon_for_return(order, payment, payments, transfers[0])]
    if not 1 <= len(payments) <= 2:
        return None
    handovers = {payment.pk: _last_returned_handover(order, payment, transfers, at_refund=at_refund)
                 for payment in payments}
    if not all(handovers.values()):
        return None
    refunds = list(OrderAddonRefund.objects.filter(payment_id__in=handovers)[:2])
    manifests = OrderSegmentSettlementManifest.objects.filter(order=order)
    if refunds:
        refund = refunds[0]
        if (len(refunds) != 1 or refund.order_id != order.pk
                or (at_refund is not None and refund.created_at != at_refund)
                or any(refund.created_at <= handover.accepted_at for handover in handovers.values())
                or manifests.filter(created_at__lte=refund.created_at).exists()
                or any(payment.pk != refund.payment_id and payment.refund_earning_deductions.exists()
                       for payment in payments)):
            return None
        return handovers[refund.payment_id] if payment_id in (None, refund.payment_id) else None
    if at_refund is not None or order.settled_at or manifests.exists():
        return None
    if payment_id is None and len(handovers) == 1:
        return next(iter(handovers.values()))
    return handovers.get(payment_id)


def _last_returned_handover(order, payment, transfers, *, at_refund):
    from .points_transfer_refund_access import _successive_addon_handovers

    if payment.handovers.exclude(status='ACCEPTED').exists():
        return None
    chain = _successive_addon_handovers(order, payment, transfers, check_refund_times=False, at_refund=at_refund)
    if len(chain) != 2:
        return None
    first, last = chain
    source, middle, returned = first.source_segment, first.target_segment, last.target_segment
    if (returned.slot != source.slot or returned.slot != middle.slot or returned.pk == source.pk
            or not returned.started_at or not transfers[1].created_at <= returned.started_at <= last.accepted_at
            or not source.provider_account_id or not returned.provider_business_cycle
            or source.provider_account_id != returned.provider_account_id
            or source.provider_account_id == middle.provider_account_id
            or source.provider_business_cycle != returned.provider_business_cycle):
        return None
    return last


def other_returned_handover(order, handover):
    """Find the other payment after both chains have passed returned_handover."""
    return (OrderAddonHandover.objects.filter(
        payment__intent__order=order, target_segment_id=handover.target_segment_id,
        status='ACCEPTED', kind='STANDARD').exclude(payment_id=handover.payment_id)
        .select_related('payment', 'previous').first())


def before_first_main_refund(order, *, transfers=None):
    """Identify the posted add-on refund preceding a separate main refund."""
    if OrderAddonPayment.objects.filter(intent__order=order).count() != 2 or order.refund_records.count() > 1:
        return None
    refunds = list(order.addon_refunds.all()[:2])
    if len(refunds) != 1:
        return None
    refund = refunds[0]
    returned = returned_handover(order, payment_id=refund.payment_id, transfers=transfers, at_refund=refund.created_at)
    return returned if returned and other_returned_handover(order, returned) else None


def require_other_seat(order, returned, segments):
    from .models import OrderParticipationSegment

    if (not segments or len(segments) != 1 or not OrderParticipationSegment.objects.filter(
            pk=segments[0], attribution__order=order, started_at__isnull=False, released_at__isnull=True)
            .exclude(slot=returned.target_segment.slot).exists()):
        raise ValidationError('请为另一席选择仍在履约的主服务段')


def verify_returned_handovers(order, handover, *, before=None, receipt_id=None):
    from .addon_handover import verify_transferred_handover_funding
    from .points_addon_receipt_usage import reuse_history_locked

    other = other_returned_handover(order, handover)
    receipts = ({receipt_id} if receipt_id else set(OrderPointsFundingAllocation.objects.filter(
        redemption__order=order).values_list('receipt_id', flat=True)))
    with reuse_history_locked(order, receipts):
        for last in (handover, other) if other else (handover,):
            for accepted in (last.previous, last):
                verify_transferred_handover_funding(order, last.payment, accepted, before=before, receipt_id=receipt_id)


def require_opening(case, handover, *, request_id, opened_at):
    from .addon_refund import case_opening_records
    from .aftersale_scope import normalize_scope

    openings = case_opening_records(case, handover.payment_id, request_id, opened_at)
    if (len(openings) != 1 or normalize_scope(openings[0].get('affected_segment_ids')) != [handover.target_segment_id]
            or normalize_scope(openings[0].get('affected_provider_account_ids')) != [handover.target_segment.provider_account_id]):
        raise ValidationError('转回段加购退款缺少原售后范围')


def verify_history(refund, allocation_ids, *, receipt_id):
    """Check posting-time allocations and acceptance proofs under existing locks."""
    order = refund.order
    returned = returned_handover(order, payment_id=refund.payment_id, at_refund=refund.created_at)
    if not returned:
        return
    allocations = list(OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order_id=refund.order_id) | Q(service_order_id=refund.order_id),
        created_at__lte=refund.created_at).order_by('pk').values_list('pk', 'created_at'))
    if (any(created_at == refund.created_at for _, created_at in allocations)
            or [pk for pk, _ in allocations] != allocation_ids):
        raise ValidationError('转回段加购退款的补资分配时序或范围异常')
    verify_returned_handovers(order, returned, before=refund.created_at, receipt_id=receipt_id)


def verify_funding(order, handover, *, receipt_id=None):
    from .addon_funding_release import verify_run

    payment = handover.payment
    refund = payment.refund_records.first()
    if refund:
        run = OrderAddonFundingReleaseRun.objects.filter(refund=refund, points_addon_snapshot__isnull=False).first()
        if not run or not run.points_addon_hash:
            raise ValidationError('转回段加购退款的补资凭证待核对')
        if order.refund_records.filter(created_at__lte=run.created_at).exists():
            raise ValidationError('末段加购与主单退款资金时序待核对')
        if receipt_id is None:
            verify_run(run)
        else:
            verify_run(run, _skip_usage=True, witness_receipt_id=receipt_id)
    else:
        verify_returned_handovers(order, handover)
