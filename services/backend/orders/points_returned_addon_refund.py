"""Refund the returned segment of a paid add-on after two same-seat transfers."""
from django.conf import settings
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from .models import (OrderAddonPayment, OrderAddonFundingReleaseRun, OrderOriginalShareTransfer,
                     OrderPointsFundingAllocation, OrderSegmentSettlementManifest)


def returned_handover(order, *, payment_id=None, transfers=None):
    from .points_business_access import configured
    from .points_service_payment import needs_integrated_processing
    from .points_transfer_refund_access import _successive_addon_handovers

    if (not configured() or not getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False)
            or not needs_integrated_processing(order) or order.is_gift_snapshot or order.source_order_id
            or order.room_required_snapshot or order.refund_records.exists()):
        return None
    if transfers is None:
        transfers = list(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order)
                         .order_by('pk')[:3])
    if len(transfers) != 2:
        return None
    payments = list(OrderAddonPayment.objects.filter(intent__order=order).select_related('buyer_transaction')[:2])
    if len(payments) != 1 or (payment_id is not None and payment_id != payments[0].pk):
        return None
    payment = payments[0]
    if payment.handovers.exclude(status='ACCEPTED').exists():
        return None
    chain = _successive_addon_handovers(order, payment, transfers, check_refund_times=False)
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
    refunds = list(payment.refund_records.all()[:2])
    manifests = OrderSegmentSettlementManifest.objects.filter(order=order)
    if not refunds:
        return last if not order.settled_at and not manifests.exists() else None
    if (len(refunds) != 1 or refunds[0].order_id != order.pk or refunds[0].created_at <= last.accepted_at
            or manifests.filter(created_at__lte=refunds[0].created_at).exists()):
        return None
    return last


def require_opening(case, handover, *, request_id, opened_at):
    from .addon_refund import case_opening_records
    from .aftersale_scope import normalize_scope

    openings = case_opening_records(case, handover.payment_id, request_id, opened_at)
    if (len(openings) != 1 or normalize_scope(openings[0].get('affected_segment_ids')) != [handover.target_segment_id]
            or normalize_scope(openings[0].get('affected_provider_account_ids')) != [handover.target_segment.provider_account_id]):
        raise ValidationError('转回段加购退款缺少原售后范围')


def verify_allocation_history(refund, allocation_ids):
    """Check the complete split as of posting with the caller's existing read locks."""
    if not returned_handover(refund.order, payment_id=refund.payment_id):
        return
    allocations = list(OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order_id=refund.order_id) | Q(service_order_id=refund.order_id),
        created_at__lte=refund.created_at).order_by('pk').values_list('pk', 'created_at'))
    if (any(created_at == refund.created_at for _, created_at in allocations)
            or [pk for pk, _ in allocations] != allocation_ids):
        raise ValidationError('转回段加购退款的补资分配时序或范围异常')


def verify_funding(order, handover):
    from .addon_funding_release import verify_run
    from .addon_handover import verify_transferred_handover_funding

    payment = handover.payment
    for accepted in (handover.previous, handover):
        verify_transferred_handover_funding(order, payment, accepted)
    refund = payment.refund_records.first()
    if refund:
        run = OrderAddonFundingReleaseRun.objects.filter(refund=refund, points_addon_snapshot__isnull=False).first()
        if not run or not run.points_addon_hash:
            raise ValidationError('转回段加购退款的补资凭证待核对')
        verify_run(run)
