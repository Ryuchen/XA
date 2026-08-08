"""预约接口（R1~R7）。

统一信封：一律 HTTP 200 + ``{code, data, msg}``，业务码取
0 / 400 / 403 / 404 / 409。HTTP 状态码只用来表达传输层问题，
业务上的「不允许」不该让客户端的网络层去分辨。

角色口径：

* **老板（BOSS）**：发起预约、取消自己的预约、把自己的预约转成订单；
* **陪玩（PROVIDER）**：确认 / 拒绝别人约自己的预约、取消已确认的预约；
* **客服（STAFF）**：全都能做（代客操作），并可查看全量列表。
"""

from django.db import transaction
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated

from .models import Reservation
from .notifier import notify_order_update
from .reservation_serializers import (
    ReservationCancelSerializer,
    ReservationCreateSerializer,
    ReservationSerializer,
)
from .reservation_services import (
    ReservationConflictError,
    ReservationError,
    convert_reservation_to_order,
    create_reservation,
)
from .reservation_state_machine import (
    IllegalReservationTransitionError,
    transition_reservation,
)
from .services import push_message_safe as _push_message_safe

# 列表 ?status= 的对外取值 -> 内部状态。未收录的取值一律 400。
RESERVATION_STATUS_FILTER_MAP = {
    'pending': Reservation.Status.PENDING,
    'confirmed': Reservation.Status.CONFIRMED,
    'rejected': Reservation.Status.REJECTED,
    'cancelled': Reservation.Status.CANCELLED,
    'converted': Reservation.Status.CONVERTED,
    'expired': Reservation.Status.EXPIRED,
}

# 列表分页上限：预约量级远小于订单，一页 50 条足够，防止拉全表
MAX_PAGE_SIZE = 50


def _first_error(errors) -> str:
    """把 DRF 的嵌套错误结构压成一句人话。"""
    if isinstance(errors, dict):
        return _first_error(next(iter(errors.values())))
    if isinstance(errors, list) and errors:
        return _first_error(errors[0])
    return str(errors)


def _is_staff(account) -> bool:
    return account.account_type == ClubAccount.AccountType.STAFF


def _base_queryset():
    return Reservation.objects.select_related(
        'customer', 'customer_account', 'provider', 'provider_account',
        'service', 'order',
    )


def _visible_reservation(account, reservation_id: int):
    """按身份取一条可见的预约；不可见与不存在一律当作不存在。

    把「无权查看」暴露成 403 等于告诉外人「这条预约确实存在」，
    这里统一收敛成 404。
    """
    qs = _base_queryset().filter(id=reservation_id)
    if not _is_staff(account):
        qs = qs.filter(customer_account=account) | qs.filter(
            provider_account=account,
        )
    return qs.first()


