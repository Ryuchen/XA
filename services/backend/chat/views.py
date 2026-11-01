import hashlib
import re

from django.db import transaction
from django.db.models import Q
from PIL import Image, UnidentifiedImageError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated
from .models import ChatMessage
from .notifier import notify_chat_message
from .serializers import ChatMessageSerializer, ChatSessionSerializer, ChatReadSerializer
from .read_state import mark_read_through
from .sessions import account_chat_session

MESSAGE_PAGE_SIZE = 50
CLIENT_MESSAGE_ID = re.compile(r'^[a-zA-Z0-9_-]{8,64}$')


class ChatSessionView(APIView):
    """C 端会话：GET 返回会话概览 + 历史消息（自动创建会话）。"""

    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        if request.account.account_type == ClubAccount.AccountType.STAFF:
            return Response({'code': 403, 'msg': '客服请使用后台工作台'})

        session = account_chat_session(request.account, request.legacy_user)
        messages = session.messages.all()
        before = request.query_params.get('before_id')
        if before is not None:
            if not before.isascii() or not before.isdigit() or int(before) < 1 or len(before) > 18:
                return Response({'code': 400, 'msg': '聊天记录位置无效'}, status=400)
            anchor = messages.filter(pk=int(before)).only('id', 'created_at').first()
            if anchor is None:
                return Response({'code': 400, 'msg': '聊天记录位置已变化，请重新加载'}, status=400)
            messages = messages.filter(Q(created_at__lt=anchor.created_at) | Q(created_at=anchor.created_at, pk__lt=anchor.pk))
        rows = list(messages.order_by('-created_at', '-id')[:MESSAGE_PAGE_SIZE + 1])
        has_more = len(rows) > MESSAGE_PAGE_SIZE
        messages = list(reversed(rows[:MESSAGE_PAGE_SIZE]))
        return Response({
            'code': 0,
            'data': {
                'session': ChatSessionSerializer(session).data,
                'messages': ChatMessageSerializer(
                    messages, many=True, context={'request': request}
                ).data,
                'has_more': has_more,
                'next_before_id': messages[0].pk if has_more else None,
            },
        })


class ChatSendView(APIView):
    """C 端发送消息：文本或图片（multipart）。"""

    permission_classes = [IsClubAccountAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def post(self, request):
        if request.account.account_type == ClubAccount.AccountType.STAFF:
            return Response({'code': 403, 'msg': '客服请使用后台工作台'})

        client_id = request.data.get('client_message_id')
        if client_id is not None and (not isinstance(client_id, str) or not CLIENT_MESSAGE_ID.fullmatch(client_id)):
            return Response({'code': 400, 'msg': '消息编号无效，请重新发送'}, status=400)

        content = request.data.get('content') or ''
        if not isinstance(content, str) or len(content) > 1000:
            return Response({'code': 400, 'msg': '消息须为不超过 1000 字的文本'})
        content = content.strip()
        image = request.FILES.get('image')
        if not content and not image:
            return Response({'code': 400, 'msg': '消息内容不能为空'})
        if image:
            if image.size > 5 * 1024 * 1024:
                return Response({'code': 400, 'msg': '图片不能超过 5MB'})
            try:
                picture = Image.open(image)
                if picture.format not in ('JPEG', 'PNG', 'WEBP'):
                    return Response({'code': 400, 'msg': '请上传 JPG、PNG 或 WebP 图片'})
                picture.verify()
                image.seek(0)
            except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
                return Response({'code': 400, 'msg': '图片内容无效'})

        content_type = (
            ChatMessage.ContentType.IMAGE if image else ChatMessage.ContentType.TEXT
        )

        fingerprint = ''
        if client_id:
            digest = hashlib.sha256()
            digest.update(content_type.encode('utf-8'))
            digest.update(b'\x00')
            digest.update(content.encode('utf-8'))
            digest.update(b'\x00')
            if image:
                for chunk in image.chunks():
                    digest.update(chunk)
                image.seek(0)
            fingerprint = digest.hexdigest()

        with transaction.atomic():
            session = account_chat_session(request.account, request.legacy_user)
            if client_id:
                existing = session.messages.filter(sender_account=request.account, client_message_id=client_id).first()
                if existing:
                    if existing.message_fingerprint != fingerprint:
                        return Response({'code': 409, 'msg': '消息内容已变化，请重新发送'}, status=409)
                    return Response({'code': 0, 'data': ChatMessageSerializer(existing, context={'request': request}).data})
            message = ChatMessage.objects.create(
                session=session,
                sender=request.legacy_user,
                sender_account=request.account,
                is_from_support=False,
                content_type=content_type,
                content=content,
                image=image,
                client_message_id=client_id,
                message_fingerprint=fingerprint,
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


class ChatMessageStatusView(APIView):
    """Find the sender's message by its identifier without sending it again."""
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        if request.account.account_type == ClubAccount.AccountType.STAFF:
            return Response({'code': 403, 'msg': '客服请使用后台工作台'}, status=403)
        client_id = request.query_params.get('client_message_id')
        if not isinstance(client_id, str) or not CLIENT_MESSAGE_ID.fullmatch(client_id):
            return Response({'code': 400, 'msg': '消息编号无效'}, status=400)
        session = account_chat_session(request.account, request.legacy_user)
        message = session.messages.filter(sender_account=request.account, client_message_id=client_id).first()
        return Response({'code': 0, 'data': {'status': 'FOUND' if message else 'UNKNOWN',
            'message': ChatMessageSerializer(message, context={'request': request}).data if message else None}})


class ChatReadView(APIView):
    """C 端标记已读：用户读取客服消息，清零用户未读数。"""

    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request):
        data = ChatReadSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with transaction.atomic():
            session = account_chat_session(request.account, request.legacy_user)
            if 'message_ids' in data.validated_data:
                unread = session.messages.filter(is_from_support=True, is_read=False)
                unread.filter(pk__in=data.validated_data['message_ids']).update(is_read=True)
                session.unread_user = unread.count()
            else:
                session.unread_user = mark_read_through(session, from_support=True, through_id=data.validated_data.get('through_id'))
            session.save(update_fields=['unread_user', 'updated_at'])
        return Response({'code': 0, 'data': {'unread_user': session.unread_user}})
