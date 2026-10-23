"""订单领域服务：结算、退款、陪玩状态维护等**唯一**实现。

为什么要有这个模块
------------------
在此之前，「把订单的钱打出去」这段逻辑在两个地方各写了一遍：

* ``orders/views.py::CompleteOrderView``   —— 陪玩自己点「完成订单」
* ``console/views.py::OrderViewSet.complete`` —— 客服在后台点「完成结算」

两份代码算的是同一笔钱，却各自演化：后台那份加了凭证闸门
（``_require_order_evidence``），顾客端那份没有；顾客端无明细订单回落到
「当前登录用户」，后台回落到 ``order.provider_id``。任何一次只改一边的修补，
都会让两条入口的账算得不一样 —— 而资金逻辑出现两套口径，就等于没有口径。

本模块把结算收敛成一个 ``settle_order``，两个入口都必须调它。同理，
陪玩状态与完成计数的维护收敛为 ``refresh_escort_status``。

调用约定
--------
* ``settle_order`` / ``refund_to_customer`` **必须**在
  ``transaction.atomic`` 内、且订单行已 ``select_for_update`` 的上下文中调用；
  ``state_machine.transition`` 的 ``side_effect`` 回调天然满足这两点。
* 金额一律是内部账务单位整数（10 = 1 兴安币），本模块不做任何 ÷10 换算。
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from common.logging_utils import log_money_event, new_trace_id
from users.models import EscortProfile
from wallet.models import ProviderReport, Transaction, get_platform_wallet
from wallet.notifier import notify_wallet_update
from wallet.services import get_wallet

from .models import Order, OrderProvider, ProviderDispatchLock
from .settlement import SettlementError, assert_order_conserved

try:  # site_messages 是可选模块，缺失时消息推送降级为 no-op
    from site_messages.utils import create_message
except ImportError:  # pragma: no cover - 仅在裁剪部署时命中
    create_message = None

# PENDING 订单超时自动取消时长（分钟）。
# 定义在此处而非 views：console 也要用它，让后台去 import 顾客端视图模块
# 会把两个 app 焊死在一起（并且很容易绕出循环导入）。
PENDING_TIMEOUT_MINUTES = 30

# 「占用陪玩产能」的订单状态：已接单 + 服务中。
ACTIVE_ORDER_STATUSES = (Order.Status.GRABBED, Order.Status.IN_SERVICE)


def lock_provider_dispatch(account_ids):
    """事务内锁住本次占用的打手名额，直到订单写入提交。

    各入口必须在计数和扣款前调用。多打手按账号 ID 排序，防止双陪交叉派单
    反向等待。使用独立互斥行，避免与结算中钱包/资料行的锁顺序形成环。
    已有订单先锁订单再调用；新建订单在建单前调用。释放名额无需取此锁。
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('派单名额锁必须在业务事务内获取')
    for account_id in sorted({int(pk) for pk in account_ids if pk}):
        ProviderDispatchLock.objects.get_or_create(account_id=account_id)
        ProviderDispatchLock.objects.select_for_update().get(account_id=account_id)


