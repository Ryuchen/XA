"""预约领域服务：时间校验、冲突检测、创建与转单。

为什么不用数据库排他约束
------------------------
「同一个陪玩的两条预约不得时间重叠」这条约束，在 Postgres 上可以用
``tstzrange`` + ``ExclusionConstraint`` 一行搞定。但本项目生产库是 MySQL、
测试库是 sqlite，两者都不支持排他约束 —— 真按 PG 写，测试跑不起来，
上线也建不出约束，等于没有约束。

因此重叠检测统一走**悲观锁 + 区间查询**：

1. 先 ``select_for_update`` 锁住该陪玩的 ``EscortProfile`` 行（一把「档期锁」）；
2. 持锁状态下查有没有重叠的占用态预约；
3. 没有才插入。

两个并发请求抢同一个时间片时，后到的那个会阻塞在第 1 步，等前一个提交后
再查就一定能看见刚插入的那条，从而被拒。三种数据库上行为一致。

区间重叠的判定式（半开区间 ``[start, end)``）::

    N.start_time < E.end_time AND N.end_time > E.start_time

首尾相接（``09:00-10:00`` 与 ``10:00-11:00``）不算冲突。
"""

from datetime import timedelta
import hashlib
import json
from typing import Optional, Tuple

from django.conf import settings
from stores.db import transaction
from stores.context import current_currency_name
from django.db.models import Q, QuerySet
from django.utils import timezone
from rest_framework import serializers

from club_accounts.models import ClubAccount, LegacyAccountMap
from common.logging_utils import log_money_event, new_trace_id
from users.models import EscortProfile
from wallet.models import Transaction
from wallet.services import claim_fund_write, get_wallet, peek_fund_write

from .models import Order, OrderStatusLog, Reservation, ReservationStatusLog
from .reservation_state_machine import (
    log_reservation_event,
    transition_reservation,
)
from .services import ensure_provider_dispatch_locks
from .settlement import compute_split
from .state_machine import log_only, transition

# 预约时间粒度：所有起止时间必须对齐到该刻度（分钟）
SLOT_MINUTES = 15
# 单次预约的最短时长（分钟）：低于半小时的档期没有履约价值，徒增碎片
MIN_DURATION_MINUTES = 15
# 单次预约的最长时长（小时）：超过一天班的时长基本是误填
MAX_DURATION_HOURS = 12
# 最长提前期（天）：太远的档期变数过大，占着陪玩时间却极易鸽单
MAX_ADVANCE_DAYS = 30

# 占用时间片的状态（列表形式，保证 SQL 参数顺序稳定）
ACTIVE_STATUS_LIST = [
    Reservation.Status.PENDING,
    Reservation.Status.CONFIRMED,
]


class ReservationError(Exception):
    """预约领域异常基类。"""


class ReservationWindowError(ReservationError):
    """预约时间窗不合法（粒度 / 时长 / 提前期 / 过去时间）。"""


class ReservationConflictError(ReservationError):
    """与该陪玩已有的占用态预约时间重叠。"""

    def __init__(self, message: str, conflicting=None):
        super().__init__(message)
        self.conflicting = conflicting


def _legacy_user_for_account(account: Optional[ClubAccount]):
    """按 ClubAccount 反查 legacy ``CustomUser``，没有映射时返回 None。"""
    if account is None:
        return None
    mapping = LegacyAccountMap.objects.select_related(None).filter(
        account_id=account.pk,
    ).first()
    if mapping is None:
        return None
    from django.contrib.auth import get_user_model
    return get_user_model().objects.filter(id=mapping.legacy_user_id).first()


