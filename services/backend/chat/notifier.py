"""聊天消息 WS 广播 helper：复用现有 /ws/orders/ 单连接通道。"""
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .serializers import ChatMessageSerializer, ChatSessionSerializer


def notify_chat_message(session, message):
    """广播一条新消息。

    - 用户发出 → 推送给所有客服(operators) + 回推用户多端(account_{id})
    - 客服发出 → 推送给会话所属用户(account_{id}) + 同步其他客服(operators)
    """
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    payload = {
        'session_id': session.id,
        'account_id': session.account_id,
        'message': ChatMessageSerializer(message).data,
    }

    async_to_sync(channel_layer.group_send)(
        'operators',
        {'type': 'chat_message', 'data': payload},
    )
    async_to_sync(channel_layer.group_send)(
        f'account_{session.account_id}',
        {'type': 'chat_message', 'data': payload},
    )


def notify_chat_session_update(session):
    """会话概览变更（未读清零等）同步给客服工作台。"""
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    async_to_sync(channel_layer.group_send)(
        'operators',
        {
            'type': 'chat_session_update',
            'data': {
                'session_id': session.id,
                'account_id': session.account_id,
                'session': ChatSessionSerializer(session).data,
            },
        },
    )
