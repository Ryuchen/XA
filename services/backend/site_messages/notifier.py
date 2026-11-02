"""Invalidate a recipient's message list after a committed change."""
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction


def notify_messages_after_commit(account_id, *, assignment_message_id=None):
    if not account_id:
        return

    def send():
        try:
            layer = get_channel_layer()
            if layer is not None:
                payload = {'account_id': account_id}
                if assignment_message_id:
                    payload['assignment_message_id'] = assignment_message_id
                async_to_sync(layer.group_send)(
                    f'account_{account_id}',
                    {'type': 'site_message_update', 'data': payload},
                )
        except Exception:
            logging.getLogger(__name__).warning('站内消息实时通知失败: account_id=%s', account_id, exc_info=True)

    transaction.on_commit(send)
