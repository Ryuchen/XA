"""Notify connected reservation participants and order reviewers to reload."""
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from stores.db import transaction


def notify_reservation_after_commit(reservation):
    reservation_id = reservation.pk
    account_ids = {reservation.customer_account_id, reservation.provider_account_id} - {None}
    if reservation.required_provider_count == 2:
        account_ids.discard(reservation.provider_account_id)
        account_ids.update(reservation.seats.filter(provider_account__isnull=False).values_list('provider_account_id', flat=True))
    if reservation.status == reservation.Status.CONVERTED and reservation.order_id:
        account_ids.update(reservation.order.providers.filter(released_at__isnull=True,
            provider_account__isnull=False).values_list('provider_account_id', flat=True))

    def send():
        try:
            layer = get_channel_layer()
        except Exception:
            logging.getLogger(__name__).warning('预约实时通道不可用: reservation_id=%s', reservation_id, exc_info=True)
            return
        if layer is None:
            return
        event = {'type': 'reservation_status_update', 'data': {'reservation_id': reservation_id}}
        for group in [*(f'account_{account_id}' for account_id in account_ids), 'operators']:
            try:
                async_to_sync(layer.group_send)(group, event)
            except Exception:
                logging.getLogger(__name__).warning('预约实时通知失败: reservation_id=%s group=%s',
                                                    reservation_id, group, exc_info=True)

    transaction.on_commit(send)
