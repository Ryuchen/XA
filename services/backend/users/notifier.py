"""Notify open boss pages after check-in configuration commits."""
import logging
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from stores.db import transaction


def notify_checkin_config_after_commit():
    def send():
        try:
            layer = get_channel_layer()
            if layer is not None:
                async_to_sync(layer.group_send)('club_bosses', {'type': 'checkin_config_update'})
        except Exception:
            logging.getLogger(__name__).warning('签到配置实时通知失败', exc_info=True)
    transaction.on_commit(send)


def notify_checkin_progress_after_commit(year, month):
    def send():
        try:
            layer = get_channel_layer()
            if layer is not None:
                async_to_sync(layer.group_send)('checkin_reviewers', {
                    'type':'checkin_progress_update', 'data':{'year':year, 'month':month},
                })
        except Exception:
            logging.getLogger(__name__).warning('签到进度实时通知失败', exc_info=True)
    transaction.on_commit(send)
