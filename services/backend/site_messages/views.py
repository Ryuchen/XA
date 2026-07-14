from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Message
from .serializers import MessageDetailSerializer, MessageListItemSerializer


class MessageListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = Message.objects.filter(recipient=request.user)
        msg_type = request.query_params.get('type')
        if msg_type:
            qs = qs.filter(type=msg_type)

        try:
            page = max(1, int(request.query_params.get('page', 1)))
            page_size = max(1, min(100, int(request.query_params.get('page_size', 20))))
        except (TypeError, ValueError):
            page, page_size = 1, 20

        total = qs.count()
        unread = Message.objects.filter(recipient=request.user, is_read=False).count()

        start = (page - 1) * page_size
        items = qs.order_by('-created_at')[start:start + page_size]

        return Response({
            'code': 0,
            'data': {
                'messages': MessageListItemSerializer(items, many=True).data,
                'total': total,
                'unread': unread,
                'page': page,
                'page_size': page_size,
            }
        })


class MessageDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, message_id):
        try:
            msg = Message.objects.get(id=message_id, recipient=request.user)
        except Message.DoesNotExist:
            return Response({'code': 404, 'msg': '消息不存在'})

        if not msg.is_read:
            msg.is_read = True
            msg.save(update_fields=['is_read'])

        return Response({'code': 0, 'data': MessageDetailSerializer(msg).data})


class MessageReadAllView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        Message.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
        return Response({'code': 0, 'msg': '全部已读'})


class MessageUnreadCountView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        unread = Message.objects.filter(recipient=request.user, is_read=False).count()
        return Response({'code': 0, 'data': {'unread': unread}})