def validate_reservation_window(start_time, end_time, *, now=None, rules=None) -> int:
    """校验预约时间窗，返回时长（分钟）。

    五条规则，按「越基础越先报」的顺序检查，保证用户一次只看到一个最根本的错：

    1. 起止时间必填，且 ``end_time`` 必须晚于 ``start_time``；
    2. 起止时间都必须对齐到 :data:`SLOT_MINUTES` 刻度（秒/微秒必须为 0）；
    3. ``start_time`` 必须晚于当前时间（不能约过去）；
    4. 时长在 [:data:`MIN_DURATION_MINUTES`, :data:`MAX_DURATION_HOURS` 小时] 之间；
    5. ``start_time`` 不得超过当前时间 + :data:`MAX_ADVANCE_DAYS` 天。

    Args:
        start_time: 预约开始时间（aware datetime）。
        end_time: 预约结束时间（aware datetime）。
        now: 当前时间，便于测试注入；默认 ``timezone.now()``。

    Returns:
        int: 预约时长（分钟）。

    Raises:
        ReservationWindowError: 任一规则不满足。
    """
    if rules is None:
        from .reservation_deadlines import current_rules
        rules = current_rules()
    slot_minutes = rules['slot_minutes']
    minimum = rules['min_duration_minutes']
    maximum = rules['max_duration_minutes']
    advance = rules['max_advance_days']
    if start_time is None or end_time is None:
        raise ReservationWindowError('请选择预约开始与结束时间')
    if end_time <= start_time:
        raise ReservationWindowError('预约结束时间必须晚于开始时间')

    for label, value in (('开始', start_time), ('结束', end_time)):
        if value.second or value.microsecond or value.minute % slot_minutes:
            raise ReservationWindowError(
                f'预约{label}时间必须对齐到 {slot_minutes} 分钟刻度'
            )

    now = now or timezone.now()
    if start_time <= now:
        raise ReservationWindowError('预约开始时间必须晚于当前时间')

    duration_minutes = int((end_time - start_time).total_seconds() // 60)
    if duration_minutes < minimum:
        raise ReservationWindowError(
            f'单次预约时长不得少于 {minimum} 分钟'
        )
    if duration_minutes > maximum:
        raise ReservationWindowError(
            f'单次预约时长不得超过 {maximum} 分钟'
        )

    if start_time > now + timedelta(days=advance):
        raise ReservationWindowError(f'最多只能预约 {advance} 天内的档期')

    return duration_minutes


def find_conflicting_reservations(
    provider_account,
    start_time,
    end_time,
    *,
    exclude_id: Optional[int] = None,
) -> QuerySet:
    """返回与给定时间窗重叠的预约或尚未结束的预约订单。

    半开区间语义：``[start, end)``，首尾相接不算冲突。

    Args:
        provider_account: 目标陪玩的 ``ClubAccount``。
        start_time / end_time: 待检测的时间窗。
        exclude_id: 需要排除的预约 ID（改期场景排除自己）。

    Returns:
        QuerySet[Reservation]: 冲突的预约集合，按开始时间升序。
    """
    now = timezone.now()
    qs = Reservation.objects.filter(
        start_time__lt=end_time,
        end_time__gt=start_time,
    ).filter(
        (Q(required_provider_count=1, provider_account=provider_account, status=Reservation.Status.PENDING)
         & (Q(confirmation_deadline__gt=now) | Q(confirmation_deadline__isnull=True, end_time__gt=now)))
        | (Q(required_provider_count=1, provider_account=provider_account, status=Reservation.Status.CONFIRMED)
           & (Q(payment_deadline__gt=now) | Q(payment_deadline__isnull=True, end_time__gt=now)))
        | (Q(required_provider_count=2, status=Reservation.Status.PENDING,
             seats__provider_account=provider_account, seats__status='PENDING', seats__confirmation_deadline__gt=now))
        | (Q(required_provider_count=2, status=Reservation.Status.PENDING,
             seats__provider_account=provider_account, seats__status='CONFIRMED', start_time__gt=now))
        | (Q(required_provider_count=2, status=Reservation.Status.CONFIRMED,
             seats__provider_account=provider_account, seats__status='CONFIRMED', payment_deadline__gt=now))
        # An orphaned converted reservation has no verifiable end state.
        # Keep the original slot blocked until its order is reconciled.
        | Q(provider_account=provider_account, status=Reservation.Status.CONVERTED, order__isnull=True)
        | Q(required_provider_count=2, status=Reservation.Status.CONVERTED, order__isnull=True,
            seats__provider_account=provider_account, seats__status='CONFIRMED')
        | Q(status=Reservation.Status.CONVERTED,
            order__status__in=[Order.Status.GRABBED, Order.Status.IN_SERVICE],
            order__providers__provider_account=provider_account,
            order__providers__released_at__isnull=True)
        # Older converted orders may have only the main-provider field.
        | Q(status=Reservation.Status.CONVERTED,
            order__status__in=[Order.Status.GRABBED, Order.Status.IN_SERVICE],
            order__providers__isnull=True, order__provider_account=provider_account)
    ).distinct()
    if exclude_id is not None:
        qs = qs.exclude(id=exclude_id)
    return qs.order_by('start_time', 'id')


def lock_provider_profile(provider_account) -> EscortProfile:
    """锁住陪玩档案行，作为该陪玩「档期」的串行化闸门。

    必须在 ``transaction.atomic`` 内调用。拿不到档案说明这个账户根本不是
    陪玩，直接拒绝比让它插进一条无主预约要好。

    Args:
        provider_account: 目标陪玩的 ``ClubAccount``。

    Returns:
        EscortProfile: 已加行锁的陪玩档案。

    Raises:
        ReservationError: 该账户没有陪玩档案。
    """
    profile = (
        EscortProfile.objects.select_for_update()
        .filter(account=provider_account)
        .first()
    )
    if profile is None:
        raise ReservationError('该陪玩暂不可预约')
    return profile


def validate_reservation_provider_availability(profile):
    """Check present eligibility without applying current schedule or capacity.

    A future reservation reserves a time slot rather than an active-order
    capacity place, but an offline or deposit-ineligible provider cannot be
    invited to it.
    """
    from wallet.refund_deposit import protection_enabled
    if protection_enabled() and profile.account_id:
        from wallet.models import RefundDepositDeficit
        if RefundDepositDeficit.objects.filter(account_id=profile.account_id, remaining__gt=0).exists():
            raise ReservationError('退款押金缺口待补，暂不可预约')
    if profile.deposit_mode != 'EARNINGS' and profile.deposit_paid < profile.effective_deposit_required:
        raise ReservationError('陪玩押金未缴齐')
    if profile.status == EscortProfile.Status.OFFLINE:
        raise ReservationError('陪玩当前处于离线状态')


def validate_reservation_schedule(profile, start_time, end_time):
    """Caller holds the profile lock shared with schedule editing."""
    from users.models import EscortSchedule
    if not EscortSchedule.provider_covers_window(profile.user, start_time, end_time, lock=True):
        raise ReservationError('陪玩设置的接单档期未覆盖预约时段')


def lock_team_conflicts(provider_account, start_time, end_time, *, exclude_id=None):
    """Current locking read after the provider's shared schedule lock.

    This prevents a pre-existing repeatable-read snapshot from missing a team
    seat committed by a previous owner of the same provider lock.
    """
    from .models import ReservationSeat
    query = ReservationSeat.objects.select_for_update().filter(
        provider_account=provider_account, reservation__status__in=ACTIVE_STATUS_LIST,
        reservation__start_time__lt=end_time, reservation__end_time__gt=start_time,
    ).select_related('reservation').order_by('reservation_id', 'number')
    if exclude_id is not None:
        query = query.exclude(reservation_id=exclude_id)
    now = timezone.now()
    for seat in query:
        booking = seat.reservation
        if (booking.status == Reservation.Status.PENDING and
                ((seat.status == ReservationSeat.Status.PENDING and seat.confirmation_deadline > now)
                 or (seat.status == ReservationSeat.Status.CONFIRMED and booking.start_time > now))):
            raise ReservationConflictError('该时间段大神已有预约，请另选时间', conflicting=booking)
        if (booking.status == Reservation.Status.CONFIRMED and seat.status == ReservationSeat.Status.CONFIRMED
                and booking.payment_deadline and booking.payment_deadline > now):
            raise ReservationConflictError('该时间段大神已有预约，请另选时间', conflicting=booking)


def lock_reservation_customer(account):
    """Bind a new booking to the buyer's actual current membership."""
    if not account:
        raise ReservationError('预约老板账号不存在')
    from club_accounts.models import ClubAccount
    locked = ClubAccount.objects.select_for_update().filter(
        pk=account.pk, account_type=ClubAccount.AccountType.BOSS).first()
    if (not locked or not locked.is_active or not locked.can_login
            or not locked.license_membership_active or locked.business_cycle != account.business_cycle):
        raise ReservationError('老板门店身份已变化，请刷新后重新预约')
    return locked


def create_reservation(
    *,
    customer_account,
    customer,
    provider_account,
    service,
    start_time,
    end_time,
    game_rounds: int = 1,
    game_region: str = '',
    game_nickname: str = '',
    game_uid: str = '',
    remark: str = '',
    operator=None,
    operator_account=None,
) -> Reservation:
    """创建一条待确认预约。**冲突检测与插入在同一把档期锁内完成。**

    Args:
        customer_account: 下单老板的 ``ClubAccount``。
        customer: 下单老板的 legacy ``CustomUser``。
        provider_account: 被预约陪玩的 ``ClubAccount``。
        service: ``ServiceItem`` 实例。
        start_time / end_time: 预约时间窗（已通过 :func:`validate_reservation_window`）。
        game_rounds: 局数，用于估价。
        game_region / game_nickname / game_uid: 游戏账号资料快照。
        remark: 备注。
        operator / operator_account: 审计用操作人；默认取下单老板本人。

    Returns:
        Reservation: 新建的预约（``PENDING``）。

    Raises:
        ReservationConflictError: 该时间段已被占用。
        ReservationError: 陪玩不可预约。
    """
    if service.fulfillment_provider_count == 2:
        raise ReservationError('该双陪套餐需联系客服安排两名打手')
    duration_minutes = int((end_time - start_time).total_seconds() // 60)
    rounds = max(int(game_rounds or 1), 1)
    # Reservations must not occupy a slot for a product the normal order
    # flow cannot sell. Reuse its catalog rules without reserving funds.
    if service.service_category_id and service.service_category.is_gift:
        raise ReservationError('礼物请从赠送礼物入口购买')
    from .gameplay import validate_service_gameplay
    from rest_framework.exceptions import ValidationError
    try:
        validate_service_gameplay(service, rounds)
    except ValidationError as exc:
        raise ReservationError(_first_error(exc.detail)) from exc

    with transaction.atomic():
        from django.conf import settings
        if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
            from club_accounts.provider_lifecycle import ProviderLifecycleError, lock_active_provider_operations
            try:
                eligible = lock_active_provider_operations([provider_account])
            except ProviderLifecycleError as exc:
                raise ReservationError('陪玩状态暂不可核验，请稍后重试') from exc
            if not eligible:
                raise ReservationError('陪玩门店身份已变化，请重新选择')
        from .models import ServiceItem
        from .account_fields import lock_and_validate
        locked_service = ServiceItem.objects.select_for_update().get(pk=service.pk)
        if (locked_service.game_category_id != service.game_category_id
                or locked_service.fulfillment_provider_count != service.fulfillment_provider_count
                or locked_service.billing_provider_count != service.billing_provider_count):
            raise ReservationError('服务资料规则已变化，请重新核对')
        try:
            validate_service_gameplay(locked_service, rounds)
            if locked_service.service_category_id and locked_service.service_category.is_gift:
                raise ReservationError('礼物请从赠送礼物入口购买')
            lock_and_validate(locked_service, {'game_region': game_region, 'game_nickname': game_nickname, 'game_uid': game_uid})
        except ValidationError as exc:
            raise ReservationError(_first_error(exc.detail)) from exc
        # 档期锁：并发抢同一时间片时，后到者阻塞在这里，避免「都查到没冲突」
        profile = lock_provider_profile(provider_account)
        provider = _legacy_user_for_account(provider_account)
        if not provider or provider.pk != profile.user_id:
            raise ReservationError('陪玩账号资料尚未关联，请联系客服核对')
        if not provider_account.is_active or not provider_account.can_login or not provider.is_active:
            raise ReservationError('陪玩账号已停用')
        if not profile.is_verified:
            raise ReservationError('陪玩尚未通过认证')
        from django.conf import settings
        if settings.PROVIDER_WECHAT_GATE_ENABLED:
            reason = profile.wechat_acceptance_error()
            if reason:
                raise ReservationError(reason)
        if not profile.can_take_service(locked_service):
            raise ReservationError('陪玩未开通对应端手游、难度或档位的接单资格')
        from users.qualifications import service_skill
        if locked_service.game_category_id and not service_skill(profile, locked_service):
            raise ReservationError('陪玩尚未配置该服务技能')
        validate_reservation_provider_availability(profile)
        validate_reservation_schedule(profile, start_time, end_time)
        # Current-day schedules and active-order capacity do not determine
        # whether a future appointment can reserve its own time slot.

        # This is a current locking read after the provider lock. A plain
        # conflict query can otherwise see an older MySQL repeatable-read
        # snapshot while an earlier reservation has just been confirmed.
        from .reservation_deadlines import due
        lock_team_conflicts(provider_account, start_time, end_time)
        active = Reservation.objects.select_for_update().filter(
            required_provider_count=1, provider_account=provider_account, status__in=ACTIVE_STATUS_LIST,
            start_time__lt=end_time, end_time__gt=start_time).order_by('pk')
        now = timezone.now()
        if any(not due(existing, now=now) for existing in active):
            raise ReservationConflictError('该时间段大神已有预约，请另选时间')
        conflicting = find_conflicting_reservations(
            provider_account, start_time, end_time,
        ).first()
        if conflicting is not None:
            raise ReservationConflictError(
                '该时间段大神已有预约，请另选时间', conflicting=conflicting,
            )

        from .reservation_deadlines import required_rules, confirmation_snapshot
        try:
            rules = required_rules(lock=True)
            duration_minutes = validate_reservation_window(start_time, end_time, rules=rules)
            confirmation_deadline = confirmation_snapshot(start_time, timezone.now(), rules)
        except ValueError as exc:
            raise ReservationError(str(exc)) from exc

        # Match conversion/replacement: take the buyer row after booking and
        # provider locks, and persist only the cycle read while holding it.
        customer_account = lock_reservation_customer(customer_account)
        from .reservation_pricing import HOURLY_UNITS, original_amount
        reservation = Reservation.objects.create(
            customer=customer,
            customer_account=customer_account,
            customer_business_cycle=customer_account.business_cycle if customer_account else None,
            provider=provider,
            provider_account=provider_account,
            provider_business_cycle=provider_account.business_cycle,
            service=service,
            start_time=start_time,
            end_time=end_time,
            duration_minutes=duration_minutes,
            game_rounds=rounds,
            estimated_amount=original_amount(locked_service, rounds,
                duration_minutes if locked_service.price_unit in HOURLY_UNITS else None),
            game_region=game_region,
            game_nickname=game_nickname,
            game_uid=game_uid,
            remark=remark,
            status=Reservation.Status.PENDING,
            confirmation_deadline=confirmation_deadline,
            confirmation_timeout_minutes=rules['confirmation_minutes'],
            confirmation_config_version=rules['version'],
        )
        if (getattr(settings, 'RESERVATION_NOTICE_RECOVERY_ENABLED', False)
                and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)):
            reservation.invitation_notice_intent = {
                'account_id': provider_account.pk, 'business_cycle': provider_account.business_cycle,
                'user_id': provider.pk, 'title': '有新的预约待确认',
                'preview': f'{timezone.localtime(reservation.start_time):%m-%d %H:%M} 起{reservation.duration_minutes} 分钟，请尽快确认',
            }
            reservation.save(update_fields=['invitation_notice_intent'])
            from site_messages.reservation_invite import schedule_single
            schedule_single(reservation.pk)
        log_reservation_event(
            reservation,
            action=ReservationStatusLog.Action.CREATE,
            operator=operator if operator is not None else customer,
            operator_account=(
                operator_account if operator_account is not None else customer_account
            ),
            reason='发起预约',
        )
    return reservation


class _CustomerContext:
    """给 ``CreateOrderSerializer`` 用的最小请求替身。

    转单的付款方永远是**预约的老板**，而不是点「转单」的那个人（客服代客
    转单时两者不同）。直接把真实 ``request`` 传进序列化器会让折扣、优惠券、
    推荐人分佣统统按客服账户算，账就错了。这里只喂它真正用到的两个属性。
    """

    def __init__(self, account, legacy_user):
        self.account = account
        self.legacy_user = legacy_user


def reservation_order_pricing(reservation, discount_selection_token=None, gift_balance_amount=0, compensation_balance_amount=0,
                              user_coupon_id=None):
    """Use the same current service and buyer rules for quote and conversion."""
    if discount_selection_token and user_coupon_id is not None:
        raise ReservationError('请通过优惠组合或优惠券编号选择，不可同时提交')
    from .serializers import CreateOrderSerializer
    provider_ref = (reservation.provider_id if getattr(settings, 'LEGACY_FORCE_AUTH_COMPAT', False)
                    else reservation.provider_account_id)
    payload = {
        'service_id': reservation.service_id,
        'game_rounds': reservation.game_rounds,
        'game_region': reservation.game_region,
        'game_nickname': reservation.game_nickname,
        'game_uid': reservation.game_uid,
        'remark': reservation.remark,
        'gift_balance_amount': gift_balance_amount,
        'compensation_balance_amount': compensation_balance_amount,
    }
    if reservation.service.fulfillment_provider_count != reservation.required_provider_count:
        raise ReservationError('服务所需人数已变化，请联系客服核对')
    confirmations = {}
    if reservation.status == Reservation.Status.CONFIRMED and reservation.required_provider_count == 1:
        confirmations = {reservation.provider_account_id: reservation.confirmed_at}
    if reservation.required_provider_count == 2:
        seats = list(reservation.seats.order_by('number'))
        if (len(seats) != 2 or any(seat.status != 'CONFIRMED' or not seat.provider_account_id for seat in seats)):
            raise ReservationError('双陪尚未全部确认，请刷新预约')
        payload['provider_ids'] = [seat.provider_account_id for seat in seats]
        if reservation.status == Reservation.Status.CONFIRMED:
            confirmations = {seat.provider_account_id: seat.confirmed_at for seat in seats}
    else:
        payload['provider_id'] = provider_ref
    if discount_selection_token:
        payload['discount_selection_token'] = discount_selection_token
    if user_coupon_id is not None:
        payload['user_coupon_id'] = user_coupon_id
    from .reservation_pricing import hourly_minutes
    serializer = CreateOrderSerializer(data=payload, context={'request': _CustomerContext(reservation.customer_account, reservation.customer),
                'reservation_conversion': True, 'reservation_minutes': hourly_minutes(reservation),
                'reservation_confirmations': confirmations})
    if not serializer.is_valid():
        raise ReservationError(_first_error(serializer.errors))
    return serializer.validated_data


def reservation_service_terms(item):
    return {'name': item.name, 'price_unit': item.price_unit,
            'service_type': item.service_type, 'game_category': item.game_category_id,
            'billing_provider_count': item.billing_provider_count, 'fulfillment_provider_count': item.fulfillment_provider_count, 'unit_price': item.price,
            'gameplay': item.gameplay_id, 'platform': item.platform,
            'security_level': item.security_level, 'provider_kind': item.provider_kind,
            'provider_grade': item.provider_grade, 'required_level': item.required_level_id,
            'service_category': item.service_category_id, 'purchase_mode': item.purchase_mode,
            'is_addon': item.is_addon, 'experience_kind': item.experience_kind,
            'allows_discount_coupons': item.allows_discount_coupons}


def convert_reservation_to_order(
    request,
    reservation_id: int,
    *,
    client_request_id: Optional[str] = None,
    expected_amount: Optional[int] = None,
    expected_service: Optional[dict] = None,
    gift_balance_amount: int = 0,
    compensation_balance_amount: int = 0,
    boss_confirmation: str = '',
    game_fields: Optional[dict] = None,
    discount_selection_token: Optional[str] = None,
    points_checkout_test_token: Optional[str] = None,
    user_coupon_id: Optional[int] = None,
) -> Tuple[int, str, Optional[Order]]:
    """把已确认的预约转成正式订单：预约链路上**唯一动钱**的一步。

    复用下单主路径的全部构件 —— ``CreateOrderSerializer`` 定价校验、
    ``compute_split`` 分账、``get_wallet`` 扣款、``transition`` 派单 ——
    保证转单产生的订单与老板自己下的单在账务上完全同构。

    幂等键**只占一次**（``scope='reservation.convert'``）：转单不再走
    ``/orders/create/`` 接口，因此不存在两处各占一次键的问题。

    Args:
        request: DRF 请求，提供操作人身份（``account`` / ``legacy_user``）。
        reservation_id: 预约 ID。
        client_request_id: 客户端幂等键，可为空（走降级窗口）。

    Returns:
        tuple[int, str, Order | None]: ``(业务码, 提示, 订单)``；
        业务码取 0 / 400 / 403 / 404 / 409，非 0 时订单为 None。
    """
    trace_id = new_trace_id()

    # 只读探测重放：首次转单成功后预约已是 CONVERTED，重放会先撞
    # 「仅已确认的预约可转订单」，把真实原因（上一次已经转成功了）盖掉。
    replayed, replay_reason = peek_fund_write(
        account=request.account, request_id=client_request_id,
    )
    if replayed:
        log_money_event(
            'reservation.convert.duplicate',
            account_id=request.account.pk,
            request_id=client_request_id or '',
            trace_id=trace_id,
            idempotent_hit=True,
            reason=f'reservation_id={reservation_id}｜{replay_reason}',
        )
        return 409, replay_reason, None

    # Initialize the shared mutex before taking a reservation row lock. Two
    # first-time conversions can otherwise deadlock while each attempts to
    # insert the same provider's missing mutex from inside its transaction.
    booking_info = Reservation.objects.filter(pk=reservation_id).values('provider_account_id', 'required_provider_count', 'service_id').first()
    provider_account_ids = ([booking_info['provider_account_id']] if booking_info else [])
    if booking_info and booking_info['required_provider_count'] == 2:
        from .models import ReservationSeat
        provider_account_ids = list(ReservationSeat.objects.filter(reservation_id=reservation_id,
            status=ReservationSeat.Status.CONFIRMED).order_by('number').values_list('provider_account_id', flat=True))
    lifecycle_accounts = []
    referral_lifecycle = None
    booking_buyer_id = None
    if booking_info and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
        from club_accounts.provider_lifecycle import ProviderLifecycleError
        from .referral_cycle import capture
        booking_identity = Reservation.objects.select_related('customer_account', 'customer').get(pk=reservation_id)
        booking_buyer_id = booking_identity.customer_account_id
        if not booking_buyer_id:
            return 409, '预约老板账号归属待核对', None
        try:
            referral_lifecycle = capture(booking_identity.customer_account, booking_identity.customer, provider_account_ids)
            lifecycle_accounts = referral_lifecycle[2]
        except (ProviderLifecycleError, ValueError):
            return 503, '预约陪玩账号状态暂不可核验，请稍后重试', None
    ensure_provider_dispatch_locks(provider_account_ids)

    from contextlib import ExitStack
    with transaction.atomic(), ExitStack() as scopes:
        if lifecycle_accounts:
            from club_accounts.provider_lifecycle import lock_provider_operations, provider_membership_current
            try:
                lock_provider_operations(lifecycle_accounts)
                if not all(provider_membership_current(account) for account in lifecycle_accounts
                           if account.pk in provider_account_ids):
                    return 409, '预约陪玩账号状态已变化，请刷新后重试', None
            except ProviderLifecycleError:
                return 503, '预约陪玩账号状态暂不可核验，请稍后重试', None
        if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
            from console.payroll_event_barrier import lock_payment_refund_events
            lock_payment_refund_events()
        # Reservation creation and replacement take service before the
        # provider and reservation rows; use the same order when paying.
        from .models import ServiceItem
        if booking_info is None:
            return 404, '预约不存在', None
        ServiceItem.objects.select_for_update().get(pk=booking_info['service_id'])
        # Decisions bind the invitation's original membership cycle. Take the
        # same account-before-profile order as confirmation before spending.
        locked_providers = {item.pk: item for item in ClubAccount.objects.select_for_update().filter(
            pk__in=provider_account_ids, account_type=ClubAccount.AccountType.PROVIDER).order_by('pk')}
        if len(locked_providers) != len(set(provider_account_ids)):
            return 409, '预约陪玩账号已变化，请重新预约', None
        # Match ordinary dispatch (dispatch mutex before profile) and booking
        # confirmation (profile before reservation); lock stable account order.
        from .services import lock_provider_dispatch
        lock_provider_dispatch(provider_account_ids)
        from users.models import EscortProfile
        for account_id in sorted(set(provider_account_ids)):
            if not EscortProfile.objects.select_for_update().filter(account_id=account_id).exists():
                return 409, '预约陪玩资料已变化，请刷新后重试', None
        try:
            reservation = (
                Reservation.objects.select_for_update()
                .get(id=reservation_id)
            )
        except Reservation.DoesNotExist:
            return 404, '预约不存在', None
        if reservation.service_id != booking_info['service_id']:
            return 409, '预约服务已变化，请刷新后重试', None
        if request.account.account_type == ClubAccount.AccountType.BOSS:
            buyer = ClubAccount.objects.select_for_update().filter(pk=request.account.pk).first()
            if (not buyer or not buyer.is_active or not buyer.can_login or not buyer.license_membership_active
                    or buyer.business_cycle != request.account.business_cycle
                    or reservation.customer_account_id != buyer.pk
                    or reservation.customer_business_cycle != buyer.business_cycle):
                return 409, '预约属于此前入店周期，请重新预约', None
        if referral_lifecycle is not None:
            if reservation.customer_account_id != booking_buyer_id:
                return 409, '预约老板已变化，请刷新后重试', None
            from .referral_cycle import validate
            buyer_locked = ClubAccount.objects.select_for_update().get(pk=booking_buyer_id)
            providers = [account for account in lifecycle_accounts if account.pk in provider_account_ids]
            try:
                validate(referral_lifecycle, buyer_locked, reservation.customer, providers)
            except ValueError:
                return 409, '预约推荐人或陪玩账号已变化，请刷新后重试', None

        from .reservation_expiration import expire_locked_reservation
        if reservation.required_provider_count == 2 and reservation.status == Reservation.Status.PENDING:
            return 409, '双陪尚未全部确认，请刷新预约', None
        if expire_locked_reservation(reservation):
            return 409, '预约已过期，请重新预约', None
        if reservation.status != Reservation.Status.CONFIRMED:
            return 409, '仅已确认的预约可转订单', None
        if reservation.required_provider_count == 1 and provider_account_ids != [reservation.provider_account_id]:
            return 409, '预约陪玩状态已变化，请刷新后重试', None
        if (reservation.required_provider_count == 1 and
                reservation.provider_business_cycle != locked_providers[reservation.provider_account_id].business_cycle):
            return 409, '预约确认属于此前入店周期，请重新预约', None
        if reservation.provider_id is None:
            return 400, '该预约缺少陪玩绑定，无法转单', None
        if reservation.required_provider_count == 2:
            confirmed_seats = list(reservation.seats.select_for_update().filter(status='CONFIRMED')
                                   .order_by('number').values_list('provider_account_id', 'provider_business_cycle'))
            current_ids = [account_id for account_id, _ in confirmed_seats]
            if len(current_ids) != 2 or current_ids != provider_account_ids or len(set(current_ids)) != 2:
                return 409, '预约陪玩状态已变化，请刷新后重试', None
            if any(cycle != locked_providers[account_id].business_cycle for account_id, cycle in confirmed_seats):
                return 409, '预约确认属于此前入店周期，请重新预约', None

        buyer_account = reservation.customer_account
        buyer_user = reservation.customer
        if buyer_account is None:
            return 403, '该预约未绑定业务账户', None

        # 定价与资格校验完全复用下单序列化器：陪玩是否可接单、是否在档期、
        # 等级是否够、活动/折扣/抽成率怎么算，一处维护。
        # provider_id 的取值口径与 C 端下单一致：兼容模式下传 legacy 用户 ID，
        # 生产模式下传 ClubAccount ID。
        # Hold the service row until the price check and debit complete.
        locked_service = ServiceItem.objects.select_for_update().get(pk=reservation.service_id)
        from .account_fields import FIELDS, lock_and_validate
        for field in FIELDS:
            if field in (game_fields or {}):
                setattr(reservation, field, game_fields[field])
        try:
            lock_and_validate(locked_service, {field: getattr(reservation, field) for field in FIELDS})
        except serializers.ValidationError as exc:
            return 400, _first_error(exc.detail), None
        # Compete for the same provider capacity as ordinary orders and repeat
        # validation while holding that lock. Lock only the reservation above:
        # locking joined account rows here reverses the ordinary dispatch order.
        try:
            data = reservation_order_pricing(reservation, discount_selection_token, gift_balance_amount,
                                             compensation_balance_amount, user_coupon_id=user_coupon_id)
        except ReservationError as exc:
            return 400, str(exc), None
        points_quote = None
        if points_checkout_test_token:
            if getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True:
                from .points_business_access import checkout, checkout_shape_allowed
                if not checkout_shape_allowed(data):
                    raise serializers.ValidationError('预约积分付款的陪玩或服务条件已变化，请重新确认')
                scopes.enter_context(checkout(account=buyer_account, service=locked_service))
            from .reservation_points_test import verify
            points_quote, points_receipt_token = verify(buyer_account, reservation, data, points_checkout_test_token)
        personal_game_snapshots = data.get('personal_game_snapshots')
        from .delta_personal_commission import mixed_funding_available, points_funding_available
        mixed_unavailable = bool((gift_balance_amount or compensation_balance_amount) and not mixed_funding_available())
        from .points_business_access import permits as points_permits
        formal_personal_points = bool(points_quote and personal_game_snapshots is not None
            and points_permits(account=buyer_account, service=locked_service, phase='checkout', kind='service'))
        points_unavailable = bool(points_quote and not (points_funding_available() or formal_personal_points))
        if personal_game_snapshots is not None and (points_unavailable or mixed_unavailable or data['amount'] <= 0):
            return 400, '三角洲本人抽成当前仅支持指定陪玩的普通店币付款', None
        from .paid_service_scope import capture as capture_paid_scope, verify as verify_paid_scope, record as record_paid_scope
        paid_scope = capture_paid_scope(data['service'])
        try:
            verify_paid_scope(reservation.service_id, paid_scope)
        except ValueError as exc:
            return 409, str(exc), None
        if getattr(settings, 'GAME_COMMISSION_PAYMENT_ENABLED', False):
            from .game_commission import lock_and_verify_payment_snapshot
            try:
                if personal_game_snapshots is not None:
                    locked_profiles = {p.account_id: p for p in EscortProfile.objects.filter(
                        account_id__in=provider_account_ids)}
                    for account, expected in zip(data['provider_accounts'], personal_game_snapshots):
                        lock_and_verify_payment_snapshot(reservation.service_id, expected,
                            provider=locked_profiles[account.pk])
                    from .delta_personal_commission import verify_payment_shares
                    specs = (data['provider_specs'] if len(data['provider_accounts']) == 2
                             else [data['commission_spec']])
                    verify_payment_shares(data['provider_accounts'], personal_game_snapshots, specs)
                else:
                    lock_and_verify_payment_snapshot(reservation.service_id,
                        data['commission_snapshot']['game_commission_snapshot'])
                    from .game_commission import verify_payment_shares
                    specs = (data['provider_specs'] if len(data['provider_accounts']) == 2
                             else [data['commission_spec']] if data['provider_accounts'] else [])
                    verify_payment_shares(data['commission_snapshot']['game_commission_snapshot'], specs)
            except ValueError as exc:
                return 409, str(exc), None
        if formal_personal_points:
            from .delta_personal_commission import verify_formal_points_checkout
            verify_formal_points_checkout(data, account=buyer_account, service=locked_service)
        amount = points_quote['calculation']['payable_units'] if points_quote else data['amount']
        safe_box_subsidy_basis = 0
        if getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False) and not points_quote:
            from .purchase_benefits import vip_safe_box_subsidy_basis
            safe_box_subsidy_basis = vip_safe_box_subsidy_basis(
                buyer_account, data['service'], data['game_rounds'], data['user_coupon'], data['pricing'])
        if personal_game_snapshots is not None and amount <= 0 and not safe_box_subsidy_basis:
            return 400, '三角洲本人抽成暂不支持零实付订单', None
        terms = reservation_service_terms(data['service'])
        if expected_service is not None and any(terms[key] != value for key, value in expected_service.items()):
            return 409, '商品信息已变化，请刷新报价后重新确认', None
        if expected_amount is not None and expected_amount != amount:
            return 409, '价格或优惠已变化，请刷新报价后重新确认', None
        provider = data['provider']
        split = compute_split(amount, inviter_commission_rate=data['inviter_commission_rate'], **data['commission_spec'])
        selected_shares = None
        if reservation.required_provider_count == 2:
            from .settlement import build_provider_shares, aggregate_order_split
            specs = data['provider_specs']
            if points_quote and data['commission_snapshot'].get('commission_type') == 'FIXED':
                from .commission import fixed_specs
                specs = fixed_specs(amount, 2, data['commission_snapshot']['commission_fixed'])
            selected_shares = build_provider_shares(amount, specs)
            split = aggregate_order_split(amount, selected_shares, data['inviter_commission_rate'])
        inviter = data.get('inviter')
        inviter_account = data['inviter_account']

        wallet = get_wallet(
            account=buyer_account, user=buyer_user, for_update=True,
        )
        if not wallet.is_active:
            return 403, '钱包不可用', None
        if wallet.balance < amount:
            return 400, f'{current_currency_name()}不足，请联系企业微信客服充值', None
        from engagement.funding_wallet import funding_quote
        funding = funding_quote(wallet, amount, gift_balance_amount, compensation_balance_amount)
        if not funding['balance_sufficient']:
            return 400, '所选资金来源余额不足，请重新核对', None

        from .purchase_benefits import lock_purchase, record_paid_benefits
        from .reservation_pricing import hourly_minutes
        reservation_minutes = hourly_minutes(reservation)
        if discount_selection_token:
            from .discount_selection import lock_selected_discount
            args = (buyer_account, data['service'], reservation.game_rounds, discount_selection_token)
            selected, locked_promotion, locked_coupon = lock_selected_discount(
                *args, *([reservation_minutes] if reservation_minutes is not None else []))
            pricing = data['pricing']
            if ((selected.original_amount, selected.boss_discount, selected.promo_discount,
                 selected.coupon_discount, selected.amount) != (pricing.original_amount, pricing.boss_discount,
                 pricing.promo_discount, pricing.coupon_discount, pricing.amount)
                    or getattr(locked_promotion, 'pk', None) != getattr(data['promotion'], 'pk', None)):
                raise serializers.ValidationError('优惠条件已变化，请重新报价')
        else:
            args = (buyer_account, data['service'], reservation.game_rounds, data['user_coupon'], data['pricing'])
            locked_coupon = lock_purchase(*args, *([reservation_minutes] if reservation_minutes is not None else []),
                                          expected_promotion=data['promotion'])
        if safe_box_subsidy_basis and vip_safe_box_subsidy_basis(
                buyer_account, data['service'], data['game_rounds'], locked_coupon,
                data['pricing']) != safe_box_subsidy_basis:
            raise serializers.ValidationError('安全箱券或补贴条件已变化，请重新报价')

        # 校验全过、即将扣款，此刻才占幂等键：校验失败不占键，用户补足余额后
        # 可以拿同一个键重试。
        if expire_locked_reservation(reservation):
            return 409, '预约已过期，请重新预约', None
        claim_savepoint = transaction.savepoint()
        allowed, reason = claim_fund_write(
            account=request.account,
            request_id=client_request_id,
            scope='reservation.convert',
            fingerprint=(f'{reservation.id}|{amount}|gift={gift_balance_amount}'
                         f'|support={request.data.get("support_contact_id") or ""}'
                         f'|compensation={compensation_balance_amount}|confirmation={boss_confirmation}'
                         f'|game_fields={hashlib.sha256(json.dumps(game_fields or {}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}'
                         f'|discount={data.get("discount_option_key", "legacy")}'
                         + (f'|coupon={user_coupon_id}' if user_coupon_id is not None else '')
                         + (f'|points={points_quote["calculation"]["points_hundredths"]}'
                            f'|points_discount={points_quote["calculation"]["discount_units"]}' if points_quote else '')),
        )
        if not allowed:
            transaction.savepoint_commit(claim_savepoint)
            log_money_event(
                'reservation.convert.duplicate',
                account_id=request.account.pk,
                request_id=client_request_id or '',
                amount=amount,
                trace_id=trace_id,
                idempotent_hit=True,
                reason=f'{reservation.reservation_no}｜{reason}',
            )
            return 409, reason, None

        # Locks and pricing can take time; never debit after the booked end.
        from .reservation_deadlines import due
        if due(reservation, now=timezone.now()):
            transaction.savepoint_rollback(claim_savepoint)
            expire_locked_reservation(reservation)
            return 409, '预约已过期，请重新预约', None
        transaction.savepoint_commit(claim_savepoint)
        balance_before = wallet.balance

        from support.assignment import selected_card
        support_contact = selected_card(request.data.get('support_contact_id'), lock=True)
        order = Order.objects.create(
            customer=buyer_user,
            customer_account=buyer_account,
            provider=None,
            service=data['service'],
            support_contact=support_contact,
            responsible_staff_id=support_contact.staff_account_id if support_contact else None,
            escort_mode=Order.EscortMode.DOUBLE if selected_shares else Order.EscortMode.SINGLE,
            requires_boss_selection=bool(selected_shares),
            amount=amount,
            game_rounds=data['game_rounds'],
            remark=reservation.remark,
            game_region=reservation.game_region,
            game_nickname=reservation.game_nickname,
            game_uid=reservation.game_uid,
            status=Order.Status.PENDING,
            payment_status=Order.PaymentStatus.PAID,
            # 预约已经把陪玩钉死了，不进抢单池，也就没有抢单超时
            auto_cancel_at=None,
            original_amount=data['original_amount'],
            boss_discount=data['boss_discount'],
            promo_discount=data['promo_discount'],
            coupon_discount=data['coupon_discount'],
            promotion=data.get('promotion'),
            **data['commission_snapshot'],
            commission_rate=split.commission_rate,
            provider_income=split.provider_income,
            inviter=inviter,
            inviter_account=inviter_account,
            inviter_commission=split.inviter_commission,
            shop_income=split.shop_income,
        )
        if referral_lifecycle is not None:
            from .referral_cycle import record
            record(order, referral_lifecycle)
        record_paid_scope(order, paid_scope)
        from .original_attribution import payment_created
        if not points_quote:
            payment_created(order)
        if getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False):
            from .purchase_benefits import vip_safe_box_subsidy_basis
            basis = vip_safe_box_subsidy_basis(
                buyer_account, data['service'], data['game_rounds'], locked_coupon, data['pricing'])
            if basis:
                from .models import OrderPlatformSubsidy
                from .segment_financial_capture import register_subsidy
                subsidy = OrderPlatformSubsidy.objects.create(
                    order=order, coupon_id_snapshot=locked_coupon.pk, basis=basis)
                register_subsidy(order, subsidy)
        record_paid_benefits(order, locked_coupon)
        if boss_confirmation:
            order.funding_confirmation = {'actor_account_id': request.account.pk,
                'confirmation': boss_confirmation, 'recorded_at': timezone.now().isoformat()}
            order.save(update_fields=['funding_confirmation'])
        from engagement.service_funding_route import use_order_funding_allocation
        funding_allocated = use_order_funding_allocation(amount=amount,
                funding_selection_enabled=funding['funding_selection_enabled'])
        if funding_allocated:
            from engagement.funding_wallet import allocate_locked_order_funding, snapshot_service_funding_terms
            allocate_locked_order_funding(order, wallet, gift=gift_balance_amount, compensation=compensation_balance_amount,
                                          points_checkout=bool(points_quote))
        else:
            from engagement.services import allocate_order_points
            allocate_order_points(order, wallet, balance_before)
        wallet.balance -= amount
        wallet.save(update_fields=['balance'])
        from wallet.notifier import notify_wallet_update
        transaction.on_commit(lambda: notify_wallet_update(wallet))
        payment = Transaction.objects.create(
            wallet=wallet,
            order=order,
            amount=-amount,
            tx_type=Transaction.TxType.PAY,
            balance_before=balance_before,
            balance_after=wallet.balance,
            remark=f'预约转单支付：{order.order_no}',
        )
        if funding_allocated:
            snapshot_service_funding_terms(order, paid_at=payment.created_at)
        if points_quote:
            from .points_service_payment import record_locked
            record_locked(order_id=order.pk, payment_id=payment.pk, token=points_receipt_token)
            order.refresh_from_db()
            payment_created(order)
        log_money_event(
            'reservation.convert',
            account_id=buyer_account.pk,
            user_id=getattr(buyer_user, 'pk', None),
            request_id=client_request_id or '',
            order_no=order.order_no,
            order_id=order.pk,
            tx_type=Transaction.TxType.PAY,
            amount=-amount,
            balance_before=balance_before,
            balance_after=wallet.balance,
            trace_id=trace_id,
            operator_id=getattr(request.account, 'pk', None),
            reason=f'预约转单：{reservation.reservation_no}',
        )

        log_only(
            order,
            action=OrderStatusLog.Action.CREATE,
            operator=request.legacy_user,
            operator_account=request.account,
            reason=f'预约转单：{reservation.reservation_no}',
        )

        provider_account = reservation.provider_account

        def assign_reserved(o: Order) -> None:
            o.provider = provider
            o.provider_account = provider_account
            profile = getattr(provider, 'escort_profile', None)
            o.provider_name_snapshot = (
                (profile.display_name if profile else '')
                or provider.nickname
                or provider.username
            )
            if selected_shares:
                from .models import OrderProvider
                for index, (selected, selected_account, share) in enumerate(zip(data['providers'], data['provider_accounts'], selected_shares)):
                    OrderProvider.objects.create(order=o, provider=selected, provider_account=selected_account,
                        provider_business_cycle=selected_account.business_cycle,
                        provider_name_snapshot=selected.escort_profile.display_name or selected.nickname or selected.username,
                        settlement_base=share.settlement_base, commission_type=share.commission_type,
                        commission_rate=share.commission_rate,
                        game_commission_snapshot=(personal_game_snapshots[index] if personal_game_snapshots is not None else o.game_commission_snapshot),
                        commission_fixed=share.commission_fixed, provider_income=share.provider_income)
            else:
                from .services import sync_single_provider_share
                sync_single_provider_share(o)

        order = transition(
            order.id,
            Order.Status.GRABBED,
            operator=request.legacy_user,
            operator_account=request.account,
            action=OrderStatusLog.Action.ASSIGN,
            reason='预约转单指定陪玩',
            side_effect=assign_reserved,
            update_fields=[
                'provider', 'provider_account', 'provider_name_snapshot',
            ],
        )

        def bind_order(r: Reservation) -> None:
            r.order = order

        if game_fields:
            reservation.save(update_fields=[field for field in FIELDS if field in game_fields])
        transition_reservation(
            reservation.id,
            Reservation.Status.CONVERTED,
            operator=request.legacy_user,
            operator_account=request.account,
            reason=f'转为订单：{order.order_no}',
            side_effect=bind_order,
            update_fields=['order'],
        )

    return 0, '预约转单成功', order


def _account_for_legacy_user(user):
    """legacy ``CustomUser`` -> ``ClubAccount``；无映射返回 None。"""
    if user is None:
        return None
    mapping = LegacyAccountMap.objects.select_related('account').filter(
        legacy_user_id=user.id,
    ).first()
    return mapping.account if mapping else None


def _first_error(errors) -> str:
    """把 DRF 的嵌套错误结构压成一句人话。"""
    if isinstance(errors, dict):
        return _first_error(next(iter(errors.values())))
    if isinstance(errors, list) and errors:
        return _first_error(errors[0])
    return str(errors)