def sync_single_provider_share(order):
    """持有订单锁时，绑定本轮单陪的完整份额；拒单后重接也复用冻结价格。"""
    if order.escort_mode != Order.EscortMode.SINGLE or not order.provider_id:
        return
    previous = order.providers.first()
    club_share = None
    if order.commission_policy != 'LEGACY' or order.commission_type == 'FIXED' or order.commission_override:
        from .pricing import club_provider_specs
        from .settlement import build_provider_shares, aggregate_order_split
        profile = EscortProfile.objects.select_related('level').get(user_id=order.provider_id)
        club_share = build_provider_shares(order.amount, club_provider_specs(order, [profile], order.amount))[0]
        # 未开始服务的指派/重接以本轮陪玩等级生成快照，开始后固定。
        if order.in_service_at and previous and previous.provider_id == order.provider_id:
            return
        inviter_rate = order.inviter_commission_rate_snapshot if order.inviter_id else 0
        split = aggregate_order_split(order.amount, [club_share], inviter_rate)
        order.commission_rate = split.commission_rate
        order.provider_income = split.provider_income
        order.inviter_commission = split.inviter_commission
        order.shop_income = split.shop_income
        order.save(update_fields=['commission_rate', 'provider_income', 'inviter_commission', 'shop_income'])
    # 兼容旧版本拒单后未释放的参与明细；未开工的回池单没有历史履约收益。
    if not order.in_service_at:
        order.providers.exclude(provider_id=order.provider_id).filter(settled_at__isnull=True).update(
            settlement_base=0, provider_income=0, commission_fixed=0, released_at=timezone.now(),
        )
    OrderProvider.objects.update_or_create(
        order=order, provider_id=order.provider_id,
        defaults={
            'provider_account_id': order.provider_account_id,
            'provider_name_snapshot': order.provider_name_snapshot,
            'settlement_base': order.amount,
            'commission_type': club_share.commission_type if club_share else (previous.commission_type if previous else OrderProvider.CommissionType.PERCENT),
            'commission_rate': order.commission_rate,
            'commission_fixed': club_share.commission_fixed if club_share else (previous.commission_fixed if previous else 0),
            'provider_income': order.provider_income,
            'released_at': None,
        },
    )


def release_rejected_provider(order):
    """未开始服务的拒单不产生收益，保留参与记录但释放份额和名额。"""
    if order.providers.filter(settled_at__isnull=False).exists():
        raise SettlementError('订单已有结算记录，不能拒单回池')
    order.providers.update(settlement_base=0, provider_income=0, commission_fixed=0, released_at=timezone.now())


# ---------------------------------------------------------------------------
# 陪玩产能与状态
# ---------------------------------------------------------------------------

def active_orders_for(
    account=None, *, account_id=None, provider_id=None, exclude_order_ids=(),
):
    """返回陪玩当前进行中的订单查询集（已接单 + 服务中），已 ``distinct``。

    兼容 ``Order.provider_account`` 外键与 ``OrderProvider`` 中间表两种关联，
    因此双陪订单对参与的每个打手都会命中。给出多个维度时取并集
    （同一订单只算一次，靠 ``distinct``）—— 历史数据里 account 与 legacy user
    两个维度并非总是齐全，只查一边会漏。

    这层单独抽出来，是因为调用方除了「有几单」还需要「是哪几单」
    （后台封禁要把在途订单号回给运营）。两处各写一遍 Q 条件的下场是
    双陪那半边条件迟早只在其中一处被维护。

    Args:
        account: ``ClubAccount`` 实例（推荐维度，位置参数兼容旧调用）。
        account_id: ``ClubAccount`` 主键，已有 id 时免去一次对象查询。
        provider_id: 旧 ``CustomUser`` 主键。
        exclude_order_ids: 需要排除的订单 ID。状态机在 ``side_effect`` 里
            调用本函数时，目标订单的新状态尚未落库，必须显式排除自身，
            否则「刚完成的这一单」会把自己算成仍在进行中。

    Returns:
        QuerySet[Order]: 进行中的订单；所有维度都为空时返回空查询集。
    """
    condition = Q()
    if account is not None:
        condition |= Q(provider_account=account) | Q(providers__provider_account=account, providers__released_at__isnull=True, providers__settled_at__isnull=True)
    if account_id is not None:
        condition |= (
            Q(provider_account_id=account_id)
            | Q(providers__provider_account_id=account_id, providers__released_at__isnull=True, providers__settled_at__isnull=True)
        )
    if provider_id is not None:
        condition |= Q(provider_id=provider_id) | Q(providers__provider_id=provider_id, providers__released_at__isnull=True, providers__settled_at__isnull=True)
    if not condition:
        return Order.objects.none()

    queryset = Order.objects.filter(condition, status__in=ACTIVE_ORDER_STATUSES)
    if exclude_order_ids:
        queryset = queryset.exclude(id__in=list(exclude_order_ids))
    return queryset.distinct()


def count_active_orders(
    account=None, *, account_id=None, provider_id=None, exclude_order_ids=(),
):
    """统计陪玩当前进行中的订单数（已接单 + 服务中）。

    薄封装，条件口径完全复用 :func:`active_orders_for`。

    Returns:
        int: 进行中的订单数；所有维度都为空时返回 0。
    """
    return active_orders_for(
        account,
        account_id=account_id,
        provider_id=provider_id,
        exclude_order_ids=exclude_order_ids,
    ).count()


