"""Notify current room participants to reread authorized messages after commit."""
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from stores.db import transaction


def schedule_room_message_update(order):
    recipients = {order.customer_account_id, *order.providers.filter(released_at__isnull=True)
                  .values_list('provider_account_id', flat=True)}
    if order.required_provider_count == 1 and not order.providers.exists():
        recipients.add(order.provider_account_id)
    recipients.discard(None)
    transaction.on_commit(lambda: notify_room_message(order.pk, recipients))


def notify_room_message(order_id, account_ids):
    try:
        layer = get_channel_layer()
        if layer is None:
            return
        event = {'type': 'room_message_update', 'data': {'order_id': order_id}}
        for group in [*(f'account_{pk}' for pk in sorted(set(account_ids))), 'operators']:
            try:
                async_to_sync(layer.group_send)(group, event)
            except Exception:
                logging.getLogger(__name__).warning('房间消息实时通知失败: order_id=%s group=%s', order_id, group, exc_info=True)
    except Exception:
        logging.getLogger(__name__).warning('房间消息实时通知失败: order_id=%s', order_id, exc_info=True)
