"""预约接口（R1~R7）。

统一信封：一律 HTTP 200 + ``{code, data, msg}``，业务码取
0 / 400 / 403 / 404 / 409。HTTP 状态码只用来表达传输层问题，
业务上的「不允许」不该让客户端的网络层去分辨。

角色口径：

* **老板（BOSS）**：发起预约、取消自己的预约、把自己的预约转成订单；
* **陪玩（PROVIDER）**：确认 / 拒绝别人约自己的预约、取消已确认的预约；
* **客服（STAFF）**：按权限代客创建、取消和转单；不得代陪玩确认或拒绝。
"""

from stores.db import transaction
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated

from .models import Reservation, ServiceItem
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


def _reservation_notice(reservation, recipient, title, preview, *, exclude_account_id=None, source_order=None):
    """Persist a role-specific link after the reservation transaction commits."""
    is_provider = recipient == 'provider'
    recipient_id = reservation.provider_id if is_provider else reservation.customer_id
    account_id = reservation.provider_account_id if is_provider else reservation.customer_account_id
    page = 'reservations' if is_provider else 'reservation-detail'
    guarded = getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
    base_cycle = reservation.provider_business_cycle if is_provider else reservation.customer_business_cycle
    recipients = [(recipient_id, account_id, base_cycle)]
    if is_provider and reservation.required_provider_count == 2:
        recipients = getattr(reservation, '_notice_recipients', None)
        if recipients is None:
            recipients = list(reservation.seats.filter(provider_account__isnull=False).values_list(
                'provider_id', 'provider_account_id', 'provider_business_cycle'))
    for user_id, target_id, original_cycle in recipients:
        if target_id == exclude_account_id:
            continue
        from site_messages.order_cycle import recipient_cycle
        cycle = recipient_cycle(source_order, target_id) if source_order else original_cycle
        transaction.on_commit(lambda user_id=user_id, target_id=target_id, cycle=cycle: _push_message_safe(
            recipient_id=user_id, recipient_account_id=target_id, title=title, preview=preview,
            msg_type='ORDER', action_url=f'/pages/{page}/index?id={reservation.pk}',
            recipient_business_cycle=cycle, require_cycle=guarded))


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
    ).prefetch_related('order__providers', 'seats', 'seat_events')


def _provider_reservations(account):
    """Original booking plus converted bookings currently assigned to them."""
    return (Q(required_provider_count=1, provider_account=account)
        | Q(required_provider_count=2, seats__provider_account=account)
        | Q(required_provider_count=2, seat_events__provider_account=account, seat_events__action='INVITED')
        | Q(status=Reservation.Status.CONVERTED,
        order__providers__provider_account=account, order__providers__released_at__isnull=True))


def _visible_reservation(account, reservation_id: int, *, for_action=False):
    """按身份取一条可见的预约；不可见与不存在一律当作不存在。

    把「无权查看」暴露成 403 等于告诉外人「这条预约确实存在」，
    这里统一收敛成 404。
    """
    qs = _base_queryset().filter(id=reservation_id)
    if not _is_staff(account):
        qs = qs.filter(Q(customer_account=account) | _provider_reservations(account)).distinct()
    reservation = qs.first()
    from .reservation_deadlines import due
    if reservation and due(reservation, now=timezone.now()):
        from .reservation_expiration import expire_reservation
        expire_reservation(reservation.pk)
        # Expiring a team invitation can remove the requesting provider from
        # its seat. Reapply the same visibility filter after the state change;
        # the initially authorized object may no longer belong to this actor.
        reservation = qs.first()
    if reservation and for_action and not _is_staff(account):
        from .reservation_cycle import viewer_cycle_status, viewer_participation_status
        if (viewer_cycle_status(reservation, account) != 'CURRENT'
                or viewer_participation_status(reservation, account) == 'HISTORICAL'):
            return None
    return reservation


