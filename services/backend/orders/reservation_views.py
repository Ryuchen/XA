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
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated

from .models import Reservation
from .notifier import notify_order_update
from .reservation_notifier import notify_reservation_after_commit
from .reservation_permissions import staff_can
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
    reservation_order_pricing,
    reservation_service_terms,
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


def _reservation_notice(reservation, recipient, title, preview):
    """Persist a role-specific link after the reservation transaction commits."""
    is_provider = recipient == 'provider'
    recipient_id = reservation.provider_id if is_provider else reservation.customer_id
    account_id = reservation.provider_account_id if is_provider else reservation.customer_account_id
    page = 'reservations' if is_provider else 'reservation-detail'
    transaction.on_commit(lambda: _push_message_safe(
        recipient_id=recipient_id,
        recipient_account_id=account_id,
        title=title,
        preview=preview,
        msg_type='ORDER',
        action_url=f'/pages/{page}/index?id={reservation.pk}',
    ))


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
    reservation = qs.first()
    if reservation and reservation.status in Reservation.ACTIVE_STATUSES and reservation.end_time <= timezone.now():
        from .reservation_expiration import expire_reservation
        expire_reservation(reservation.pk)
        # A concurrent reader or the scheduled scan may have expired it first.
        reservation.refresh_from_db()
    return reservation


