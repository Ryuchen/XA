"""消息工具函数：供其他 app（如 orders 状态机）调用。"""
from typing import Optional


def create_message(
    *,
    recipient_id: int,
    recipient_account_id: Optional[int] = None,
    title: str,
    preview: str = '',
    detail: str = '',
    msg_type: str = 'SYSTEM',
    action_url: str = '',
    related_order_id: Optional[int] = None,
):
    """创建一条站内消息。失败不抛异常，避免影响主业务。"""
    try:
        from .models import Message
        if recipient_account_id is None:
            from club_accounts.models import LegacyAccountMap
            recipient_account_id = LegacyAccountMap.objects.filter(
                legacy_user_id=recipient_id,
            ).values_list('account_id', flat=True).first()
        return Message.objects.create(
            recipient_id=recipient_id,
            recipient_account_id=recipient_account_id,
            type=msg_type,
            title=title[:100],
            preview=preview[:255],
            detail=detail,
            action_url=action_url[:255],
            related_order_id=related_order_id,
        )
    except Exception:
        return None