class ReservationAccountFieldsView(APIView):
    """Expose only the effective fields needed by an authorized booking operator."""
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request, service_id):
        account = request.account
        if not _is_staff(account) or not staff_can(account, 'order:dispatch'):
            return Response({'code': 403, 'msg': '无代派单权限'}, status=403)
        service = ServiceItem.objects.filter(pk=service_id, is_active=True).first()
        if service is None:
            return Response({'code': 404, 'msg': '服务不存在或不可用'}, status=404)
        from .account_fields import effective
        try:
            fields = effective(service)
        except ValueError:
            return Response({'code': 409, 'msg': '本店游戏资料配置异常，请联系管理员核对'}, status=409)
        return Response({'code': 0, 'data': {'service_id': service.pk, 'account_fields': fields}})


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
            qs = qs.filter(_provider_reservations(account)).distinct()
        elif role == 'customer':
            qs = qs.filter(customer_account=account)
        elif account.account_type == ClubAccount.AccountType.PROVIDER:
            qs = qs.filter(_provider_reservations(account)).distinct()
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
        sort_start = request.query_params.get('sort_start', 'desc')
        if sort_start not in ('asc', 'desc') or (cursor_mode and sort_start != 'desc'):
            return Response({'code': 400, 'msg': '预约排序方式无效'})
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
            if sort_start == 'asc':
                qs = qs.order_by('start_time', 'id')
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
            create = create_reservation
            provider_args = {'provider_account': provider_account}
            if data['service'].fulfillment_provider_count == 2:
                from .reservation_team import create_team_reservation
                create = create_team_reservation
                provider_args = {'providers': data['provider_accounts']}
            reservation = create(
                customer_account=data['customer_account'],
                customer=data['customer'],
                **provider_args,
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
        if not (getattr(settings, 'RESERVATION_NOTICE_RECOVERY_ENABLED', False)
                and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)):
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
        reservation = _visible_reservation(account, reservation_id, for_action=True)
        if reservation is None:
            return Response({'code': 404, 'msg': '预约不存在'})

        if (reservation.required_provider_count == 2 and self.target_status in
                (Reservation.Status.CONFIRMED, Reservation.Status.REJECTED)):
            if account.account_type != ClubAccount.AccountType.PROVIDER:
                return Response({'code': 403, 'msg': '仅受邀陪玩本人可确认或拒绝'}, status=403)
            serializer = ReservationCancelSerializer(data=request.data)
            if not serializer.is_valid():
                return Response({'code': 400, 'msg': _first_error(serializer.errors)})
            from .reservation_team import decide
            try:
                reservation, error = decide(reservation.id, account,
                    accept=self.target_status == Reservation.Status.CONFIRMED,
                    reason=serializer.validated_data['reason'].strip())
            except ReservationError as exc:
                return Response({'code': 400, 'msg': str(exc)})
            replayed = getattr(reservation, '_decision_replayed', False)
            if (not getattr(reservation, '_decision_notice_already_handled', False)
                    and (not replayed or getattr(reservation, '_decision_refreshed', False))):
                notify_reservation_after_commit(reservation)
            if (not replayed and not getattr(reservation, '_decision_notice_already_handled', False)
                    and not (getattr(settings, 'RESERVATION_STATUS_NOTICE_RECOVERY_ENABLED', False)
                    and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))):
                _reservation_notice(reservation, 'customer', '双陪预约状态更新',
                                    f'预约 {reservation.reservation_no} 的陪玩确认状态已更新')
            if (replayed and getattr(reservation, '_decision_refreshed', False)
                    and not (getattr(settings, 'RESERVATION_EXPIRY_NOTICE_RECOVERY_ENABLED', False)
                             and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))):
                _reservation_notice(reservation, 'customer', '预约席位已超时',
                                    '已有陪玩确认，请重新选择空出的席位')
            if error:
                return Response({'code': 409, 'msg': error})
            return Response({'code': 0, 'msg': self.success_msg,
                             'data': ReservationSerializer(reservation, context={'request': request}).data})

        denied = self.check_actor(account, reservation)
        if denied:
            return Response({'code': 403, 'msg': denied}, status=403)

        serializer = ReservationCancelSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'code': 400, 'msg': _first_error(serializer.errors)})
        reason = serializer.validated_data['reason'].strip()

        update_fields = []
        confirmation_rules = None
        decision_cycle = None

        def ensure_current(r: Reservation) -> None:
            nonlocal confirmation_rules
            if not _is_staff(account):
                from .reservation_cycle import viewer_cycle_status
                current_actor = ClubAccount.objects.select_for_update().filter(pk=account.pk).first()
                if (not current_actor or not current_actor.is_active or not current_actor.can_login
                        or current_actor.business_cycle != account.business_cycle
                        or viewer_cycle_status(r, current_actor) != 'CURRENT'
                        or self.check_actor(current_actor, r)):
                    raise ReservationError('预约属于此前入店周期或当前账号已无权操作')
            from .reservation_deadlines import due, required_rules
            if due(r, now=timezone.now()):
                raise IllegalReservationTransitionError('预约已过期，请重新预约', current_status=Reservation.Status.EXPIRED)
            if self.target_status in (Reservation.Status.CONFIRMED, Reservation.Status.REJECTED):
                if r.provider_account_id != account.pk or r.provider_business_cycle != decision_cycle:
                    raise ReservationError('预约邀请属于此前入店周期，请联系老板重新预约')
            if self.target_status == Reservation.Status.CONFIRMED:
                if r.provider_account_id != account.pk:
                    raise ReservationError('仅被预约的陪玩本人可确认')
                if settings.PROVIDER_WECHAT_GATE_ENABLED:
                    from users.models import EscortProfile
                    profile = EscortProfile.objects.select_for_update().filter(account_id=r.provider_account_id).first()
                    if not profile:
                        raise ReservationError('陪玩资料不存在')
                    error = profile.wechat_acceptance_error()
                    if error:
                        raise ReservationError(error)
                if r.confirmation_config_version is not None:
                    try:
                        confirmation_rules = required_rules(lock=True)
                    except ValueError as exc:
                        raise ReservationError(str(exc)) from exc

        def write_reason(r: Reservation) -> None:
            if self.reason_field:
                setattr(r, self.reason_field, reason)
            if confirmation_rules:
                from .reservation_deadlines import payment_snapshot
                if (r.confirmed_at >= r.start_time or
                        (r.confirmation_deadline and r.confirmed_at >= r.confirmation_deadline)):
                    raise IllegalReservationTransitionError('预约确认时限已到', current_status=Reservation.Status.EXPIRED)
                r.payment_deadline = payment_snapshot(r.start_time, r.confirmed_at, confirmation_rules)
                r.payment_timeout_minutes = confirmation_rules['payment_minutes']
                r.payment_config_version = confirmation_rules['version']

        if self.reason_field:
            update_fields.append(self.reason_field)
        status_recovery = (getattr(settings, 'RESERVATION_STATUS_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))
        if self.target_status == Reservation.Status.CONFIRMED:
            update_fields.extend(['payment_deadline', 'payment_timeout_minutes', 'payment_config_version'])

        try:
            with transaction.atomic():
                if (account.account_type == ClubAccount.AccountType.PROVIDER
                        and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)):
                    from club_accounts.provider_lifecycle import (ProviderLifecycleError, lock_provider_operation,
                                                                  provider_membership_current)
                    try:
                        lock_provider_operation(account)
                        if (self.target_status == Reservation.Status.CONFIRMED
                                and not provider_membership_current(account)):
                            raise ReservationError('陪玩门店身份已变化，请重新确认')
                    except ProviderLifecycleError as exc:
                        raise ReservationError('陪玩状态暂不可核验，请稍后重试') from exc
                if account.account_type == ClubAccount.AccountType.PROVIDER:
                    current_actor = ClubAccount.objects.select_for_update().filter(
                        pk=account.pk, account_type=ClubAccount.AccountType.PROVIDER).first()
                    if (not current_actor or not current_actor.is_active or not current_actor.can_login
                            or not current_actor.license_membership_active
                            or current_actor.business_cycle != account.business_cycle):
                        raise ReservationError('陪玩门店身份已变化，请重新登录')
                    decision_cycle = current_actor.business_cycle
                if self.target_status == Reservation.Status.CONFIRMED:
                    # Match reservation creation's provider -> reservation
                    # lock order before checking and persisting confirmation.
                    from .reservation_services import lock_provider_profile
                    lock_provider_profile(account)
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
        except ReservationError as exc:
            return Response({'code': 400, 'msg': str(exc)}, status=400)

        notify_reservation_after_commit(reservation)
        if self.target_status == Reservation.Status.CONFIRMED and not status_recovery:
            _reservation_notice(reservation, 'customer', '预约已确认',
                                f'预约 {reservation.reservation_no} 已确认，请核对报价后转为订单')
        elif self.target_status == Reservation.Status.REJECTED and not status_recovery:
            _reservation_notice(reservation, 'customer', '预约已拒绝',
                                f'预约 {reservation.reservation_no} 已拒绝' + (f'：{reason}' if reason else ''))
        elif self.target_status == Reservation.Status.CANCELLED:
            preview = f'预约 {reservation.reservation_no} 已取消' + (f'：{reason}' if reason else '')
            if not (getattr(settings, 'RESERVATION_CANCELLATION_NOTICE_RECOVERY_ENABLED', False)
                    and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)):
                if _is_staff(account) or account.pk != reservation.customer_account_id:
                    _reservation_notice(reservation, 'customer', '预约已取消', preview)
                _reservation_notice(reservation, 'provider', '预约已取消', preview,
                                    exclude_account_id=account.pk)

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
        if reservation.provider_account_id == account.pk and account.account_type == ClubAccount.AccountType.PROVIDER:
            return ''
        return '仅被预约的陪玩本人可确认'

