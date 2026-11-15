"""Narrow production slice for an ordinary two-person zero termination."""
from django.conf import settings
from stores.db import connection


def eligible(order, *, allow_pending=False):
    from .ordinary_followup_capability import production_enabled
    from .points_service_payment import needs_integrated_processing
    from .direct_payment_models import DirectPaymentIntent
    from .models import (Order, OrderAddonPayment, OrderAddonHandover, OrderOriginalShareTransfer,
                         OrderPlatformSubsidy, OrderPrestartHandoff, OrderStatusLog)
    if not order:
        return False
    payments = list(OrderAddonPayment.objects.filter(intent__order=order).order_by('pk'))
    if not allow_pending and OrderAddonHandover.objects.filter(payment__in=payments, status='PENDING').exists():
        return False
    return bool(connection.vendor == 'mysql' and production_enabled()
        and getattr(settings, 'ORDINARY_ZERO_FOLLOWUP_MYSQL_ENABLED', False)
        and order and not order.is_gift_snapshot and not order.source_order_id
        and not Order.objects.filter(source_order_id=order.pk, is_gift_snapshot=False).exists()
        and order.required_provider_count == 2 and order.customer_account_id and order.funding_terms is not None
        and order.funding_allocations.exists() and not needs_integrated_processing(order)
        and not DirectPaymentIntent.objects.filter(order=order).exists()
        and not order.status_logs.filter(action=OrderStatusLog.Action.TRANSFER).exists()
        and not OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
        and not OrderPrestartHandoff.objects.filter(order=order).exists()
        and not OrderPlatformSubsidy.objects.filter(order=order).exists())
