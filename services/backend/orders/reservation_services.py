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
from typing import Optional, Tuple

from django.conf import settings
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

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
from .services import mark_escort_busy
from .settlement import compute_split
from .state_machine import log_only, transition

# 预约时间粒度：所有起止时间必须对齐到该刻度（分钟）
SLOT_MINUTES = 15
# 单次预约的最短时长（分钟）：低于半小时的档期没有履约价值，徒增碎片
MIN_DURATION_MINUTES = 30
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


def validate_reservation_window(start_time, end_time, *, now=None) -> int:
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
    if start_time is None or end_time is None:
        raise ReservationWindowError('请选择预约开始与结束时间')
    if end_time <= start_time:
        raise ReservationWindowError('预约结束时间必须晚于开始时间')

    for label, value in (('开始', start_time), ('结束', end_time)):
        if value.second or value.microsecond or value.minute % SLOT_MINUTES:
            raise ReservationWindowError(
                f'预约{label}时间必须对齐到 {SLOT_MINUTES} 分钟刻度'
            )

    now = now or timezone.now()
    if start_time <= now:
        raise ReservationWindowError('预约开始时间必须晚于当前时间')

    duration_minutes = int((end_time - start_time).total_seconds() // 60)
    if duration_minutes < MIN_DURATION_MINUTES:
        raise ReservationWindowError(
            f'单次预约时长不得少于 {MIN_DURATION_MINUTES} 分钟'
        )
    if duration_minutes > MAX_DURATION_HOURS * 60:
        raise ReservationWindowError(
            f'单次预约时长不得超过 {MAX_DURATION_HOURS} 小时'
        )

    if start_time > now + timedelta(days=MAX_ADVANCE_DAYS):
        raise ReservationWindowError(f'最多只能预约 {MAX_ADVANCE_DAYS} 天内的档期')

    return duration_minutes


def find_conflicting_reservations(
    provider_account,
    start_time,
    end_time,
    *,
    exclude_id: Optional[int] = None,
) -> QuerySet:
    """返回与给定时间窗重叠的占用态预约。

    半开区间语义：``[start, end)``，首尾相接不算冲突。

    Args:
        provider_account: 目标陪玩的 ``ClubAccount``。
        start_time / end_time: 待检测的时间窗。
        exclude_id: 需要排除的预约 ID（改期场景排除自己）。

    Returns:
        QuerySet[Reservation]: 冲突的预约集合，按开始时间升序。
    """
    qs = Reservation.objects.filter(
        provider_account=provider_account,
        status__in=ACTIVE_STATUS_LIST,
        start_time__lt=end_time,
        end_time__gt=start_time,
    )
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
    duration_minutes = int((end_time - start_time).total_seconds() // 60)
    rounds = max(int(game_rounds or 1), 1)

    with transaction.atomic():
        # 档期锁：并发抢同一时间片时，后到者阻塞在这里，避免「都查到没冲突」
        lock_provider_profile(provider_account)

        conflicting = find_conflicting_reservations(
            provider_account, start_time, end_time,
        ).first()
        if conflicting is not None:
            raise ReservationConflictError(
                '该时间段大神已有预约，请另选时间', conflicting=conflicting,
            )

        reservation = Reservation.objects.create(
            customer=customer,
            customer_account=customer_account,
            provider=_legacy_user_for_account(provider_account),
            provider_account=provider_account,
            service=service,
            start_time=start_time,
            end_time=end_time,
            duration_minutes=duration_minutes,
            game_rounds=rounds,
            estimated_amount=service.price * rounds,
            game_region=game_region,
            game_nickname=game_nickname,
            game_uid=game_uid,
            remark=remark,
            status=Reservation.Status.PENDING,
        )
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


def convert_reservation_to_order(
    request,
    reservation_id: int,
    *,
    client_request_id: Optional[str] = None,
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
    from .serializers import CreateOrderSerializer

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

    with transaction.atomic():
        try:
            reservation = (
                Reservation.objects.select_for_update()
                .select_related('customer', 'customer_account', 'provider',
                                'provider_account', 'service')
                .get(id=reservation_id)
            )
        except Reservation.DoesNotExist:
            return 404, '预约不存在', None

        if reservation.status != Reservation.Status.CONFIRMED:
            return 409, '仅已确认的预约可转订单', None
        if reservation.provider_id is None:
            return 400, '该预约缺少陪玩绑定，无法转单', None

        buyer_account = reservation.customer_account
        buyer_user = reservation.customer
        if buyer_account is None:
            return 403, '该预约未绑定业务账户', None

        # 定价与资格校验完全复用下单序列化器：陪玩是否可接单、是否在档期、
        # 等级是否够、活动/折扣/抽成率怎么算，一处维护。
        # provider_id 的取值口径与 C 端下单一致：兼容模式下传 legacy 用户 ID，
        # 生产模式下传 ClubAccount ID。
        provider_ref = (
            reservation.provider_id
            if getattr(settings, 'LEGACY_FORCE_AUTH_COMPAT', False)
            else reservation.provider_account_id
        )
        serializer = CreateOrderSerializer(
            data={
                'service_id': reservation.service_id,
                'provider_id': provider_ref,
                'game_rounds': reservation.game_rounds,
                'game_region': reservation.game_region,
                'game_nickname': reservation.game_nickname,
                'game_uid': reservation.game_uid,
                'remark': reservation.remark,
            },
            context={'request': _CustomerContext(buyer_account, buyer_user)},
        )
        if not serializer.is_valid():
            return 400, _first_error(serializer.errors), None

        data = serializer.validated_data
        amount = data['amount']
        provider = data['provider']
        split = compute_split(
            amount, data['commission_rate'], data['inviter_commission_rate'],
        )
        inviter = data.get('inviter')
        inviter_account = _account_for_legacy_user(inviter)

        wallet = get_wallet(
            account=buyer_account, user=buyer_user, for_update=True,
        )
        if not wallet.is_active:
            return 403, '钱包不可用', None
        if wallet.balance < amount:
            return 400, '兴安币不足，请联系企业微信客服充值', None

        # 校验全过、即将扣款，此刻才占幂等键：校验失败不占键，用户补足余额后
        # 可以拿同一个键重试。
        allowed, reason = claim_fund_write(
            account=request.account,
            request_id=client_request_id,
            scope='reservation.convert',
            fingerprint=f'{reservation.id}|{amount}',
        )
        if not allowed:
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

        balance_before = wallet.balance
        wallet.balance -= amount
        wallet.save(update_fields=['balance'])

        order = Order.objects.create(
            customer=buyer_user,
            customer_account=buyer_account,
            provider=None,
            service=data['service'],
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
            commission_rate=split.commission_rate,
            provider_income=split.provider_income,
            inviter=inviter,
            inviter_account=inviter_account,
            inviter_commission=split.inviter_commission,
            shop_income=split.shop_income,
        )
        Transaction.objects.create(
            wallet=wallet,
            order=order,
            amount=-amount,
            tx_type=Transaction.TxType.PAY,
            balance_before=balance_before,
            balance_after=wallet.balance,
            remark=f'预约转单支付：{order.order_no}',
        )
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
            mark_escort_busy(
                provider_ids=[provider.id],
                account_ids=[getattr(provider_account, 'pk', None)],
            )

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