class ReservationListCreateView(APIView):
    """R1 发起预约 / R2 我的预约列表。"""

    permission_classes = [IsClubAccountAuthenticated]
    throttle_scope = 'order_write'

    def get(self, request):
        account = request.account
        if _is_staff(account) and not staff_can(account, 'order:view'):
            return Response({'code': 403, 'msg': '无查看订单权限'})
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

        from .reservation_expiration import expire_due_reservations
        expire_due_reservations(qs)

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
        cursor_mode = request.query_params.get('cursor') == '1'
        if cursor_mode:
            before_time = request.query_params.get('before_time')
            before_id = request.query_params.get('before_id')
            if bool(before_time) != bool(before_id):
                return Response({'code': 400, 'msg': '预约分页位置无效'})
            if before_time:
                try:
                    before_time = serializers.DateTimeField().run_validation(before_time)
                    before_id = serializers.IntegerField(min_value=1).run_validation(before_id)
                except serializers.ValidationError:
                    return Response({'code': 400, 'msg': '预约分页位置无效'})
                from django.db.models import Q
                qs = qs.filter(Q(start_time__lt=before_time) | Q(start_time=before_time, id__lt=before_id))
            chunk = list(qs.order_by('-start_time', '-id')[:page_size + 1])
            items = chunk[:page_size]
            next_cursor = ({'start_time': items[-1].start_time.isoformat(), 'id': items[-1].id}
                           if len(chunk) > page_size else None)
        else:
            offset = (page - 1) * page_size
            items = list(qs[offset:offset + page_size])
            next_cursor = None
        return Response({
            'code': 0,
            'data': {
                'total': total,
                'page': page,
                'page_size': page_size,
                'next_cursor': next_cursor,
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
        if _is_staff(account) and not staff_can(account, 'order:dispatch'):
            return Response({'code': 403, 'msg': '无代派单权限'})

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
                customer_account=data['customer_account'],
                customer=data['customer'],
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

        notify_reservation_after_commit(reservation)
        _reservation_notice(
            reservation, 'provider', '有新的预约待确认',
            f'{timezone.localtime(reservation.start_time):%m-%d %H:%M} 起'
            f'{reservation.duration_minutes} 分钟，请尽快确认',
        )
        if _is_staff(account):
            _reservation_notice(reservation, 'customer', '客服已创建预约',
                                f'预约 {reservation.reservation_no} 已创建，等待陪玩确认')

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
        if _is_staff(request.account) and not staff_can(request.account, 'order:view'):
            return Response({'code': 403, 'msg': '无查看订单权限'})
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
        if _is_staff(account):
            permission = 'order:cancel' if self.target_status == Reservation.Status.CANCELLED else 'order:dispatch'
            if not staff_can(account, permission):
                return Response({'code': 403, 'msg': '无预约操作权限'})
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

        def ensure_current(r: Reservation) -> None:
            if r.end_time <= timezone.now():
                raise IllegalReservationTransitionError('预约已过期，请重新预约', current_status=Reservation.Status.EXPIRED)

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
                pre_check=ensure_current,
                side_effect=write_reason,
                update_fields=update_fields,
            )
        except IllegalReservationTransitionError as exc:
            from .reservation_expiration import expire_reservation
            expire_reservation(reservation.id)
            # The unlocked visibility read may be older than the row seen by
            # the state machine after it acquires the reservation lock.
            current = dict(Reservation.Status.choices).get(exc.current_status, '未知')
            return Response({
                'code': 409,
                'msg': f'当前状态（{current}）不允许该操作',
            })

        notify_reservation_after_commit(reservation)
        if self.target_status == Reservation.Status.CONFIRMED:
            _reservation_notice(reservation, 'customer', '预约已确认',
                                f'预约 {reservation.reservation_no} 已确认，请核对报价后转为订单')
            if _is_staff(account):
                _reservation_notice(reservation, 'provider', '预约已确认',
                                    f'客服已确认预约 {reservation.reservation_no}，等待转为订单')
        elif self.target_status == Reservation.Status.REJECTED:
            _reservation_notice(reservation, 'customer', '预约已拒绝',
                                f'预约 {reservation.reservation_no} 已拒绝' + (f'：{reason}' if reason else ''))
            if _is_staff(account):
                _reservation_notice(reservation, 'provider', '预约已拒绝',
                                    f'客服已拒绝预约 {reservation.reservation_no}' + (f'：{reason}' if reason else ''))
        elif self.target_status == Reservation.Status.CANCELLED:
            preview = f'预约 {reservation.reservation_no} 已取消' + (f'：{reason}' if reason else '')
            if _is_staff(account) or account.pk != reservation.customer_account_id:
                _reservation_notice(reservation, 'customer', '预约已取消', preview)
            if _is_staff(account) or account.pk != reservation.provider_account_id:
                _reservation_notice(reservation, 'provider', '预约已取消', preview)

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


class ReservationQuoteView(APIView):
    """Read the current payable amount for the reservation's real customer."""
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request, reservation_id: int):
        if _is_staff(request.account) and not staff_can(request.account, 'order:dispatch'):
            return Response({'code': 403, 'msg': '无代派单权限'})
        reservation = _visible_reservation(request.account, reservation_id)
        if reservation is None:
            return Response({'code': 404, 'msg': '预约不存在'})
        if not _is_staff(request.account) and reservation.customer_account_id != request.account.pk:
            return Response({'code': 403, 'msg': '仅预约所属玩家可查看报价'})
        if reservation.status != Reservation.Status.CONFIRMED:
            return Response({'code': 409, 'msg': '仅已确认的预约可转订单'})
        if not reservation.customer_account_id or not reservation.customer_id or not reservation.provider_id:
            return Response({'code': 400, 'msg': '预约账号资料不完整，请联系客服核对'})
        from wallet.services import get_wallet, WalletAddressingError
        try:
            data = reservation_order_pricing(reservation)
            wallet = get_wallet(account=reservation.customer_account, user=reservation.customer, create=False)
        except (ReservationError, WalletAddressingError) as exc:
            return Response({'code': 400, 'msg': str(exc)})
        balance = wallet.balance if wallet else 0
        return Response({'code': 0, 'data': {
            'original_amount': data['original_amount'], 'boss_discount': data['boss_discount'],
            'promo_discount': data['promo_discount'], 'coupon_discount': data['coupon_discount'],
            'amount': data['amount'], 'wallet_balance': balance,
            'balance_sufficient': bool(wallet and wallet.is_active and balance >= data['amount']),
            'service': reservation_service_terms(data['service']),
        }})


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
        if _is_staff(account) and not staff_can(account, 'order:dispatch'):
            return Response({'code': 403, 'msg': '无代派单权限'})

        reservation = _visible_reservation(account, reservation_id)
        if reservation is None:
            return Response({'code': 404, 'msg': '预约不存在'})
        if not _is_staff(account) and reservation.customer_account_id != account.pk:
            return Response({'code': 403, 'msg': '仅预约所属玩家可转单'})

        try:
            expected_amount = (serializers.IntegerField(min_value=0).run_validation(request.data['expected_amount'])
                               if 'expected_amount' in request.data else None)
        except serializers.ValidationError:
            return Response({'code': 400, 'msg': '确认金额无效，请刷新报价'})
        expected_service = None
        if 'expected_service' in request.data:
            from .serializers import ExpectedServiceSerializer
            expected_serializer = ExpectedServiceSerializer(data=request.data['expected_service'])
            if not expected_serializer.is_valid():
                return Response({'code': 400, 'msg': '商品确认信息无效，请刷新报价'})
            expected_service = expected_serializer.validated_data
        code, msg, order = convert_reservation_to_order(
            request,
            reservation.id,
            client_request_id=request.data.get('client_request_id'),
            expected_amount=expected_amount,
            expected_service=expected_service,
        )
        if code != 0:
            return Response({'code': code, 'msg': msg})

        notify_reservation_after_commit(reservation)
        # 通知放在事务外：推送失败不该回滚已经成立的订单
        transaction.on_commit(lambda: notify_order_update(order))
        if _is_staff(account):
            _reservation_notice(reservation, 'customer', '预约已转为订单',
                                f'客服已将预约 {reservation.reservation_no} 转为订单，请查看详情')
        if order.provider_id:
            _push_message_safe(
                recipient_id=order.provider_id,
                recipient_account_id=order.provider_account_id,
                title='预约已转为订单',
                preview=f'预约「{order.service_name_snapshot}」已成单，请查看原预约时段并与老板确认服务安排',
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
