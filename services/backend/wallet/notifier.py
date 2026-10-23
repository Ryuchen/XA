"""Small, permission-scoped wallet invalidations; never publish money values."""
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def owner_account_id(record):
    if record.account_id:
        return record.account_id
    from club_accounts.models import LegacyAccountMap
    return LegacyAccountMap.objects.filter(
        legacy_user_id=record.user_id,
    ).values_list('account_id', flat=True).first()


def _publish(record, event_type, reviewer_group, payload):
    try:
        layer = get_channel_layer()
        if layer is None:
            return
        account_id = owner_account_id(record)
        event = {'type': event_type, 'data': {**payload, 'account_id': account_id}}
        groups = [reviewer_group]
        if account_id:
            groups.insert(0, f'account_{account_id}')
        for group in groups:
            try:
                async_to_sync(layer.group_send)(group, event)
            except Exception:
                logging.getLogger(__name__).warning(
                    '资金实时通知失败: type=%s id=%s group=%s', event_type, record.pk, group, exc_info=True,
                )
    except Exception:
        logging.getLogger(__name__).warning(
            '资金实时通知失败: type=%s id=%s', event_type, record.pk, exc_info=True,
        )


def notify_wallet_update(wallet):
    _publish(wallet, 'wallet_update', 'wallet_reviewers', {'wallet_id': wallet.pk})


def notify_withdraw_update(withdraw):
    _publish(withdraw, 'withdraw_status_update', 'withdraw_reviewers', {
        'withdraw_id': withdraw.pk, 'status': withdraw.status,
    })
