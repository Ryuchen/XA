"""Prompt connected customers to reload coupon availability when it changes."""
import logging
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def notify_coupon_catalog_changed():
    try:
        layer = get_channel_layer()
        if layer is not None:
            async_to_sync(layer.group_send)('club_bosses', {'type': 'coupon_catalog_update'})
    except Exception:
        logging.getLogger(__name__).warning('优惠券实时通知失败', exc_info=True)


def notify_coupon_claim_changed(coupon_id):
    try:
        layer = get_channel_layer()
        if layer is not None:
            async_to_sync(layer.group_send)('operators', {
                'type': 'coupon_claim_update', 'data': {'coupon_id': coupon_id},
            })
    except Exception:
        logging.getLogger(__name__).warning('优惠券领取记录实时通知失败', exc_info=True)


def notify_owner_coupon_changed(account_id):
    try:
        layer = get_channel_layer()
        if layer is not None:
            async_to_sync(layer.group_send)(f'account_{account_id}', {'type': 'coupon_catalog_update'})
    except Exception:
        logging.getLogger(__name__).warning('会员优惠券实时通知失败', exc_info=True)
