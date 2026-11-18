import hashlib
import re

from django.conf import settings
from stores.db import transaction
from django.db.models import Q
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated
from .models import ChatMessage, ChatSession
from .notifier import schedule_chat_message
from .serializers import ChatMessageSerializer, ChatSessionSerializer, ChatReadSerializer
from .read_state import mark_read_through
from .sessions import account_chat_session, lock_chat_account

MESSAGE_PAGE_SIZE = 50
CLIENT_MESSAGE_ID = re.compile(r'^[a-zA-Z0-9_-]{8,64}$')


class ChatParticipationDetailView(APIView):
    """A past participant can read only their own recorded work and case."""
    permission_classes = [IsClubAccountAuthenticated]

    @transaction.atomic
    def get(self, request, message_id):
        from .authorization import can_read_session
        from .order_context import participated_order_queryset, serialized_order_card
        from orders.models import OrderParticipationSegment, OrderWorkStint

        account = request.account
        if account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 404, 'msg': '履约记录不存在或无权查看'}, status=404)
        lock_chat_account(account)
        message = ChatMessage.objects.select_related('session', 'related_order').filter(pk=message_id).first()
        if (not message or not message.related_order_id or not can_read_session(account, message.session)
                or not participated_order_queryset(account).filter(pk=message.related_order_id).exists()):
            return Response({'code': 404, 'msg': '履约记录不存在或无权查看'}, status=404)
        card = serialized_order_card(request, message)
        stints = OrderWorkStint.objects.filter(order_id=message.related_order_id, account=account,
            started_at__isnull=False).order_by('started_at', 'pk')
        work = [{'started_at': row.started_at, 'released_at': row.released_at,
                 'completed_at': row.completed_at} for row in stints]
        if not work:
            segments = OrderParticipationSegment.objects.filter(attribution__order_id=message.related_order_id,
                provider_account=account, started_at__isnull=False).order_by('started_at', 'pk')
            work = [{'started_at': row.started_at, 'released_at': row.released_at,
                     'completed_at': None} for row in segments]
        return Response({'code': 0, 'data': {'order_card': card, 'work_stints': work}})


class ChatUnreadView(APIView):
    """Read the existing conversation count without creating empty sessions."""
    permission_classes = [IsClubAccountAuthenticated]

    @transaction.atomic
    def get(self, request):
        if request.account.account_type == ClubAccount.AccountType.STAFF:
            return Response({'code': 403, 'msg': '客服请使用后台工作台'}, status=403)
        lock_chat_account(request.account)
        session = ChatSession.objects.filter(account=request.account).only('unread_user').first()
        if session is None and request.legacy_user is not None:
            session = ChatSession.objects.filter(account__isnull=True, user=request.legacy_user).only('unread_user').first()
        return Response({'code': 0, 'data': {'unread': session.unread_user if session else 0}})