class ReservationListCreateView(APIView):
    """R1 发起预约 / R2 我的预约列表。"""

    permission_classes = [IsClubAccountAuthenticated]
    throttle_scope = 'order_write'

    def get(self, request):
        account = request.account
        qs = _base_queryset()

        role = (request.query_params.get('role') or '').strip().lower()
        if _is_staff(account) and role in ('', 'all'):
            pass  # 客服默认看全量
        elif role == 'provider':
            qs = qs.filter(provider_account=account)
        elif role == 'customer':
            qs = qs.filter(customer_account=account)
        elif account.account_type == ClubAccount.AccountType.PROVIDER:
            qs = qs.filter(provider_account=account)
        else:
            qs = qs.filter(customer_account=account)

        status_param = (request.query_params.get('status') or '').strip().lower()
        if status_param:
            internal = RESERVATION_STATUS_FILTER_MAP.get(status_param)
            if internal is None:
                return Response({'code': 400, 'msg': '不支持的预约状态筛选'})
            qs = qs.filter(status=internal)

        try:
            page = max(int(request.query_params.get('page', 1)), 1)
            page_size = int(request.query_params.get('page_size', 20))
        except (TypeError, ValueError):
            return Response({'code': 400, 'msg': '分页参数非法'})
        page_size = min(max(page_size, 1), MAX_PAGE_SIZE)

        total = qs.count()
        offset = (page - 1) * page_size
        items = list(qs[offset:offset + page_size])
        return Response({
            'code': 0,
            'data': {
                'total': total,
                'page': page,
                'page_size': page_size,
                'items': ReservationSerializer(
                    items, many=True, context={'request': request},
                ).data,
            },
        })

    def post(self, request):
        account = request.account
        if account.account_type not in (
            ClubAccount.AccountType.BOSS,
            ClubAccount.AccountType.STAFF,
        ):
            return Response({'code': 403, 'msg': '仅玩家或客服可发起预约'})

        serializer = ReservationCreateSerializer(
            data=request.data, context={'request': request},
        )
        if not serializer.is_valid():
            return Response({'code': 400, 'msg': _first_error(serializer.errors)})

        data = serializer.validated_data
        provider_account = data['provider_account']
        if provider_account.pk == account.pk:
            return Response({'code': 400, 'msg': '不能预约自己'})

        try:
            reservation = create_reservation(
                customer_account=account,
                customer=request.legacy_user,
                provider_account=provider_account,
                service=data['service'],
                start_time=data['start_time'],
                end_time=data['end_time'],
                game_rounds=data['game_rounds'],
                game_region=data['game_region'],
                game_nickname=data['game_nickname'],
                game_uid=data['game_uid'],
                remark=data['remark'],
                operator=request.legacy_user,
                operator_account=account,
            )
        except ReservationConflictError as exc:
            return Response({'code': 409, 'msg': str(exc)})
        except ReservationError as exc:
            return Response({'code': 400, 'msg': str(exc)})

        if reservation.provider_id:
            _push_message_safe(
                recipient_id=reservation.provider_id,
                title='有新的预约待确认',
                preview=(
                    f'{reservation.start_time:%m-%d %H:%M} 起'
                    f'{reservation.duration_minutes} 分钟，请尽快确认'
                ),
                msg_type='ORDER',
            )

        return Response({
            'code': 0,
            'msg': '预约成功，等待大神确认',
            'data': ReservationSerializer(
                reservation, context={'request': request},
            ).data,
        })


class ReservationDetailView(APIView):
    """R3 预约详情。"""

    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request, reservation_id: int):
        reservation = _visible_reservation(request.account, reservation_id)
        if reservation is None:
            return Response({'code': 404, 'msg': '预约不存在'})
        return Response({
            'code': 0,
            'data': ReservationSerializer(
                reservation, context={'request': request},
            ).data,
        })


class _ReservationTransitionView(APIView):
    """确认 / 拒绝 / 取消三个动作的公共骨架。

    三者的差异只有「谁能做、目标状态是什么、理由写进哪个字段」，
    其余（取数、可见性、状态机异常翻译、出参）完全一致。
    """

    permission_classes = [IsClubAccountAuthenticated]
    throttle_scope = 'order_write'

    target_status = ''
    reason_field = ''
    success_msg = ''

    def check_actor(self, account, reservation) -> str:
        """返回空串表示放行，否则返回拒绝原因。"""
        raise NotImplementedError

    def post(self, request, reservation_id: int):
        account = request.account
        reservation = _visible_reservation(account, reservation_id)
        if reservation is None:
            return Response({'code': 404, 'msg': '预约不存在'})

        denied = self.check_actor(account, reservation)
        if denied:
            return Response({'code': 403, 'msg': denied})

        serializer = ReservationCancelSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'code': 400, 'msg': _first_error(serializer.errors)})
        reason = serializer.validated_data['reason'].strip()

        update_fields = []

        def write_reason(r: Reservation) -> None:
            if self.reason_field:
                setattr(r, self.reason_field, reason)

        if self.reason_field:
            update_fields.append(self.reason_field)

        try:
            reservation = transition_reservation(
                reservation.id,
                self.target_status,
                operator=request.legacy_user,
                operator_account=account,
                reason=reason,
                side_effect=write_reason,
                update_fields=update_fields,
            )
        except IllegalReservationTransitionError:
            return Response({
                'code': 409,
                'msg': f'当前状态（{reservation.get_status_display()}）不允许该操作',
            })

        return Response({
            'code': 0,
            'msg': self.success_msg,
            'data': ReservationSerializer(
                reservation, context={'request': request},
            ).data,
        })


