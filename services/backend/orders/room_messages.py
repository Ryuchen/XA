"""Plain-text room discussion with store-local, current-participant access."""
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.permissions import IsClubAccountAuthenticated
from stores.db import transaction
from .models import Order, OrderRoom, OrderRoomMessage
from .reservation_permissions import staff_can
from .rooms import message_role


def _message(row, request):
    from common.private_media import private_media_url
    image = getattr(row, 'image_attachment', None)
    return {'id': row.pk, 'author_account_id': row.author_id, 'author_role': row.author_role,
            'content': row.content, 'created_at': row.created_at,
            'content_type': 'IMAGE' if image else 'TEXT',
            'image_url': private_media_url(request, image.image) if image else None}


class OrderRoomMessageView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def _access(self, account, order_id, *, lock=False):
        if not 0 < order_id <= 9223372036854775807:
            return None, None, None
        orders = Order.objects.filter(pk=order_id)
        if lock:
            orders = orders.select_for_update()
        order = orders.first()
        if not order:
            return None, None, None
        rooms = OrderRoom.objects.filter(order=order)
        if lock:
            rooms = rooms.select_for_update()
        room = rooms.first()
        role = message_role(order, account) if room else None
        return order, room, role

    @transaction.atomic
    def get(self, request, order_id):
        _, room, role = self._access(request.account, order_id, lock=True)
        if not room or not role:
            return Response({'code': 404, 'msg': '房间不存在'}, status=404)
        queryset = room.messages.select_related('image_attachment').order_by('-pk')
        if role == 'FORMER_PROVIDER':
            queryset = queryset.filter(author=request.account)
        from console.pagination import ConsolePagination
        paginator = ConsolePagination()
        page = paginator.paginate_queryset(queryset, request)
        response = paginator.get_paginated_response([_message(row, request) for row in page])
        response['Cache-Control'] = 'no-store'
        return response

    @transaction.atomic
    def post(self, request, order_id):
        order, room, role = self._access(request.account, order_id, lock=True)
        if not room or not role:
            return Response({'code': 404, 'msg': '房间不存在'}, status=404)
        if (not isinstance(request.data, dict) or set(request.data) != {'content', 'client_request_id'}
                or not isinstance(request.data['content'], str) or not 0 < len(request.data['content']) <= 1000
                or not request.data['content'].strip() or not isinstance(request.data['client_request_id'], str)
                or not 0 < len(request.data['client_request_id']) <= 64
                or request.data['client_request_id'] != request.data['client_request_id'].strip()):
            return Response({'code': 400, 'msg': '请填写消息内容及有效请求编号'}, status=400)
        content, key = request.data['content'], request.data['client_request_id']
        existing = room.messages.filter(author=request.account, request_id=key).first()
        if existing:
            if existing.content != content or hasattr(existing, 'image_attachment'):
                return Response({'code': 409, 'errorCode': 'REQUEST_CONFLICT', 'msg': '请求编号已用于其他消息'}, status=409)
            return Response({'code': 0, 'data': _message(existing, request)})
        if role == 'FORMER_PROVIDER' or (role == 'STAFF' and not staff_can(request.account, 'support:edit')):
            return Response({'code': 403, 'msg': '当前身份不能发送房间消息'}, status=403)
        if order.status not in (Order.Status.GRABBED, Order.Status.IN_SERVICE):
            return Response({'code': 400, 'msg': '订单已结束，不能发送房间消息'}, status=400)
        row = OrderRoomMessage.objects.create(room=room, author=request.account, author_role=role,
                                              content=content, request_id=key)
        from .room_message_notifier import schedule_room_message_update
        schedule_room_message_update(order)
        return Response({'code': 0, 'data': _message(row, request), 'msg': '消息已发送'})
