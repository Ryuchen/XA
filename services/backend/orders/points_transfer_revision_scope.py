"""Restrict existing-allocation transfer changes to a single guarded request."""
from contextlib import contextmanager
from contextvars import ContextVar

from django.conf import settings
from rest_framework.exceptions import ValidationError

from .points_checkout_test import require_isolated_checkout


_order_id = ContextVar('points_transfer_revision_order', default=None)


@contextmanager
def revision_scope(order_id, *, outgoing_accounts=()):
    if getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True:
        from .models import Order
        from .points_service_payment import needs_integrated_processing
        order = Order.objects.filter(pk=order_id).first()
        if order and needs_integrated_processing(order):
            if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                from console.payroll_event_barrier import lock_payment_refund_events
                lock_payment_refund_events()
            from .points_transfer_refund_access import transfer_capture, post_refund_transfer, third_refund_transfer
            scope = (third_refund_transfer(order_id) if order.refund_records.count() >= 3 else
                     post_refund_transfer(order_id) if order.refund_records.exists() else
                     transfer_capture(order_id, outgoing_accounts=outgoing_accounts))
            with scope:
                token = _order_id.set(order_id)
                try:
                    yield
                finally:
                    _order_id.reset(token)
            return
    if not getattr(settings, 'SERVICE_POINTS_TRANSFER_FUNDING_REVISION_TESTING', False):
        yield
        return
    try:
        require_isolated_checkout()
    except ValidationError:
        yield
        return
    token = _order_id.set(order_id)
    try:
        yield
    finally:
        _order_id.reset(token)


def active(order):
    return bool(order and order.pk and _order_id.get() == order.pk)