class ReservationConfirmView(_ReservationTransitionView):
    """R5 陪玩确认预约。"""

    target_status = Reservation.Status.CONFIRMED
    reason_field = ''
    success_msg = '已确认预约'

    def check_actor(self, account, reservation) -> str:
        if _is_staff(account) or reservation.provider_account_id == account.pk:
            return ''
        return '仅被预约的大神或客服可确认'

    def post(self, request, reservation_id: int):
        response = super().post(request, reservation_id)
        if response.data.get('code') == 0:
            data = response.data['data']
            _push_message_safe(
                recipient_id=data['customer']['legacy_user_id'],
                title='大神已确认你的预约',
                preview=f"预约 {data['reservation_no']} 已确认，请按时上号",
                msg_type='ORDER',
            )
        return response


class ReservationRejectView(_ReservationTransitionView):
    """R6 陪玩拒绝预约。"""

    target_status = Reservation.Status.REJECTED
    reason_field = 'reject_reason'
    success_msg = '已拒绝预约'

    def check_actor(self, account, reservation) -> str:
        if _is_staff(account) or reservation.provider_account_id == account.pk:
            return ''
        return '仅被预约的大神或客服可拒绝'


class ReservationCancelView(_ReservationTransitionView):
    """R4 取消预约。

    待确认阶段是老板单方面反悔，已确认后双方都可能有变故，因此
    ``CONFIRMED`` 状态下陪玩也放行 —— 状态机保证转换本身合法。
    """

    target_status = Reservation.Status.CANCELLED
    reason_field = 'cancel_reason'
    success_msg = '已取消预约'

    def check_actor(self, account, reservation) -> str:
        if _is_staff(account):
            return ''
        if reservation.customer_account_id == account.pk:
            return ''
        if (
            reservation.provider_account_id == account.pk
            and reservation.status == Reservation.Status.CONFIRMED
        ):
            return ''
        return '无权取消该预约'


class ReservationConvertView(APIView):
    """R7 预约转订单：唯一动钱的入口，仅老板本人与客服可发起。"""

    permission_classes = [IsClubAccountAuthenticated]
    throttle_scope = 'order_write'

    def post(self, request, reservation_id: int):
        account = request.account
        if account.account_type not in (
            ClubAccount.AccountType.BOSS,
            ClubAccount.AccountType.STAFF,
        ):
            return Response({'code': 403, 'msg': '仅玩家或客服可转单'})

        reservation = _visible_reservation(account, reservation_id)
        if reservation is None:
            return Response({'code': 404, 'msg': '预约不存在'})
        if not _is_staff(account) and reservation.customer_account_id != account.pk:
            return Response({'code': 403, 'msg': '仅预约所属玩家可转单'})

        code, msg, order = convert_reservation_to_order(
            request,
            reservation.id,
            client_request_id=request.data.get('client_request_id'),
        )
        if code != 0:
            return Response({'code': code, 'msg': msg})

        # 通知放在事务外：推送失败不该回滚已经成立的订单
        transaction.on_commit(lambda: notify_order_update(order))
        if order.provider_id:
            _push_message_safe(
                recipient_id=order.provider_id,
                title='预约已转为订单',
                preview=f'预约「{order.service_name_snapshot}」已成单，请尽快开始服务',
                msg_type='ORDER',
                related_order_id=order.id,
            )

        from .serializers import OrderSerializer
        reservation.refresh_from_db()
        return Response({
            'code': 0,
            'msg': msg,
            'data': {
                'order': OrderSerializer(order, context={'request': request}).data,
                'reservation': ReservationSerializer(
                    reservation, context={'request': request},
                ).data,
            },
        })
