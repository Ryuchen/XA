from django.db.models import Q
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.permissions import IsClubAccountAuthenticated
from .models import Message
from .serializers import MessageDetailSerializer, MessageListItemSerializer


class MessageListView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        qs = Message.objects.filter(recipient_account=request.account)
        msg_type = request.query_params.get('type')
        if msg_type:
            qs = qs.filter(type=msg_type)

        try:
            page = max(1, int(request.query_params.get('page', 1)))
            page_size = max(1, min(100, int(request.query_params.get('page_size', 20))))
        except (TypeError, ValueError):
            page, page_size = 1, 20

        total = qs.count()
        unread = Message.objects.filter(
            recipient_account=request.account,
            is_read=False,
        ).count()

        before = request.query_params.get('before')
        if before:
            try:
                anchor = qs.only('id', 'created_at').get(pk=int(before))
            except (ValueError, TypeError, Message.DoesNotExist):
                return Response({'code': 400, 'msg': '消息列表已变化，请刷新后重试'}, status=400)
            qs = qs.filter(Q(created_at__lt=anchor.created_at) | Q(created_at=anchor.created_at, pk__lt=anchor.pk))
            start = 0
        else:
            start = (page - 1) * page_size
        window = list(qs.order_by('-created_at', '-id')[start:start + page_size + 1])
        items = window[:page_size]
        has_more = len(window) > page_size

        return Response({
            'code': 0,
            'data': {
                'messages': MessageListItemSerializer(items, many=True).data,
                'total': total,
                'unread': unread,
                'page': page,
                'page_size': page_size,
                'has_more': has_more,
                'next_before': items[-1].pk if has_more and items else None,
            }
        })


class MessageDetailView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request, message_id):
        try:
            msg = Message.objects.get(
                id=message_id,
                recipient_account=request.account,
            )
        except Message.DoesNotExist:
            return Response({'code': 404, 'msg': '消息不存在'})

        if not msg.is_read:
            msg.is_read = True
            msg.save(update_fields=['is_read'])
            from .notifier import notify_messages_after_commit
            notify_messages_after_commit(request.account.pk)

        return Response({'code': 0, 'data': MessageDetailSerializer(msg).data})


class MessageReadAllView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request):
        updated = Message.objects.filter(
            recipient_account=request.account,
            is_read=False,
        ).update(is_read=True)
        if updated:
            from .notifier import notify_messages_after_commit
            notify_messages_after_commit(request.account.pk)
        return Response({'code': 0, 'msg': '全部已读'})


class MessageUnreadCountView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        unread = Message.objects.filter(
            recipient_account=request.account,
            is_read=False,
        ).count()
        return Response({'code': 0, 'data': {'unread': unread}})


class AssignmentAlertCursorView(APIView):
    """Reconcile missed alerts against unread assignments still being served."""
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        from club_accounts.models import ClubAccount
        from orders.models import Order
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩账号可查看派单提醒'}, status=403)
        raw = request.query_params.get('after')
        if raw is not None and (not raw.isascii() or not raw.isdigit()):
            return Response({'code': 400, 'msg': '提醒位置无效'}, status=400)
        after = int(raw) if raw is not None else None
        messages = Message.objects.filter(recipient_account=request.account, assignment_alert=True)
        latest = messages.order_by('-id').values_list('id', flat=True).first() or 0
        pending = False
        if after is not None and latest > after:
            active = Order.objects.filter(
                Q(provider_account=request.account) | Q(providers__provider_account=request.account, providers__released_at__isnull=True),
                status__in=[Order.Status.GRABBED, Order.Status.IN_SERVICE],
                payment_status=Order.PaymentStatus.PAID,
            ).values('pk')
            pending = messages.filter(id__gt=after, id__lte=latest, is_read=False, related_order_id__in=active).exists()
        return Response({'code': 0, 'data': {'latest_id': latest, 'pending': pending}})
