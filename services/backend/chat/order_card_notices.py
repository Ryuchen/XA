"""Refresh chat sessions whose existing order cards remain visible."""
import logging

from stores.db import transaction

logger = logging.getLogger(__name__)


def schedule(order_id):
    """Only query cards after the order transaction has committed."""
    if type(order_id) is not int or order_id <= 0:
        return
    transaction.on_commit(lambda: _refresh_safely(order_id), robust=True)


def _refresh_safely(order_id):
    try:
        refresh(order_id)
    except Exception:
        # The order operation has already committed; websocket delivery is
        # only an invalidation and must not change its result.
        logger.exception('咨询订单卡片状态刷新失败 order=%s', order_id)


def refresh(order_id):
    from club_accounts.models import ClubAccount
    from .models import ChatMessage
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer
    from .order_context import chat_order_queryset

    # Only customer-originated cards carry the original owner/cycle snapshot.
    # Never infer the recipient from a current assignment or another store.
    layer = get_channel_layer()
    if layer is None:
        return
    cards = (ChatMessage.objects.filter(related_order_id=order_id, is_from_support=False,
                notification_recipient_id__isnull=False, notification_recipient_cycle__isnull=False)
             .select_related('session__account').order_by('session_id', '-pk'))
    handled = set()
    for message in cards.iterator(chunk_size=200):
        session = message.session
        if session.pk in handled:
            continue
        account = session.account
        cycle = message.notification_recipient_cycle
        if (not account or account.pk != message.notification_recipient_id
                or account.account_type not in (ClubAccount.AccountType.BOSS, ClubAccount.AccountType.PROVIDER)
                or not account.is_active or not account.can_login or not account.license_membership_active
                or type(cycle) is not int or cycle != account.business_cycle
                or not chat_order_queryset(account).filter(pk=order_id).exists()):
            continue
        handled.add(session.pk)
        # No order or aftersale details are broadcast. The existing socket
        # consumer rechecks this session owner and the original cycle.
        try:
            async_to_sync(layer.group_send)(f'account_{account.pk}', {
                'type': 'chat_session_update',
                'data': {'session_id': session.pk, 'recipient_business_cycle': cycle},
            })
        except Exception:
            logger.exception('咨询订单卡片刷新通道不可用 session=%s', session.pk)