def refresh_escort_status(*, provider_ids=(), account_ids=(), exclude_order_ids=()):
    """按「是否还有进行中的订单」重算陪玩状态。**唯一允许改 status 的入口。**

    历史实现是订单一结束就无脑 ``update(status=AVAILABLE)``，两个真实 bug：

    1. 双开的陪玩手上还有另一单在服务中，结束 A 单后被标成「空闲」，
       抢单池立刻又给他派单，实际产能被击穿；
    2. 陪玩已经手动置为「离线」，一笔历史订单结算就把他强行拉回在线。

    这里改成「查一下他到底还忙不忙」，并且**永不覆盖 OFFLINE** ——
    离线是陪玩的主动意图，系统无权替他上线。

    Args:
        provider_ids: 旧 ``CustomUser`` 主键集合。
        account_ids: ``ClubAccount`` 主键集合。
        exclude_order_ids: 计数时排除的订单（通常是当前正在结算的那一单）。

    Returns:
        int: 实际发生状态变更的陪玩数量。
    """
    provider_ids = [pid for pid in provider_ids if pid]
    account_ids = [aid for aid in account_ids if aid]
    if not provider_ids and not account_ids:
        return 0

    condition = Q()
    if provider_ids:
        condition |= Q(user_id__in=provider_ids)
    if account_ids:
        condition |= Q(account_id__in=account_ids)

    changed = 0
    profiles = EscortProfile.objects.filter(condition).only(
        'id', 'status', 'user_id', 'account_id',
    )
    for profile in profiles:
        # 离线是主动意图，结算不得代为上线。
        if profile.status == EscortProfile.Status.OFFLINE:
            continue
        busy = count_active_orders(
            account_id=profile.account_id,
            provider_id=profile.user_id,
            exclude_order_ids=exclude_order_ids,
        )
        desired = (
            EscortProfile.Status.BUSY if busy else EscortProfile.Status.AVAILABLE
        )
        if desired == profile.status:
            continue
        EscortProfile.objects.filter(pk=profile.pk).update(status=desired)
        changed += 1
    return changed


def mark_escort_busy(*, provider_ids=(), account_ids=()):
    """接单/派单时把陪玩置为忙碌。**唯一允许置 BUSY 的入口。**

    与 ``refresh_escort_status`` 配对使用：那个负责「订单结束后要不要放开」，
    这个负责「订单开始时占住」。之所以不复用前者，是因为状态机在
    ``side_effect`` 阶段订单还没落库成 GRABBED，此时去数「进行中的订单」
    只会数到 0，反而把刚接单的人标成空闲。

    口径统一为「手上有至少一单在跑 = BUSY」。此前后台派单用的是
    「达到并发上限才 BUSY」，与陪玩自己抢单的口径相反，导致同一个人
    在两条入口下显示的状态不一致。并发上限的控制权仍在
    ``count_active_orders``，不靠 status 兼职。

    OFFLINE 不覆盖：离线的人根本不该被派到单，真出现了也应由准入校验拦，
    而不是靠这里把他悄悄拉上线。

    Returns:
        int: 实际发生状态变更的陪玩数量。
    """
    provider_ids = [pid for pid in provider_ids if pid]
    account_ids = [aid for aid in account_ids if aid]
    if not provider_ids and not account_ids:
        return 0

    condition = Q()
    if provider_ids:
        condition |= Q(user_id__in=provider_ids)
    if account_ids:
        condition |= Q(account_id__in=account_ids)

    return EscortProfile.objects.filter(condition).exclude(
        status__in=[EscortProfile.Status.OFFLINE, EscortProfile.Status.BUSY],
    ).update(status=EscortProfile.Status.BUSY)


# ---------------------------------------------------------------------------
# 结算
# ---------------------------------------------------------------------------

