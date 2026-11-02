"""Refresh claim queues after committed business changes."""
import logging
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction


def notify_claims_after_commit():
    def send():
        try:
            layer = get_channel_layer()
            if layer is not None:
                async_to_sync(layer.group_send)('operators', {'type': 'claim_queue_update', 'data': {}})
        except Exception:
            logging.getLogger(__name__).warning('权益队列通知失败', exc_info=True)
    transaction.on_commit(send)
