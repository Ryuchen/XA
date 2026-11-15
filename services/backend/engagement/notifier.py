"""Refresh claim queues after committed business changes."""
import logging
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from stores.db import transaction


def notify_claims_after_commit():
    def send():
        try:
            layer = get_channel_layer()
            if layer is not None:
                async_to_sync(layer.group_send)('operators', {'type': 'claim_queue_update', 'data': {}})
        except Exception:
            logging.getLogger(__name__).warning('权益队列通知失败', exc_info=True)
    transaction.on_commit(send)


def notify_member_points_after_commit(account_id):
    """Invalidate the owner's points and claims after a committed change."""
    def send():
        try:
            layer = get_channel_layer()
            if layer is not None:
                async_to_sync(layer.group_send)(f'account_{account_id}', {
                    'type': 'member_points_update', 'data': {'account_id': account_id},
                })
        except Exception:
            logging.getLogger(__name__).warning('会员积分通知失败: account_id=%s', account_id, exc_info=True)
    transaction.on_commit(send)


def notify_reward_catalog_after_commit():
    """Refresh both member storefronts after catalog or stock changes."""
    def send():
        try:
            layer = get_channel_layer()
            if layer is not None:
                for group in ('club_bosses', 'providers'):
                    async_to_sync(layer.group_send)(group, {'type': 'reward_catalog_update'})
        except Exception:
            logging.getLogger(__name__).warning('积分商城通知失败', exc_info=True)
    transaction.on_commit(send)


def notify_provider_tags_after_commit(account):
    """Tell boss clients which public companion card should be reloaded."""
    if account.account_type != 'PROVIDER':
        return
    account_id = account.pk
    def send():
        try:
            layer = get_channel_layer()
            if layer is not None:
                async_to_sync(layer.group_send)('club_bosses', {
                    'type': 'provider_tags_update', 'data': {'provider_id': account_id},
                })
                async_to_sync(layer.group_send)(f'account_{account_id}', {
                    'type': 'provider_tags_update', 'data': {'provider_id': account_id},
                })
        except Exception:
            logging.getLogger(__name__).warning('陪玩标签通知失败: account_id=%s', account_id, exc_info=True)
    transaction.on_commit(send)
def notify_vip_config_changed():
    """Ask open member pages to reread the public tier configuration."""
    import logging
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        layer = get_channel_layer()
        if layer is not None:
            async_to_sync(layer.group_send)('club_bosses', {'type': 'vip_config_update'})
    except Exception:
        logging.getLogger(__name__).warning('会员配置实时通知失败', exc_info=True)

