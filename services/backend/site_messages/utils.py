"""消息工具函数：供其他 app（如 orders 状态机）调用。"""
from typing import Optional


def create_message(
    *,
    recipient_id: int,
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
        return Message.objects.create(
            recipient_id=recipient_id,
            type=msg_type,
            title=title[:100],
            preview=preview[:255],
            detail=detail,
            action_url=action_url[:255],
            related_order_id=related_order_id,
        )
    except Exception:
        return None