class ReservationRejectView(_ReservationTransitionView):
    """R6 陪玩拒绝预约。"""

    target_status = Reservation.Status.REJECTED
    reason_field = 'reject_reason'
    success_msg = '已拒绝预约'

    def check_actor(self, account, reservation) -> str:
        if reservation.provider_account_id == account.pk and account.account_type == ClubAccount.AccountType.PROVIDER:
            return ''
        return '仅被预约的陪玩本人可拒绝'


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
            (reservation.provider_account_id == account.pk if reservation.required_provider_count == 1 else
             reservation.seats.filter(provider_account=account, status='CONFIRMED').exists())
            and reservation.status == Reservation.Status.CONFIRMED
        ):
            return ''
        return '无权取消该预约'


class ReservationReplaceProviderView(APIView):
    permission_classes = [IsClubAccountAuthenticated]
    throttle_scope = 'order_write'

    def post(self, request, reservation_id):
        account = request.account
        booking = _visible_reservation(account, reservation_id, for_action=True)
        if booking is None:
            return Response({'code': 404, 'msg': '预约不存在'})
        if account.account_type != ClubAccount.AccountType.BOSS or booking.customer_account_id != account.pk:
            return Response({'code': 403, 'msg': '仅预约老板本人可替换陪玩'}, status=403)
        try:
            number = serializers.IntegerField(min_value=1, max_value=2).run_validation(request.data.get('seat_number'))
            provider_id = serializers.IntegerField(min_value=1).run_validation(request.data.get('provider_account_id'))
        except serializers.ValidationError:
            return Response({'code': 400, 'msg': '请选择有效席位和陪玩'})
        request_id = request.data.get('client_request_id')
        if request_id == '':
            request_id = None
        if request_id is not None and (not isinstance(request_id, str) or not request_id.strip()
                                       or request_id != request_id.strip() or len(request_id) > 64):
            return Response({'code': 400, 'msg': '请求编号无效'})
        target = ClubAccount.objects.filter(pk=provider_id, account_type=ClubAccount.AccountType.PROVIDER).first()
        if target is None:
            return Response({'code': 400, 'msg': '陪玩不存在'})
        from .reservation_team import replace
        try:
            booking, error = replace(booking.pk, account, number, target, client_request_id=request_id)
        except ReservationConflictError as exc:
            return Response({'code': 409, 'msg': str(exc)})
        except ReservationError as exc:
            return Response({'code': 400, 'msg': str(exc)})
        replayed = getattr(booking, '_replacement_replayed', False)
        if not replayed or getattr(booking, '_replacement_refreshed', False):
            notify_reservation_after_commit(booking)
        if (getattr(booking, '_replacement_refreshed', False)
                and not (getattr(settings, 'RESERVATION_EXPIRY_NOTICE_RECOVERY_ENABLED', False)
                         and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))):
            confirmed = booking.seats.filter(status='CONFIRMED').exists()
            _reservation_notice(booking, 'customer', '预约席位已超时',
                '已有陪玩确认，请重新选择空出的席位' if confirmed else '有陪玩确认超时，请查看预约并重新选择空出的席位')
        if error:
            return Response({'code': 409, 'msg': error})
        seat = booking.seats.get(number=number)
        if (not replayed and not (getattr(settings, 'RESERVATION_NOTICE_RECOVERY_ENABLED', False)
                 and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))
                and seat.provider_account_id == target.pk and seat.provider_business_cycle == target.business_cycle):
            seat_cycle = seat.provider_business_cycle
            guarded = getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
            transaction.on_commit(lambda: _push_message_safe(recipient_id=seat.provider_id,
                recipient_account_id=seat.provider_account_id, title='有新的预约待确认',
                preview=f'预约 {booking.reservation_no} 等待您确认', msg_type='ORDER',
                action_url=f'/pages/reservations/index?id={booking.pk}',
                recipient_business_cycle=seat_cycle, require_cycle=guarded))
        return Response({'code': 0, 'msg': '已邀请陪玩',
                         'data': ReservationSerializer(booking, context={'request': request}).data})


