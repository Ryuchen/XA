from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from .serializers import OrderSerializer


def notify_order_update(order):
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    data = OrderSerializer(order).data

    if order.customer_account_id:
        async_to_sync(channel_layer.group_send)(
            f'account_{order.customer_account_id}',
            {'type': 'order_status_update', 'data': data},
        )

    notified_provider_ids = set()
    if order.provider_account_id:
        notified_provider_ids.add(order.provider_account_id)
        async_to_sync(channel_layer.group_send)(
            f'account_{order.provider_account_id}',
            {'type': 'order_status_update', 'data': data},
        )

    # 双陪第二打手也必须收到状态变化，否则其 H5 只能手动刷新。
    for provider_id in order.providers.exclude(
        provider_account_id=None,
    ).values_list('provider_account_id', flat=True):
        if provider_id in notified_provider_ids:
            continue
        notified_provider_ids.add(provider_id)
        async_to_sync(channel_layer.group_send)(
            f'account_{provider_id}',
            {'type': 'order_status_update', 'data': data},
        )

    async_to_sync(channel_layer.group_send)(
        'operators',
        {'type': 'order_status_update', 'data': data},
    )

    # 抢单池相关状态广播给全部陪玩：PENDING 加入/退回，GRABBED/CANCELLED 移除。
    if order.status in (order.Status.PENDING, order.Status.GRABBED, order.Status.CANCELLED):
        async_to_sync(channel_layer.group_send)(
            'providers',
            {'type': 'order_status_update', 'data': data},
        )