def require_order_evidence(order):
    """结算前凭证校验：每个参与结算的陪玩都必须有入队图 + 结单图。

    顾客端与后台是同一个「把钱打给陪玩」的动作，凭证要求必须一致，
    否则后台就成了绕过风控的后门，报单审核退化为事后追认。

    仅当 ``REQUIRE_ORDER_EVIDENCE_IMAGES`` 开启时生效。

    Raises:
        SettlementError: 任一陪玩凭证缺失；调用方事务整体回滚。
    """
    if not getattr(settings, 'REQUIRE_ORDER_EVIDENCE_IMAGES', False):
        return

    provider_rows = list(order.providers.all())
    if provider_rows:
        provider_ids = [row.provider_id for row in provider_rows if row.provider_id and (row.settlement_base > 0 or not row.released_at)]
    elif order.provider_id:
        provider_ids = [order.provider_id]
    else:
        provider_ids = []
    if not provider_ids:
        return

    reports = {
        report.provider_id: report
        for report in ProviderReport.objects.filter(
            order=order, provider_id__in=provider_ids,
        )
    }
    for provider_id in provider_ids:
        report = reports.get(provider_id)
        if report is None:
            raise SettlementError(f'陪玩 #{provider_id} 缺少服务报单凭证，无法结算')
        if not report.entry_image:
            raise SettlementError(f'陪玩 #{provider_id} 缺少入队截图，无法结算')
        if not report.completion_image:
            raise SettlementError(f'陪玩 #{provider_id} 缺少结单截图，无法结算')


def _credit_wallet(
    *,
    order,
    amount,
    tx_type,
    remark,
    event,
    trace_id,
    user_id=None,
    account_id=None,
    platform=False,
):
    """给某个主体的钱包入账并落流水 + 结构化日志。

    Args:
        order: 关联订单。
        amount: 入账金额（内部账务单位，非正数直接跳过）。
        tx_type: ``Transaction.TxType`` 取值。
        remark: 流水备注。
        event: 资金日志事件名。
        trace_id: 本次结算的追踪 ID。
        user_id / account_id: 收款主体；``platform=True`` 时忽略。
        platform: 为 True 时收款方是平台系统钱包。

    Returns:
        Transaction | None: 金额为 0 时不落流水，返回 None。
    """
    amount = int(amount or 0)
    if amount <= 0:
        return None

    if platform:
        wallet = get_platform_wallet(for_update=True)
    else:
        wallet = get_wallet(user_id=user_id, account_id=account_id, for_update=True)

    balance_before = wallet.balance
    wallet.balance += amount
    wallet.save(update_fields=['balance'])
    tx = Transaction.objects.create(
        wallet=wallet,
        order=order,
        amount=amount,
        tx_type=tx_type,
        balance_before=balance_before,
        balance_after=wallet.balance,
        remark=remark,
    )
    log_money_event(
        event,
        account_id=account_id,
        user_id=user_id,
        order_no=order.order_no,
        order_id=order.pk,
        tx_type=tx_type,
        amount=amount,
        balance_before=balance_before,
        balance_after=wallet.balance,
        trace_id=trace_id,
    )
    return tx