class ReservationQuoteView(APIView):
    """Read the current payable amount for the reservation's real customer."""
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request, reservation_id: int):
        if _is_staff(request.account) and not staff_can(request.account, 'order:dispatch'):
            return Response({'code': 403, 'msg': '无代派单权限'})
        reservation = _visible_reservation(request.account, reservation_id, for_action=True)
        if reservation is None:
            return Response({'code': 404, 'msg': '预约不存在'})
        if not _is_staff(request.account) and reservation.customer_account_id != request.account.pk:
            return Response({'code': 403, 'msg': '仅预约所属玩家可查看报价'})
        if reservation.status != Reservation.Status.CONFIRMED:
            return Response({'code': 409, 'msg': '仅已确认的预约可转订单'})
        if not reservation.customer_account_id or not reservation.provider_id:
            return Response({'code': 400, 'msg': '预约账号资料不完整，请联系客服核对'})
        from wallet.services import get_wallet, WalletAddressingError
        from engagement.funding_wallet import funding_quote, parse_funding_selection
        gift, compensation = parse_funding_selection(request.query_params)
        token = request.query_params.get('discount_selection_token')
        try:
            raw_coupon = request.query_params.get('user_coupon_id')
            coupon_id = serializers.IntegerField(min_value=1).run_validation(raw_coupon) if raw_coupon not in (None, '') else None
        except serializers.ValidationError:
            return Response({'code': 400, 'msg': '优惠券编号无效'})
        if token and 'user_coupon_id' in request.query_params:
            return Response({'code': 400, 'msg': '请通过优惠组合或优惠券编号选择，不可同时提交'})
        explicit_no_coupon = 'user_coupon_id' in request.query_params and coupon_id is None
        options = []
        discount_enabled = False
        from .purchase_benefits import is_experience
        recommended = None
        if not is_experience(reservation.service) and not reservation.service.is_addon and not (
                reservation.service.service_category_id and reservation.service.service_category.is_gift):
            from dataclasses import asdict
            from .discount_recommendation import service_discount_options
            from .reservation_pricing import hourly_minutes
            candidates = service_discount_options(reservation.customer_account, reservation.service,
                reservation.game_rounds, hourly_minutes(reservation))
            recommended = asdict(candidates[0])
        if getattr(settings, 'DISCOUNT_COMBINATIONS_PAYMENT_ENABLED', False) and not is_experience(reservation.service):
            from dataclasses import asdict
            from .discount_selection import signed_option
            from .reservation_pricing import hourly_minutes
            reservation_minutes = hourly_minutes(reservation)
            candidates = service_discount_options(reservation.customer_account, reservation.service, reservation.game_rounds, reservation_minutes)
            options = [{**asdict(item), 'selection_token': signed_option(reservation.customer_account,
                reservation.service, reservation.game_rounds, item, reservation_minutes)} for item in candidates]
            if coupon_id is not None:
                match = next((item for item in options if item['coupon_id'] == coupon_id), None)
                if not match:
                    return Response({'code': 400, 'msg': '优惠券不适用当前预约'})
                token, coupon_id = match['selection_token'], None
            elif explicit_no_coupon:
                token = next(item['selection_token'] for item in options if item['coupon_id'] is None)
            elif not token:
                token = options[0]['selection_token']
            discount_enabled = True
        try:
            data = reservation_order_pricing(reservation, token, gift, compensation, user_coupon_id=coupon_id)
            wallet = get_wallet(account=reservation.customer_account, user=reservation.customer, create=False)
        except (ReservationError, WalletAddressingError) as exc:
            return Response({'code': 400, 'msg': str(exc)})
        if discount_enabled:
            for option in options:
                if option['key'] == data.get('discount_option_key'):
                    option['selection_token'] = token
                    break
        balance = wallet.balance if wallet else 0
        points_preview = None
        if 'preview_points_hundredths' in request.query_params:
            if not getattr(settings, 'POINTS_REDEMPTION_PREVIEW_ENABLED', False):
                return Response({'code': 400, 'msg': '积分试算尚未开放'}, status=400)
            try:
                selected = serializers.IntegerField(min_value=0, max_value=2147483647).run_validation(
                    request.query_params.get('preview_points_hundredths'))
                from .points_redemption_preview import preview_service_points
                points_preview = preview_service_points(account=reservation.customer_account, service=data['service'],
                                                        deal_amount=data['amount'], selected_points=selected)
                if getattr(settings, 'SERVICE_POINTS_CHECKOUT_TESTING', False) and points_preview.get('selected_groups'):
                    from .reservation_points_test import quote as points_quote
                    _, points_preview['checkout_test_token'] = points_quote(reservation.customer_account, reservation, data, selected)
                    points_preview['checkout_test_only'] = True
                elif (getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
                        and points_preview.get('selected_groups')):
                    from .points_business_access import checkout, checkout_shape_allowed
                    from .reservation_points_test import quote as points_quote
                    if checkout_shape_allowed(data):
                        if data.get('personal_game_snapshots') is not None:
                            from .services import ensure_provider_dispatch_locks
                            ensure_provider_dispatch_locks([item.pk for item in data['provider_accounts']])
                        with transaction.atomic(), checkout(account=reservation.customer_account, service=data['service']):
                            if data.get('personal_game_snapshots') is not None:
                                from .models import ServiceItem
                                from .services import lock_provider_dispatch
                                from .delta_personal_commission import verify_formal_points_checkout
                                locked_service = ServiceItem.objects.select_for_update().get(pk=data['service'].pk)
                                lock_provider_dispatch([item.pk for item in data['provider_accounts']])
                                verify_formal_points_checkout(data, account=reservation.customer_account, service=locked_service)
                            _, points_preview['checkout_test_token'] = points_quote(reservation.customer_account, reservation, data, selected)
                        points_preview['checkout_test_only'] = False

            except (ValueError, serializers.ValidationError) as exc:
                return Response({'code': 400, 'msg': _first_error(getattr(exc, 'detail', str(exc)))}, status=400)
        funding_amount = (points_preview['estimated_payable_units'] if points_preview and points_preview.get('applicable')
                          else data['amount'])
        funding = funding_quote(wallet, funding_amount, gift, compensation)
        return Response({'code': 0, 'data': {
            'original_amount': data['original_amount'], 'boss_discount': data['boss_discount'],
            'promo_discount': data['promo_discount'], 'coupon_discount': data['coupon_discount'],
            'amount': data['amount'], 'payable_amount': funding_amount, 'wallet_balance': balance,
            **funding,
            'service': reservation_service_terms(data['service']),
            'discount_selection_enabled': discount_enabled,
            'discount_selection_token': token if discount_enabled else None,
            'discount_option_key': data.get('discount_option_key'),
            'discount_options': options,
            'recommended_discount': recommended,
            'user_coupon_id': data['user_coupon'].pk if data.get('user_coupon') else None,
            **({'points_preview': points_preview} if points_preview is not None else {}),
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

        if 'preview_points_hundredths' in request.data:
            return Response({'code': 400, 'msg': '积分数量需先报价并使用报价凭据确认'})
        if 'points_checkout_test_token' in request.data and not request.data.get('points_checkout_test_token'):
            return Response({'code': 400, 'msg': '积分报价凭据无效，请重新报价'})

        reservation = _visible_reservation(account, reservation_id, for_action=True)
        if reservation is None:
            return Response({'code': 404, 'msg': '预约不存在'})
        if not _is_staff(account) and reservation.customer_account_id != account.pk:
            return Response({'code': 403, 'msg': '仅预约所属玩家可转单'})

        from engagement.funding_wallet import parse_funding_selection
        gift, compensation = parse_funding_selection(request.data)
        try:
            raw_coupon = request.data.get('user_coupon_id')
            coupon_id = serializers.IntegerField(min_value=1).run_validation(raw_coupon) if raw_coupon not in (None, '') else None
        except serializers.ValidationError:
            return Response({'code': 400, 'msg': '优惠券编号无效'})
        if 'user_coupon_id' in request.data and request.data.get('discount_selection_token'):
            return Response({'code': 400, 'msg': '请通过优惠组合或优惠券编号选择，不可同时提交'})
        from .account_fields import FIELDS
        game_fields = {}
        try:
            for field in FIELDS:
                if field in request.data:
                    game_fields[field] = serializers.CharField(max_length=50, allow_blank=True).run_validation(request.data[field])
        except serializers.ValidationError:
            return Response({'code': 400, 'msg': '游戏资料格式无效，请核对'})
        confirmation = request.data.get('boss_confirmation', '')
        if _is_staff(account) and (game_fields or coupon_id is not None or request.data.get('points_checkout_test_token')
                                   or getattr(settings, 'BOSS_FUNDING_SELECTION_ENABLED', False)
                                   or getattr(settings, 'DISCOUNT_COMBINATIONS_PAYMENT_ENABLED', False)):
            if not isinstance(confirmation, str) or not 2 <= len(confirmation.strip()) <= 500:
                return Response({'code': 400, 'msg': '请记录老板明确确认的付款选择'})
            confirmation = confirmation.strip()
        else:
            confirmation = ''

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
            gift_balance_amount=gift,
            compensation_balance_amount=compensation,
            boss_confirmation=confirmation if _is_staff(account) else '',
            game_fields=game_fields,
            discount_selection_token=request.data.get('discount_selection_token'),
            points_checkout_test_token=request.data.get('points_checkout_test_token'),
            user_coupon_id=coupon_id,
        )
        if code != 0:
            return Response({'code': code, 'msg': msg})

        notify_reservation_after_commit(reservation)
        # 通知放在事务外：推送失败不该回滚已经成立的订单
        transaction.on_commit(lambda: notify_order_update(order))
        conversion_recovery = (getattr(settings, 'RESERVATION_CONVERSION_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))
        if _is_staff(account) and not conversion_recovery:
            _reservation_notice(reservation, 'customer', '预约已转为订单',
                                f'客服已将预约 {reservation.reservation_no} 转为订单，请查看详情', source_order=order)
        recipients = (list(order.providers.values_list('provider_id', 'provider_account_id'))
                      if reservation.required_provider_count == 2 else [(order.provider_id, order.provider_account_id)])
        for provider_id, provider_account_id in ([] if conversion_recovery else recipients):
            from site_messages.order_cycle import recipient_cycle
            cycle = recipient_cycle(order, provider_account_id)
            guarded = getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
            _push_message_safe(
                recipient_id=provider_id,
                recipient_account_id=provider_account_id,
                title='预约已转为订单',
                preview=f'预约「{order.service_name_snapshot}」已成单，请查看原预约时段并与老板确认服务安排',
                msg_type='ORDER',
                related_order_id=order.id,
                assignment_alert=True,
                recipient_business_cycle=cycle,
                require_cycle=guarded,
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
