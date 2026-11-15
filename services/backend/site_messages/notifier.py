"""Invalidate a recipient's message list after a committed change."""
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from stores.db import transaction


def notify_messages_after_commit(account_id, *, assignment_message_id=None, business_cycle=None):
    if not account_id:
        return

    def send():
        try:
            from django.conf import settings
            from club_accounts.models import ClubAccount
            account_type = ClubAccount.objects.filter(pk=account_id).values_list('account_type', flat=True).first()
            if not account_type:
                logging.getLogger(__name__).warning('站内消息实时通知账号待核对: account_id=%s', account_id)
                return
            guarded = (getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
                       or account_type in (ClubAccount.AccountType.BOSS, ClubAccount.AccountType.PROVIDER))
            if guarded and (type(business_cycle) is not int or business_cycle <= 0):
                logging.getLogger(__name__).warning('站内消息实时通知缺少原周期: account_id=%s', account_id)
                return
            layer = get_channel_layer()
            if layer is not None:
                payload = {'account_id': account_id}
                if guarded:
                    payload['business_cycle'] = business_cycle
                if assignment_message_id:
                    payload['assignment_message_id'] = assignment_message_id
                async_to_sync(layer.group_send)(
                    f'account_{account_id}',
                    {'type': 'site_message_update', 'data': payload},
                )
        except Exception:
            logging.getLogger(__name__).warning('站内消息实时通知失败: account_id=%s', account_id, exc_info=True)

    transaction.on_commit(send)