def settle_order(order, *, operator=None, operator_account=None, trace_id=None):
    """把一笔订单的钱打出去 —— 全站唯一的结算实现。

    执行顺序（顺序本身就是风控，不要调整）：

    1. 凭证闸门：缺图直接抛错，一分钱都不动；
    2. 守恒闸门：跨行汇总「本轮将要流出的钱」与订单实付比对，超额即阻断。
       单行 CheckConstraint 看不见「双陪两行各拿全额」这种跨行超付；
    3. 逐个打手入账并打上 ``settled_at``（重复结算的第二道防线）；
    4. 推荐人分佣、平台留存各入账一份；
    5. 完成计数 ``F() + 1``（读改写会在并发下丢更新），刷新陪玩状态。

    必须在 ``transaction.atomic`` 内调用，且订单行已加锁。

    Args:
        order: 已加锁的 ``Order`` 实例。注意状态机会在调用本函数前把
            ``order.status`` 在**内存**里改成 COMPLETED（尚未落库），
            因此第 5 步统计产能时必须排除本单。
        operator: 操作人（陪玩本人或后台客服），仅用于日志。
        operator_account: 业务账户维度的操作人，仅用于日志。
        trace_id: 追踪 ID；不传则自动生成。

    Returns:
        list[int]: 本次完成结算的打手 legacy user id 列表。

    Raises:
        SettlementError: 凭证缺失或分账超额。
        wallet.services.WalletAddressingError: 钱包脏数据。
    """
    if order.settled_at:
        return []
    from .financial_lock import lock_order_money
    lock_order_money()
    if order.customer_account_id or order.customer_id:
        get_wallet(account_id=order.customer_account_id, user_id=order.customer_id, for_update=True)
    if order.payment_status != Order.PaymentStatus.PAID:
        raise SettlementError('订单未付款或已退款，无法结算')
    if order.settlement_mode == Order.SettlementMode.REPORT_APPROVAL:
        from engagement.confirmation import settlement_ready
        if not settlement_ready(order):
            raise SettlementError('订单售后窗口尚未结束或客诉未处理，暂不能结算')
        reports = list(order.provider_reports.all())
        provider_ids = required_report_providers(order)
        approved_ids = {r.provider_id for r in reports if r.status == ProviderReport.Status.APPROVED}
        if not provider_ids or None in provider_ids or not provider_ids.issubset(approved_ids):
            raise SettlementError('所有打手报单审核通过后才能结算')
    trace_id = trace_id or new_trace_id()
    require_order_evidence(order)

    provider_rows = list(order.providers.select_for_update().all())
    pending_rows = [
        row for row in provider_rows if row.provider_id and not row.settled_at
        and (row.settlement_base > 0 or not row.released_at)
    ]

    if provider_rows:
        planned_incomes = [row.provider_income for row in pending_rows]
    elif order.provider_id:
        # 兼容无打手明细的旧单：按订单级 provider_income 给主打手入账。
        # 为 0 就是 0（抽成率 100% 是合法配置），绝不回落成 amount ——
        # 那会让陪玩拿走全款、平台留存还照发，凭空造钱。
        planned_incomes = [order.provider_income]
    else:
        planned_incomes = []

    assert_order_conserved(
        order.amount,
        planned_incomes,
        order.inviter_commission if order.inviter_id else 0,
        order.shop_income,
    )

    settled_provider_ids = []
    settled_account_ids = []
    from wallet.income import hold_provider_income
    provider_ids = [row.provider_id for row in pending_rows] if provider_rows else [order.provider_id]
    profiles = {p.user_id: p for p in EscortProfile.objects.select_for_update().filter(user_id__in=[pk for pk in provider_ids if pk]).order_by('user_id')}

    if provider_rows:
        now = timezone.now()
        for row in pending_rows:
            income_tx = _credit_wallet(
                order=order,
                amount=row.provider_income,
                tx_type=Transaction.TxType.INCOME,
                remark=f'订单收入：{order.order_no}',
                event='order.settle.provider',
                trace_id=trace_id,
                user_id=row.provider_id,
                account_id=row.provider_account_id,
            )
            hold_provider_income(income_tx, profile=profiles.get(row.provider_id), hold_hours=order.income_hold_hours)
            row.settled_at = now
            row.save(update_fields=['settled_at'])
            settled_provider_ids.append(row.provider_id)
            if row.provider_account_id:
                settled_account_ids.append(row.provider_account_id)
    elif order.provider_id:
        income_tx = _credit_wallet(
            order=order,
            amount=order.provider_income,
            tx_type=Transaction.TxType.INCOME,
            remark=f'订单收入：{order.order_no}',
            event='order.settle.provider',
            trace_id=trace_id,
            user_id=order.provider_id,
            account_id=order.provider_account_id,
        )
        hold_provider_income(income_tx, profile=profiles.get(order.provider_id), hold_hours=order.income_hold_hours)
        settled_provider_ids.append(order.provider_id)
        if order.provider_account_id:
            settled_account_ids.append(order.provider_account_id)

    if order.inviter_id:
        _credit_wallet(
            order=order,
            amount=order.inviter_commission,
            tx_type=Transaction.TxType.INCOME,
            remark=f'推荐分佣：{order.order_no}',
            event='order.settle.inviter',
            trace_id=trace_id,
            user_id=order.inviter_id,
            account_id=order.inviter_account_id,
        )

    _credit_wallet(
        order=order,
        amount=order.shop_income,
        tx_type=Transaction.TxType.SHOP_INCOME,
        remark=f'平台收入：{order.order_no}',
        event='order.settle.platform',
        trace_id=trace_id,
        platform=True,
    )

    if settled_provider_ids:
        # F() 自增：读到内存再 +1 写回，会在并发结算下丢计数。
        EscortProfile.objects.filter(user_id__in=settled_provider_ids).update(
            completed_order_count=F('completed_order_count') + 1,
        )
        refresh_escort_status(
            provider_ids=settled_provider_ids,
            account_ids=settled_account_ids,
            exclude_order_ids=[order.pk],
        )

    log_money_event(
        'order.settle',
        account_id=getattr(operator_account, 'pk', None),
        user_id=getattr(operator, 'pk', None),
        order_no=order.order_no,
        order_id=order.pk,
        amount=order.amount,
        trace_id=trace_id,
        operator_id=getattr(operator, 'pk', None),
        reason=f'providers={settled_provider_ids}',
    )
    order.settled_at = timezone.now()
    order.save(update_fields=['settled_at'])
    from engagement.services import award_order_points
    award_order_points(order)
    return settled_provider_ids


