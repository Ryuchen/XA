"""消息工具函数：供其他 app（如 orders 状态机）调用。"""
from typing import Optional


def create_cycle_message(*, recipient_account_id, recipient_business_cycle, **kwargs):
    """Deliver deferred notices only to the original in-store membership."""
    if not recipient_account_id or not recipient_business_cycle:
        return None
    from club_accounts.models import ClubAccount
    from stores.db import transaction
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=recipient_account_id,
            business_cycle=recipient_business_cycle, license_membership_active=True).first()
        if not account:
            return None
        return create_message(recipient_account_id=account.pk,
            recipient_business_cycle=recipient_business_cycle, **kwargs)


def create_message(
    *,
    recipient_id: Optional[int] = None,
    recipient_account_id: Optional[int] = None,
    recipient_business_cycle: Optional[int] = None,
    title: str,
    preview: str = '',
    detail: str = '',
    msg_type: str = 'SYSTEM',
    action_url: str = '',
    related_order_id: Optional[int] = None,
    assignment_alert: bool = False,
):
    """创建一条站内消息。失败不抛异常，避免影响主业务。"""
    try:
        from .models import Message
        if recipient_account_id is None:
            from club_accounts.models import LegacyAccountMap
            recipient_account_id = LegacyAccountMap.objects.filter(
                legacy_user_id=recipient_id,
            ).values_list('account_id', flat=True).first()
        message = Message.objects.create(
            recipient_id=recipient_id,
            recipient_account_id=recipient_account_id,
            recipient_business_cycle=recipient_business_cycle,
            type=msg_type,
            title=title[:100],
            preview=preview[:255],
            detail=detail,
            action_url=action_url[:255],
            related_order_id=related_order_id,
            assignment_alert=assignment_alert,
        )
        from .notifier import notify_messages_after_commit
        notify_messages_after_commit(message.recipient_account_id,
                                     assignment_message_id=message.pk if assignment_alert else None,
                                     business_cycle=message.recipient_business_cycle)
        return message
    except Exception:
        return None
