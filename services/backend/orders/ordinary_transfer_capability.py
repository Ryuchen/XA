"""Controlled MySQL capability for ordinary transferred-order refunds."""
from django.conf import settings
from stores.db import connection


def enabled():
    from .points_checkout_test import ordinary_production_enabled
    return bool(connection.vendor == 'mysql'
                and getattr(settings, 'ORDINARY_TRANSFER_REFUND_MYSQL_ENABLED', False)
                and getattr(settings, 'NEGOTIATION_PROPOSAL_ENABLED', False)
                and getattr(settings, 'NEGOTIATION_REFUND_EXECUTION_ENABLED', False)
                and getattr(settings, 'SEGMENT_REPORT_ENABLED', False)
                and getattr(settings, 'SEGMENT_SETTLEMENT_ENABLED', False)
                and ordinary_production_enabled())


def permits(order, *, allow_addon_refund=False, allow_addon_handover=False):
    """Narrow existing funding checks to one real transferred ordinary order."""
    from stores.context import current_store
    from stores.db import transaction
    if not enabled() or not transaction.get_connection().in_atomic_block or current_store() is None or not order:
        return False
    from .models import OrderOriginalShareTransfer, OrderPlatformSubsidy
    from .points_service_payment import needs_integrated_processing
    from .models import OrderTransferNegotiationEvidence, OrdinaryTransferFollowupRound, NegotiationExecution
    from .models import OrderAddonPayment
    refund_count = order.refund_records.count()
    from django.db.models import Q
    first = OrderTransferNegotiationEvidence.objects.filter(
        Q(execution__refund__order=order) | Q(execution__zero_order=order),
        order=order, execution__proposal__status='ACCEPTED').first()
    tracked_refunds = {first.execution.refund_id} if first and first.execution.refund_id else set()
    followups = OrdinaryTransferFollowupRound.objects.filter(order=order, execution__refund__isnull=False)
    tracked_refunds.update(followups.values_list('execution__refund_id', flat=True))
    all_refunds = set(order.refund_records.values_list('pk', flat=True))
    from .negotiation_zero import marked_execution_id
    from .models import OrdinaryPostRefundTransferRevision
    zero_id = marked_execution_id(order)
    pretransfer_zero = bool(zero_id and OrdinaryPostRefundTransferRevision.objects.filter(order=order,
        snapshot__zero_pretransfer_execution_id=zero_id).exists())
    first_transfer = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).order_by('created_at', 'pk').first()
    pretransfer_execution = (NegotiationExecution.objects.filter(refund__order=order,
        termination_processed_at__lt=first_transfer.created_at).select_related('refund').order_by('pk').first()
        if first_transfer and not first else None)
    if pretransfer_execution:
        tracked_refunds.add(pretransfer_execution.refund_id)
    refunds_tracked = not refund_count or (bool(first or pretransfer_zero or pretransfer_execution)
                                          and tracked_refunds == all_refunds)
    account_ids = list(order.providers.values_list('provider_account_id', flat=True))
    intents = order.addon_intents.all()
    # Payment, prior refunds and accepted handovers are checked again in the
    # proposal and settlement transactions; unresolved work remains pending.
    addons_valid = (not intents.exists() or (
        (allow_addon_handover or not OrderAddonPayment.objects.filter(intent__order=order, handovers__status='PENDING').exists())
        and (allow_addon_refund or not OrderAddonPayment.objects.filter(intent__order=order,
            refund_proposals__status__in=['PENDING', 'ACCEPTED'], refund_proposals__refund__isnull=True).exists())))
    return bool(not needs_integrated_processing(order) and not order.is_gift_snapshot and not order.source_order_id
                and order.customer_account_id and order.funding_terms is not None and order.funding_allocations.exists()
                and account_ids and all(account_ids) and len(account_ids) == len(set(account_ids))
                and refunds_tracked and addons_valid
                and not OrderPlatformSubsidy.objects.filter(order=order).exists()
                and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists())
