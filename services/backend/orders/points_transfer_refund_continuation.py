"""Verify a closed transferred-points aftersale before continued fulfilment."""
from django.conf import settings
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import Order, OrderOriginalShareTransfer, PointsNegotiationPosting
from .points_checkout_test import require_isolated_checkout


def verify_locked(order):
    from .points_transfer_refund_access import permits, verification_scope
    if any(permits(order, phase=phase) for phase in ('report', 'settlement')):
        with verification_scope(order):
            return _verify_locked(order)
    return _verify_locked(order)


def _verify_locked(order):
    from .points_transfer_refund_access import permits, require_refund_or_isolated
    require_refund_or_isolated()
    if (not transaction.get_connection().in_atomic_block
            or (not permits(order, phase='refund') and not getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_TERMINATION_TESTING', False))
            or order.status not in (Order.Status.IN_SERVICE, Order.Status.COMPLETED)
            or order.payment_status not in (Order.PaymentStatus.PARTIALLY_REFUNDED, Order.PaymentStatus.REFUNDED)
            or not OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        raise ValidationError('转单退款后的履约尚未开放')
    from .financial_lock import lock_order_money
    from .points_negotiation_termination import verify_termination_locked
    lock_order_money()
    rows = list(PointsNegotiationPosting.objects.select_for_update()
                .filter(points_refund__redemption__order=order).order_by('pk')[:2])
    if len(rows) != 1 or not isinstance(rows[0].participants, (dict, list)):
        raise ValidationError('转单退款后的履约凭证不完整')
    return verify_termination_locked(rows[0])