# ---------------------------------------------------------------------------
# 退款
# ---------------------------------------------------------------------------


def ensure_fulfillment_reports(order):
    """创建每个参与人的报单，金额取该参与人的分账快照。调用方持有订单锁。"""
    rows = list(order.providers.all())
    # 极小金额均分可能产生 0 基数，仍在服务的成员也必须提供履约凭证。
    participants = [
        (r.provider_id, r.provider_account_id, r.settlement_base, r.commission_rate)
        for r in rows if r.settlement_base > 0 or not r.released_at
    ]
    if not rows and order.provider_id:
        participants = [(order.provider_id, order.provider_account_id, order.amount, order.commission_rate)]
    for user_id, account_id, amount, rate in participants:
        if not user_id:
            continue
        report, created = ProviderReport.objects.get_or_create(
            provider_id=user_id, order=order,
            defaults={
                'provider_account_id': account_id, 'amount': amount,
                'game_name': order.service_name_snapshot or order.service.name,
                'description': order.remark, 'status': ProviderReport.Status.DRAFT,
                'commission_rate': rate,
            },
        )
        if report.status in (ProviderReport.Status.DRAFT, ProviderReport.Status.REJECTED):
            report.amount = amount
            report.commission_rate = rate
            report.save(update_fields=['amount', 'commission_rate', 'updated_at'])
        if created:
            from .notifier import notify_report_update
            transaction.on_commit(lambda report=report: notify_report_update(report))


def complete_fulfillment(order, *, operator=None, operator_account=None):
    """完成服务释放接单名额；新单等待全体报单通过，旧单继续原结算方式。"""
    if order.settlement_mode == Order.SettlementMode.COMPLETION:
        settled = settle_order(order, operator=operator, operator_account=operator_account)
        ensure_fulfillment_reports(order)
        return settled
    ensure_fulfillment_reports(order)
    provider_ids = list(order.providers.values_list('provider_id', flat=True)) or [order.provider_id]
    refresh_escort_status(provider_ids=[p for p in provider_ids if p], exclude_order_ids=[order.pk])
    return []


def missing_report_evidence(report):
    missing = []
    if not report.entry_image:
        missing.append('入队截图')
    if not report.completion_image:
        missing.append('结单截图')
    if not report.result_images.exists():
        missing.append('战绩截图')
    return missing


def required_report_providers(order):
    rows = list(order.providers.all())
    return {r.provider_id for r in rows if r.settlement_base > 0 or not r.released_at} if rows else {order.provider_id}


def report_edit_error(report):
    if report.status not in (ProviderReport.Status.DRAFT, ProviderReport.Status.REJECTED):
        return '当前报单不可修改凭证'
    if not report.order_id:
        return ''
    order = report.order
    if order.status not in (Order.Status.GRABBED, Order.Status.IN_SERVICE, Order.Status.COMPLETED) or order.payment_status != Order.PaymentStatus.PAID:
        return '订单已取消、退款或已回到待接单池，报单流程已停止'
    if report.provider_id not in required_report_providers(order):
        return '本人已退出服务且无结算份额，无需提交报单'
    return ''


