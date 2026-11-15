"""Order and receipt locks for a later points-refund operation."""
from contextlib import contextmanager
from django.conf import settings
from django.db.models import Q
from rest_framework.exceptions import ValidationError
from stores.context import current_store
from stores.db import transaction


@contextmanager
def locked(order_id, *, phase='refund'):
    from .models import Order, OrderPointsFundingAllocation
    from .financial_lock import lock_order_money
    from .points_business_access import _bind
    from .points_round_funding import current_locked
    from users.models import PassRefundRepaymentReceipt
    connection, store = transaction.get_connection(), current_store()
    if (not connection.in_atomic_block or connection.vendor != 'mysql' or not store
            or store.database_alias != connection.alias
            or getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is not True
            or phase not in ('refund', 'settlement')):
        raise ValidationError('多轮退款须在本店资金事务中办理')
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if not order or order.refund_records.count() < 3:
        raise ValidationError('多轮退款原订单或历史轮次缺失')
    lock_order_money()
    receipt_ids = set(OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order=order) | Q(service_order=order)).values_list('receipt_id', flat=True))
    receipts = list(PassRefundRepaymentReceipt.objects.select_for_update().filter(pk__in=receipt_ids).order_by('pk'))
    if len(receipts) != len(receipt_ids):
        raise ValidationError('多轮退款到账凭证缺失')
    with _bind(phase='refund', kind='transfer', order_id=order.pk,
               account_id=order.customer_account_id, service_id=order.service_id):
        current_locked(order)
        if phase == 'refund':
            yield order
        else:
            with _bind(phase='settlement', kind='transfer', order_id=order.pk,
                       account_id=order.customer_account_id, service_id=order.service_id):
                yield order
