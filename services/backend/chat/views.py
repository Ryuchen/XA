from django.db import transaction
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated
from .models import ChatMessage, ChatSession
from .notifier import notify_chat_message
from .serializers import ChatMessageSerializer, ChatSessionSerializer

MESSAGE_PAGE_SIZE = 50


class ChatSessionView(APIView):
    """C 端会话：GET 返回会话概览 + 历史消息（自动创建会话）。"""

    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        if request.account.account_type == ClubAccount.AccountType.STAFF:
            return Response({'code': 403, 'msg': '客服请使用后台工作台'})

        session, _ = ChatSession.objects.get_or_create(
            account=request.account,
            defaults={'user': request.legacy_user},
        )
        messages = session.messages.all().order_by('-created_at')[:MESSAGE_PAGE_SIZE]
        messages = list(reversed(messages))
        return Response({
            'code': 0,
            'data': {
                'session': ChatSessionSerializer(session).data,
                'messages': ChatMessageSerializer(
                    messages, many=True, context={'request': request}
                ).data,
            },
        })


class ChatSendView(APIView):
    """C 端发送消息：文本或图片（multipart）。"""

    permission_classes = [IsClubAccountAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def post(self, request):
        if request.account.account_type == ClubAccount.AccountType.STAFF:
            return Response({'code': 403, 'msg': '客服请使用后台工作台'})

        content = (request.data.get('content') or '').strip()
        image = request.FILES.get('image')
        if not content and not image:
            return Response({'code': 400, 'msg': '消息内容不能为空'})

        content_type = (
            ChatMessage.ContentType.IMAGE if image else ChatMessage.ContentType.TEXT
        )

        with transaction.atomic():
            session, _ = ChatSession.objects.select_for_update().get_or_create(
                account=request.account,
                defaults={'user': request.legacy_user},
            )
            message = ChatMessage.objects.create(
                session=session,
                sender=request.legacy_user,
                sender_account=request.account,
                is_from_support=False,
                content_type=content_type,
                content=content,
                image=image,
            )
            session.last_message = message.preview
            session.last_message_at = message.created_at
            session.unread_support += 1
            session.save(update_fields=['last_message', 'last_message_at', 'unread_support', 'updated_at'])

        notify_chat_message(session, message)
        return Response({
            'code': 0,
            'data': ChatMessageSerializer(message, context={'request': request}).data,
        })


class ChatReadView(APIView):
    """C 端标记已读：用户读取客服消息，清零用户未读数。"""

    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request):
        with transaction.atomic():
            session, _ = ChatSession.objects.select_for_update().get_or_create(
                account=request.account,
                defaults={'user': request.legacy_user},
            )
            session.messages.filter(is_from_support=True, is_read=False).update(is_read=True)
            session.unread_user = 0
            session.save(update_fields=['unread_user', 'updated_at'])
        return Response({'code': 0, 'data': {'unread_user': 0}})