@transaction.atomic
def approve_order_report(report_id, *, operator, operator_account):
    initial = ProviderReport.objects.only('order_id').get(pk=report_id)
    # 所有入口统一先锁订单、再锁报单，双陪同时审核不会互相反向等待。
    order = Order.objects.select_for_update().get(pk=initial.order_id)
    report = ProviderReport.objects.select_for_update().get(pk=report_id)
    if report.status != ProviderReport.Status.PENDING:
        raise SettlementError('该报单不是待审核状态，无法重复操作')
    if order.status != Order.Status.COMPLETED or order.payment_status != Order.PaymentStatus.PAID:
        raise SettlementError('订单未完成或已退款，不能通过报单')
    missing = missing_report_evidence(report) if (
        order.settlement_mode == Order.SettlementMode.REPORT_APPROVAL
        or settings.REQUIRE_ORDER_EVIDENCE_IMAGES
    ) else []
    if missing:
        raise SettlementError('报单缺少：' + '、'.join(missing))
    own_share = order.providers.filter(provider_id=report.provider_id).first()
    if not own_share and order.provider_id != report.provider_id:
        raise SettlementError('报单人不属于该订单')
    report.status = ProviderReport.Status.APPROVED
    report.commission_rate = own_share.commission_rate if own_share else order.commission_rate
    report.payout_amount = own_share.provider_income if own_share else order.provider_income
    report.auditor = operator
    report.auditor_account = operator_account
    report.audited_at = timezone.now()
    report.save(update_fields=['status', 'commission_rate', 'payout_amount', 'auditor', 'auditor_account', 'audited_at'])
    if order.settlement_mode == Order.SettlementMode.REPORT_APPROVAL and not order.settled_at:
        provider_ids = required_report_providers(order)
        approved_ids = set(order.provider_reports.filter(status=ProviderReport.Status.APPROVED).values_list('provider_id', flat=True))
        from engagement.confirmation import settlement_ready, refresh_report_clock
        refresh_report_clock(order)
        if provider_ids.issubset(approved_ids) and settlement_ready(order):
            settle_order(order, operator=operator, operator_account=operator_account)
    transaction.on_commit(lambda: push_message_safe(
        recipient_id=report.provider_id, title='报单审核通过',
        preview='收益已结算到账' if order.settled_at else '凭证已通过，全部打手审核通过且售后期结束后自动结算',
        msg_type='ORDER', related_order_id=order.pk,
    ))
    from .notifier import notify_report_update
    transaction.on_commit(lambda: notify_report_update(report))
    return report

