"""Order-room creation and access; no gameplay or settlement is implied."""
from django.db.models import Q
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated
from stores.db import transaction
from .models import Order, OrderRoom, OrderParticipationSegment
from .reservation_permissions import staff_can
from .state_machine import IllegalTransitionError


def create_assigned_room_locked(order):
    """Called after GRABBED is saved while the order transaction holds its lock."""
    if order.room_required_snapshot is not True:
        return None
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('创建订单房间必须持有订单事务')
    if order.status != Order.Status.GRABBED or order.payment_status != Order.PaymentStatus.PAID:
        raise IllegalTransitionError('订单付款或安排状态异常，不能创建房间')
    count = order.required_provider_count
    if count not in (1, 2) or not order.customer_account_id or not order.provider_account_id:
        raise IllegalTransitionError('订单身份或陪玩人数不完整，不能创建房间')
    assigned = list(order.providers.filter(released_at__isnull=True).values_list('provider_account_id', flat=True))
    if not assigned and count == 1:
        assigned = [order.provider_account_id]
    if (len(assigned) != count or None in assigned or len(set(assigned)) != count
            or order.provider_account_id not in assigned):
        raise IllegalTransitionError('订单陪玩尚未安排齐全，不能创建房间')
    room, _ = OrderRoom.objects.get_or_create(order=order)
    return room


class OrderRoomView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    @transaction.atomic
    def get(self, request, order_id):
        if not 0 < order_id <= 9223372036854775807:
            return Response({'code': 404, 'msg': '房间不存在'}, status=404)
        if not Order.objects.select_for_update().filter(pk=order_id).exists():
            return Response({'code': 404, 'msg': '房间不存在'}, status=404)
        account = request.account
        if account.account_type == ClubAccount.AccountType.BOSS:
            scope = Q(order__customer_account_id=account.pk)
            role = 'BOSS'
        elif account.account_type == ClubAccount.AccountType.PROVIDER:
            scope = Q(order__providers__provider_account_id=account.pk, order__providers__released_at__isnull=True)
            scope |= Q(order__required_provider_count=1, order__provider_account_id=account.pk,
                       order__providers__isnull=True)
            role = 'PROVIDER'
        elif account.account_type == ClubAccount.AccountType.STAFF and staff_can(account, 'order:view'):
            scope = Q()
            role = 'STAFF'
        else:
            return Response({'code': 404, 'msg': '房间不存在'}, status=404)
        room = OrderRoom.objects.select_related('order').filter(scope, order_id=order_id).first()
        if not room and account.account_type == ClubAccount.AccountType.PROVIDER:
            candidate = OrderRoom.objects.select_related('order').filter(order_id=order_id).first()
            if candidate and former_provider(candidate.order, account.pk):
                room, role = candidate, 'FORMER_PROVIDER'
        if not room:
            return Response({'code': 404, 'msg': '房间不存在'}, status=404)
        data = {'id': room.pk, 'order_id': room.order_id, 'order_status': room.order.status,
                'role': role, 'created_at': room.created_at,
                'capabilities': ['OWN_MESSAGE_HISTORY'] if role == 'FORMER_PROVIDER' else ['ENTER']}
        if role in ('BOSS', 'PROVIDER', 'STAFF'):
            data['has_open_objection'] = room.objections.filter(status='OPEN').exists()
        return Response({'code': 0, 'data': data})


def former_provider(order, account_id):
    return (order.providers.filter(provider_account_id=account_id, released_at__isnull=False).exists()
            or OrderParticipationSegment.objects.filter(attribution__order=order, provider_account_id=account_id,
                                                         released_at__isnull=False).exists())


def message_role(order, account):
    """Resolve permissions from current store-local order facts, never a room URL."""
    if account.account_type == ClubAccount.AccountType.BOSS and order.customer_account_id == account.pk:
        return 'BOSS'
    if account.account_type == ClubAccount.AccountType.STAFF and staff_can(account, 'order:view'):
        return 'STAFF'
    if account.account_type != ClubAccount.AccountType.PROVIDER:
        return None
    if order.providers.filter(provider_account_id=account.pk, released_at__isnull=True).exists():
        return 'PROVIDER'
    if (order.required_provider_count == 1 and order.provider_account_id == account.pk
            and not order.providers.exists()):
        return 'PROVIDER'
    if former_provider(order, account.pk):
        return 'FORMER_PROVIDER'
    return None