class ChatSessionView(APIView):
    """C 端会话：GET 返回会话概览 + 历史消息（自动创建会话）。"""

    permission_classes = [IsClubAccountAuthenticated]

    @transaction.atomic
    def get(self, request):
        if request.account.account_type == ClubAccount.AccountType.STAFF:
            return Response({'code': 403, 'msg': '客服请使用后台工作台'}, status=403)

        session = account_chat_session(request.account, request.legacy_user)
        messages = session.messages.select_related('sender_account')
        before = request.query_params.get('before_id')
        if before is not None:
            if not before.isascii() or not before.isdigit() or int(before) < 1 or len(before) > 18:
                return Response({'code': 400, 'msg': '聊天记录位置无效'}, status=400)
            anchor = session.messages.filter(pk=int(before)).only('id', 'created_at').first()
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
            return Response({'code': 403, 'msg': '客服请使用后台工作台'}, status=403)

        client_id = request.data.get('client_message_id')
        if client_id is not None and (not isinstance(client_id, str) or not CLIENT_MESSAGE_ID.fullmatch(client_id)):
            return Response({'code': 400, 'msg': '消息编号无效，请重新发送'}, status=400)

        content = request.data.get('content') or ''
        if not isinstance(content, str) or len(content) > 1000:
            return Response({'code': 400, 'msg': '消息须为不超过 1000 字的文本'}, status=400)
        content = content.strip()
        image = request.FILES.get('image')
        order_ref = request.data.get('order_ref', '')
        if not isinstance(order_ref, str) or len(order_ref) > 32:
            return Response({'code': 400, 'msg': '订单信息无效，请重新选择'}, status=400)
        if not content and not image and not order_ref:
            return Response({'code': 400, 'msg': '消息内容不能为空'}, status=400)
        if image:
            from .images import chat_image_error
            error = chat_image_error(image)
            if error:
                return Response({'code': 400, 'msg': error}, status=400)

        content_type = (
            ChatMessage.ContentType.IMAGE if image else ChatMessage.ContentType.TEXT
        )

        digest = None
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

        with transaction.atomic():
            session = account_chat_session(request.account, request.legacy_user)
            if client_id:
                existing = session.messages.select_related('related_order').filter(
                    sender_account=request.account, client_message_id=client_id).first()
                if existing:
                    from .order_context import matches_message_order
                    if not matches_message_order(order_ref, existing):
                        return Response({'code': 409, 'msg': '消息内容已变化，请重新发送'}, status=409)
                    if existing.related_order_id:
                        digest.update(b'\x00order:')
                        digest.update(str(existing.related_order_id).encode('ascii'))
                    if existing.message_fingerprint != digest.hexdigest():
                        return Response({'code': 409, 'msg': '消息内容已变化，请重新发送'}, status=409)
                    return Response({'code': 0, 'data': ChatMessageSerializer(existing, context={'request': request}).data})
            order = None
            if order_ref:
                from .order_context import resolve_order
                order = resolve_order(request.account, order_ref, lock=True)
                if order is None:
                    return Response({'code': 404, 'msg': '订单不存在或无权关联'}, status=404)
            if digest and order:
                digest.update(b'\x00order:')
                digest.update(str(order.pk).encode('ascii'))
            fingerprint = digest.hexdigest() if digest else ''
            from .order_context import order_snapshot
            message = ChatMessage.objects.create(
                session=session,
                sender=request.legacy_user,
                sender_account=request.account,
                related_order=order,
                order_snapshot=order_snapshot(order) if order else {},
                is_from_support=False,
                content_type=content_type,
                content=content,
                image=image,
                client_message_id=client_id,
                message_fingerprint=fingerprint,
                notification_intent=getattr(settings, 'CHAT_NOTIFICATION_OUTBOX_ENABLED', False),
                notification_recipient_id=request.account.pk,
                notification_recipient_cycle=request.account.business_cycle,
            )
            session.last_message = message.preview
            session.last_message_at = message.created_at
            session.unread_support += 1
            session.save(update_fields=['last_message', 'last_message_at', 'unread_support', 'updated_at'])
            schedule_chat_message(session, message, owner_snapshot=(request.account.pk, request.account.business_cycle))
        return Response({
            'code': 0,
            'data': ChatMessageSerializer(message, context={'request': request}).data,
        })


class ChatMessageStatusView(APIView):
    """Find the sender's message by its identifier without sending it again."""
    permission_classes = [IsClubAccountAuthenticated]

    @transaction.atomic
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
        if request.account.account_type == ClubAccount.AccountType.STAFF:
            return Response({'code': 403, 'msg': '客服请使用后台工作台'}, status=403)
        data = ChatReadSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with transaction.atomic():
            session = account_chat_session(request.account, request.legacy_user)
            previous_unread = session.unread_user
            if 'message_ids' in data.validated_data:
                unread = session.messages.filter(is_from_support=True, is_read=False)
                unread.filter(pk__in=data.validated_data['message_ids']).update(is_read=True)
                session.unread_user = unread.count()
            else:
                session.unread_user = mark_read_through(session, from_support=True, through_id=data.validated_data.get('through_id'))
            changed = session.unread_user != previous_unread
            intent_key = None
            if changed:
                from .notifier import stage_chat_session_update
                intent_key = stage_chat_session_update(session, owner_snapshot=(request.account.pk, request.account.business_cycle))
            fields = ['unread_user', 'updated_at']
            if intent_key:
                fields.append('refresh_notification_intents')
            session.save(update_fields=fields)
            if changed:
                # Notify the reception workspace after commit so the current
                # receiver can refresh the read state. The shared event only
                # carries the session ID; consumers recheck their permissions.
                from .notifier import schedule_chat_session_update
                schedule_chat_session_update(session, owner_snapshot=(request.account.pk, request.account.business_cycle),
                                             intent_key=intent_key)
        return Response({'code': 0, 'data': {'unread_user': session.unread_user}})