@transaction.atomic
def refund_to_customer(order, reason='订单退款', *, trace_id=None, operator=None, operator_account=None,
                       allow_settled_recovery=False, plan_token=None, refund_amount=None, client_request_id=None):
    """Refund an authorized amount; caller holds the order and saves its state."""
    from .financial_lock import lock_order_money
    from .refunds import (PAID_STATES, posted_settlement, recover_settlement, authorized_refund,
                          refunded_amount, parse_refund_amount, replay_refund, request_fingerprint, RefundError)
    from .models import OrderRefund
    existing = replay_refund(order,client_request_id,refund_amount,reason)
    if existing:
        order._refund_replayed = True
        return existing
    if order.payment_status not in PAID_STATES:
        # Ordinary cancellation can still close an unpaid legacy order.
        return None
    lock_order_money()
    prior = refunded_amount(order)
    amount = parse_refund_amount(refund_amount,order.amount-prior)
    if amount <= 0:
        raise RefundError('订单已无可退余款',code='REFUND_INVALID_AMOUNT')
    settled = posted_settlement(order)
    full = prior+amount == order.amount
    if not full and (not settled or order.status != 'COMPLETED'):
        raise RefundError('仅已结算完成订单支持部分退款',code='REFUND_PARTIAL_UNSETTLED')
    if not full and not client_request_id:
        raise RefundError('部分退款须提供操作凭证',code='REFUND_INVALID_REQUEST')
    if settled and not (allow_settled_recovery and authorized_refund(operator_account)):
        raise SettlementError('已结算订单仅可由有退款权限的后台核算追回')
    from engagement.confirmation import aftersale_deadline
    cutoff = aftersale_deadline(order)
    if not settled and (order.consumption_confirmed_at or (cutoff and timezone.now() >= cutoff and not order.aftersales.filter(status='OPEN').exists())):
        raise SettlementError('售后期限已结束，订单不再支持退款')
    trace_id = trace_id or new_trace_id()
    customer_wallet = get_wallet(user_id=order.customer_id,account_id=order.customer_account_id,for_update=True)
    refund = OrderRefund.objects.create(order=order,amount=amount,reason=reason,operator=operator,operator_account=operator_account,
        client_request_id=client_request_id or None,request_fingerprint=request_fingerprint(amount,reason))
    changed_wallets = [customer_wallet]
    if settled:
        changed_wallets += recover_settlement(order,refund,plan_token=plan_token,operator=operator,operator_account=operator_account)
        customer_wallet.refresh_from_db()
    before = customer_wallet.balance
    customer_wallet.balance += amount
    customer_wallet.save(update_fields=['balance'])
    Transaction.objects.create(wallet=customer_wallet,order=order,amount=amount,tx_type=Transaction.TxType.REFUND,
        balance_before=before,balance_after=customer_wallet.balance,remark=f'{reason}：{order.order_no}',
        operator=operator,operator_account=operator_account)
    log_money_event('order.refund',account_id=order.customer_account_id,user_id=order.customer_id,
        order_no=order.order_no,order_id=order.pk,tx_type=Transaction.TxType.REFUND,amount=amount,
        balance_before=before,balance_after=customer_wallet.balance,trace_id=trace_id,reason=reason)
    order.payment_status = Order.PaymentStatus.REFUNDED if full else Order.PaymentStatus.PARTIALLY_REFUNDED
    order.refunded_at = timezone.now()
    from engagement.services import reverse_order_points, release_order_allocations
    reverse_order_points(order,refund=refund)
    release_order_allocations(order,refund=refund)
    if full:
        from coupons.benefits import restore_order_coupons
        restore_order_coupons(order)
    from engagement.refunds import reverse_refund_benefits
    refund.benefit_effects = reverse_refund_benefits(order,refund,operator_account)
    refund.save(update_fields=['benefit_effects','recovered_amount','plan_fingerprint'])
    for wallet in {row.pk:row for row in changed_wallets}.values():
        transaction.on_commit(lambda wallet=wallet:notify_wallet_update(wallet))
    return refund


# ---------------------------------------------------------------------------
# 旁路副作用（失败不得影响主流程）
# ---------------------------------------------------------------------------

def schedule_auto_cancel(order):
    """投递超时取消任务到 Celery；不可用时静默失败。

    Celery 没起来不是下单失败的理由 —— management command
    (``check_order_timeout``) 会兜底扫描超时单。
    """
    try:
        from .tasks import auto_cancel_pending_order
        auto_cancel_pending_order.apply_async(
            args=[order.id],
            countdown=PENDING_TIMEOUT_MINUTES * 60,
        )
    except Exception:
        return


def pending_timeout_deadline(from_time=None):
    """返回 PENDING 订单的自动取消时间点。"""
    base = from_time or timezone.now()
    return base + timedelta(minutes=PENDING_TIMEOUT_MINUTES)


def push_message_safe(*, recipient_id, title, preview='', detail='',
                      msg_type='SYSTEM', action_url='', related_order_id=None):
    """安全推送站内消息：站内信模块异常时静默失败，绝不影响主流程。"""
    if create_message is None or recipient_id is None:
        return
    if related_order_id and not action_url:
        action_url = f'/pages/orderList/index?orderId={related_order_id}'
    try:
        create_message(
            recipient_id=recipient_id,
            title=title,
            preview=preview,
            detail=detail,
            msg_type=msg_type,
            action_url=action_url,
            related_order_id=related_order_id,
        )
    except Exception:
        return
