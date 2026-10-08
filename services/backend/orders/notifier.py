from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
import logging

from .serializers import OrderSerializer


def notify_order_update(order):
    # 订单和账务已经提交，消息通道故障不能把成功操作伪装成失败。
    try:
        _notify_order_update(order)
    except Exception:
        logging.getLogger(__name__).warning('订单实时通知失败: order_id=%s', order.pk, exc_info=True)


def _notify_order_update(order):
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

    for provider_id in set(order.applications.values_list('applicant_id', flat=True)) | set(order.applications.exclude(partner_id=None).values_list('partner_id', flat=True)):
        if provider_id not in notified_provider_ids:
            async_to_sync(channel_layer.group_send)(f'account_{provider_id}', {'type': 'order_status_update', 'data': {'id': order.pk, 'status': order.status}})

    async_to_sync(channel_layer.group_send)(
        'operators',
        {'type': 'order_status_update', 'data': data},
    )

    # 抢单池相关状态广播给全部陪玩：PENDING 加入/退回，GRABBED/CANCELLED 移除。
    if order.status in (order.Status.PENDING, order.Status.GRABBED, order.Status.CANCELLED):
        async_to_sync(channel_layer.group_send)(
            'providers',
            # 公共分组只通知刷新，详细订单必须经带通行证/档位过滤的 HTTP 接口读取。
            {'type': 'order_status_update', 'data': {'id': order.pk, 'status': order.status}},
        )
