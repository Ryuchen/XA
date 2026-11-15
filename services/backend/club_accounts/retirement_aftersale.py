"""Order-snapshot aftersale windows for a provider membership closure."""
from django.db.models import Q
from django.core.exceptions import ObjectDoesNotExist, ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from orders.models import Order, OrderParticipationSegment
from orders.settlement import SettlementError


def lock_candidate_orders(account):
    """Call within a store transaction, before locking the account row."""
    segments = OrderParticipationSegment.objects.filter(provider_account_id=account.pk).values('attribution__order_id')
    ids = list(Order.objects.filter(status=Order.Status.COMPLETED).filter(
        Q(provider_account_id=account.pk) | Q(providers__provider_account_id=account.pk)
        | Q(pk__in=segments)).order_by().values_list('pk', flat=True).distinct())
    orders = list(Order.objects.select_for_update().filter(pk__in=ids).order_by('pk'))
    if orders:
        from orders.financial_lock import lock_order_money
        lock_order_money()
    return orders


def window_facts(account, orders):
    """Return currently open windows and sources needing reconciliation."""
    from engagement.confirmation import aftersale_deadline
    from orders.aftersale_segment_scope import closed_segments_locked
    from orders.negotiation_aftersale_scope import closed_provider_accounts
    from orders.models import OrderOriginalShareTransfer, OrderTransferNegotiationEvidence
    from orders.points_service_payment import needs_integrated_processing

    now = timezone.now()
    open_ids, review_ids = [], []
    for order in orders:
        if order.status != Order.Status.COMPLETED:
            continue
        seats = list(order.providers.filter(provider_account_id=account.pk).values_list(
            'provider_business_cycle', flat=True))
        segments = list(OrderParticipationSegment.objects.filter(attribution__order=order,
            provider_account_id=account.pk, started_at__isnull=False).values_list('pk', 'provider_business_cycle'))
        cycles = seats + [cycle for _, cycle in segments]
        if order.is_gift_snapshot and order.provider_account_id == account.pk:
            cycles.append(order.gift_provider_business_cycle)
        if not cycles:
            continue
        if any(cycle is None for cycle in cycles):
            review_ids.append(order.pk)
            continue
        if account.business_cycle not in cycles:
            continue
        clock = order.gift_aftersale_hours if order.is_gift_snapshot else order.service_aftersale_hours
        if clock is None or not order.completed_at:
            review_ids.append(order.pk)
            continue
        deadline = aftersale_deadline(order)
        if not deadline or now >= deadline:
            continue
        try:
            ordinary = OrderTransferNegotiationEvidence.objects.filter(order=order,
                execution__termination_processed_at__isnull=False).exists()
            points_transfer = (needs_integrated_processing(order) and order.refund_records.exists()
                and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists())
            closed_accounts = set()
            closed_segments = None
            main_closed = False
            if ordinary or points_transfer:
                if points_transfer:
                    from orders.points_transfer_refund_access import after_transfer
                    with after_transfer(order.pk, phase='refund') as scoped_order:
                        closed_segments = closed_segments_locked(scoped_order)
                else:
                    closed_segments = closed_segments_locked(order)
                own = {pk for pk, cycle in segments if cycle == account.business_cycle}
                if own and closed_segments is not None and own.issubset(closed_segments):
                    main_closed = True
            else:
                closed_accounts = closed_provider_accounts(order)
                if account.pk in closed_accounts:
                    main_closed = True
            # A plain full main refund has no surviving main aftersale. Scoped
            # agreements can retain another participant's open service.
            if (order.payment_status == Order.PaymentStatus.REFUNDED
                    and not ordinary and not points_transfer and not closed_accounts):
                main_closed = True
            if main_closed:
                from .retirement_addon_clearance import all_relevant_refunded
                if all_relevant_refunded(order, account):
                    continue
        except (ValidationError, DjangoValidationError, SettlementError, ObjectDoesNotExist,
                ValueError, TypeError, KeyError, AttributeError):
            review_ids.append(order.pk)
            continue
        open_ids.append(order.pk)
    return {'open_order_ids': open_ids, 'review_order_ids': review_ids}
