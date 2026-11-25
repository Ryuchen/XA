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
* 金额一律是内部账务单位整数（10 = 1 浪花），本模块不做任何 ÷10 换算。
"""

from datetime import timedelta

from django.conf import settings
from stores.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from common.logging_utils import log_money_event, new_trace_id
from users.models import EscortProfile
from wallet.models import ProviderReport, Transaction, get_platform_wallet
from wallet.notifier import notify_wallet_update
from wallet.services import WalletAddressingError, get_wallet

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


def ensure_provider_dispatch_locks(account_ids):
    """在业务事务和行锁之前初始化名额互斥行。

    首次 INSERT 会取得 ClubAccount 外键共享锁。如果把它留在业务事务中，
    并发封禁可持有互斥行再等待账号排他锁，而派单持有账号共享锁等待互斥行，
    形成死锁。入口应先在 autocommit 下完成初始化，再开始订单/资金事务。
    """
    ids = sorted({int(pk) for pk in account_ids if pk})
    if ids:
        from .capacity import ensure_capacity_rule
        ensure_capacity_rule()
    for account_id in ids:
        ProviderDispatchLock.objects.get_or_create(account_id=account_id)


def lock_provider_dispatch(account_ids):
    """事务内锁住本次占用的打手名额，直到订单写入提交。

    各入口必须在计数和扣款前调用。多打手按账号 ID 排序，防止双陪交叉派单
    反向等待。使用独立互斥行，避免与结算中钱包/资料行的锁顺序形成环。
    入口先调用 ensure_provider_dispatch_locks，再开启事务；此处只锁已有行，
    不在持有业务锁时创建外键行。已有订单先锁订单再调用；新建订单在建单前
    调用。释放名额无需取此锁。
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('派单名额锁必须在业务事务内获取')
    ids = sorted({int(pk) for pk in account_ids if pk})
    for account_id in ids:
        ProviderDispatchLock.objects.select_for_update().get(account_id=account_id)
    if ids:
        from .capacity import lock_capacity_rule_for_admission
        lock_capacity_rule_for_admission()


def sync_single_provider_share(order):
    """持有订单锁时，绑定本轮单陪的完整份额；拒单后重接也复用冻结价格。"""
    if order.escort_mode != Order.EscortMode.SINGLE or not order.provider_id:
        return
    previous = order.providers.first()
    club_share = None
    personal_evidence = None
    if (order.commission_allocation_pending or
            (not order.in_service_at and (order.game_commission_snapshot or {}).get('personal_rules'))):
        from .delta_personal_commission import pending_assignment_terms
        profile = EscortProfile.objects.select_for_update().get(account_id=order.provider_account_id)
        shares, split, evidence = pending_assignment_terms(order, [profile], allow_reassignment=True)
        club_share, personal_evidence = shares[0], evidence[0]
        order.commission_rate = split.commission_rate
        order.provider_income = split.provider_income
        order.inviter_commission = split.inviter_commission
        order.shop_income = split.shop_income
        order.game_commission_snapshot = personal_evidence
        order.commission_allocation_pending = False
        order.save(update_fields=['commission_rate', 'provider_income', 'inviter_commission',
                                  'shop_income', 'game_commission_snapshot', 'commission_allocation_pending'])
    elif (order.game_commission_snapshot or order.commission_policy != 'LEGACY'
            or order.commission_type == 'FIXED' or order.commission_override):
        from .pricing import club_provider_specs
        from .settlement import build_provider_shares, aggregate_order_split
        profile = EscortProfile.objects.select_related('level').get(user_id=order.provider_id)
        club_share = build_provider_shares(order.amount, club_provider_specs(order, [profile], order.amount))[0]
        # 未开始服务的指派/重接以本轮陪玩等级生成快照，开始后固定。
        if order.in_service_at and previous and previous.provider_id == order.provider_id:
            return
        inviter_rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
        split = aggregate_order_split(order.amount, [club_share], inviter_rate)
        order.commission_rate = split.commission_rate
        order.provider_income = split.provider_income
        order.inviter_commission = split.inviter_commission
        order.shop_income = split.shop_income
        order.save(update_fields=['commission_rate', 'provider_income', 'inviter_commission', 'shop_income'])
    # 兼容旧版本拒单后未释放的参与明细；未开工的回池单没有历史履约收益。
    if not order.in_service_at:
        order.providers.exclude(provider_id=order.provider_id).filter(settled_at__isnull=True, released_at__isnull=True).update(
            settlement_base=0, provider_income=0, commission_fixed=0, released_at=timezone.now(),
        )
        if getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False):
            from .models import OrderPlatformSubsidyShare
            OrderPlatformSubsidyShare.objects.filter(assignment__order=order, paid_at__isnull=True).exclude(
                assignment__provider_id=order.provider_id).delete()
    assignment, _ = OrderProvider.objects.update_or_create(
        order=order, provider_id=order.provider_id,
        defaults={
            'provider_account_id': order.provider_account_id,
            'provider_business_cycle': order.provider_account.business_cycle if order.provider_account_id else None,
            'provider_name_snapshot': order.provider_name_snapshot,
            'settlement_base': order.amount,
            'commission_type': club_share.commission_type if club_share else (previous.commission_type if previous else OrderProvider.CommissionType.PERCENT),
            'commission_rate': order.commission_rate,
            'game_commission_snapshot': personal_evidence or order.game_commission_snapshot,
            'commission_fixed': club_share.commission_fixed if club_share else (previous.commission_fixed if previous else 0),
            'provider_income': order.provider_income,
            'released_at': None,
        },
    )
    if getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False):
        from .models import OrderPlatformSubsidy, OrderPlatformSubsidyShare
        subsidy = OrderPlatformSubsidy.objects.filter(order=order).first()
        if subsidy:
            from .pricing import club_provider_specs
            from .settlement import build_provider_shares
            profile = EscortProfile.objects.select_related('level').get(user_id=order.provider_id)
            root = order.game_commission_snapshot
            if isinstance(root, dict) and root.get('personal_rules'):
                from .delta_personal_commission import verify_assignment_evidence
                if not verify_assignment_evidence(order, assignment):
                    raise ValueError('安全箱陪玩本人资格及付款抽成快照待核对')
                specs = [{'commission_type': 'PERCENT', 'commission_rate': assignment.commission_rate}]
            else:
                specs = club_provider_specs(order, [profile], subsidy.basis)
            if specs is None:
                specs = [{'commission_type': assignment.commission_type, 'commission_rate': assignment.commission_rate,
                          'commission_fixed': assignment.commission_fixed}]
            funded_share = build_provider_shares(subsidy.basis, specs)[0]
            OrderPlatformSubsidyShare.objects.update_or_create(
                subsidy=subsidy, assignment=assignment, paid_at__isnull=True,
                defaults={'amount': funded_share.provider_income},
            )
            from .segment_financial_capture import subsidy_capture_enabled
            if subsidy_capture_enabled():
                order._subsidy_entry_terms = {'assignment_id': assignment.pk, 'share': funded_share}


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


def capacity_orders_for(account=None, *, account_id=None, provider_id=None, exclude_order_ids=()):
    """Open assignments consuming capacity; unstarted paid bookings do not.

    Keep ``active_orders_for`` unchanged for deposit refunds, account bans and
    other outstanding-business checks: those must still see future bookings.

    传入 OuterRef 等表达式时返回单层子查询可用的 Order 查询集：Django 6 的
    OuterRef 只解析直接外层，账号维度直接引用外层字段，入池证据引用订单自身
    主陪玩字段。主单口径与真实入池一致；双陪转预约且次席未入池的极端组合
    留最终联调核对。具体值路径保留原嵌套口径。
    """
    from django.db.models import Exists, OuterRef, Q
    from django.db.models.expressions import Combinable
    from club_accounts.models import LegacyAccountMap
    from .models import OrderWorkStint
    account_ids = {value for value in (getattr(account, 'pk', None), account_id) if value is not None}
    expression_dims = isinstance(provider_id, Combinable) or any(
        isinstance(value, Combinable) for value in account_ids)
    if expression_dims:
        match = Q()
        concrete_ids = [value for value in account_ids if not isinstance(value, Combinable)]
        if concrete_ids:
            match |= Q(provider_account_id__in=concrete_ids)
            match |= Q(providers__provider_account_id__in=concrete_ids,
                       providers__released_at__isnull=True, providers__settled_at__isnull=True)
        for value in account_ids:
            if isinstance(value, Combinable):
                match |= Q(provider_account_id=value)
                match |= Q(providers__provider_account_id=value,
                           providers__released_at__isnull=True, providers__settled_at__isnull=True)
        if provider_id is not None:
            match |= Q(provider_id=provider_id)
            match |= Q(providers__provider_id=provider_id,
                       providers__released_at__isnull=True, providers__settled_at__isnull=True)
        if not match:
            return Order.objects.none()
        entered = OrderWorkStint.objects.filter(
            order_id=OuterRef('pk'), account_id=OuterRef('provider_account_id'),
            started_at__isnull=False, released_at__isnull=True)
        queryset = Order.objects.filter(match, status__in=ACTIVE_ORDER_STATUSES).annotate(
            _capacity_entered=Exists(entered)).exclude(
            status=Order.Status.GRABBED, reservation__status='CONVERTED', _capacity_entered=False)
        if exclude_order_ids:
            queryset = queryset.exclude(id__in=list(exclude_order_ids))
        return queryset.distinct()
    if provider_id is not None:
        account_ids.update(LegacyAccountMap.objects.filter(legacy_user_id=provider_id)
                           .values_list('account_id', flat=True))
    entered = OrderWorkStint.objects.filter(order_id=OuterRef('pk'), account_id__in=account_ids,
                                             started_at__isnull=False, released_at__isnull=True)
    return active_orders_for(account, account_id=account_id, provider_id=provider_id,
        exclude_order_ids=exclude_order_ids).annotate(_capacity_entered=Exists(entered)).exclude(
            status=Order.Status.GRABBED, reservation__status='CONVERTED', _capacity_entered=False)


def count_capacity_orders(account=None, *, account_id=None, provider_id=None, exclude_order_ids=()):
    return capacity_orders_for(account, account_id=account_id, provider_id=provider_id,
                               exclude_order_ids=exclude_order_ids).count()


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
        busy = count_capacity_orders(
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
    log_after_commit=False,
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
        log_after_commit: 事务提交后才写入资金日志，回滚时不记录成功入账。

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
    log_values = dict(
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
    if log_after_commit:
        transaction.on_commit(lambda: log_money_event(event, **log_values), robust=True)
    else:
        log_money_event(event, **log_values)
    return tx


def settle_order(order, *, operator=None, operator_account=None, trace_id=None):
    from .points_service_payment import needs_integrated_processing
    if (getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
            and needs_integrated_processing(order)):
        if not Order.objects.filter(pk=order.pk, status=Order.Status.COMPLETED).exists():
            return []
        from .models import OrderOriginalShareTransfer
        if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
            # The common dispatch checks add-on eligibility before it reaches
            # the segment writer, so bind the same transfer scope here too.
            from .points_transfer_refund_access import after_transfer
            with after_transfer(order.pk, phase='settlement'):
                return _settle_order(order, operator=operator, operator_account=operator_account, trace_id=trace_id)
        from .points_business_access import service_order
        with service_order(order.pk, phase='settlement', addon=order.addon_intents.filter(payment__isnull=False).exists()):
            return _settle_order(order, operator=operator, operator_account=operator_account, trace_id=trace_id)
    return _settle_order(order, operator=operator, operator_account=operator_account, trace_id=trace_id)


def _settle_order(order, *, operator=None, operator_account=None, trace_id=None):
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
    if (order.commission_allocation_pending or order.commission_rate is None
            or order.provider_income is None or order.shop_income is None):
        raise SettlementError('订单陪玩抽成和收益仍待分配，暂不能结算')
    # A service without its payment-time timing evidence cannot safely enter
    # any settlement branch, including otherwise specialized funding paths.
    if not order.is_gift_snapshot and (order.service_clock_version is None
            or order.service_aftersale_hours is None or order.service_freeze_hours is None
            or order.service_deposit_percent is None):
        return []
    from .segment_settlement import completion_transfer_order_eligible
    if (order.settlement_mode == order.SettlementMode.COMPLETION and completion_transfer_order_eligible(order)):
        from .models import OrderOriginalShareTransfer
        if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
            from .segment_settlement import settle_due_segment_people
            return settle_due_segment_people(order, operator=operator, operator_account=operator_account, trace_id=trace_id)
    if order.funding_terms is not None and not order.is_gift_snapshot and not order.source_order_id:
        from .negotiation_followup_accounting import marked_followup_id
        followup_id = marked_followup_id(order)
        if followup_id is not None:
            from .models import NegotiationFollowupLink
            from .ordinary_addon_followup_settlement import is_followup, settle_locked
            link = NegotiationFollowupLink.objects.filter(pk=followup_id, order=order).first()
            if link and is_followup(link):
                from .ordinary_followup_capability import stage_enabled
                if not stage_enabled('ORDINARY_ADDON_FOLLOWUP_SETTLEMENT_INTERNAL_TESTING'):
                    return []
                return settle_locked(order, link, trace_id=trace_id)
    from .addon_settlement import enabled as addon_settlement_enabled, has_paid_addons
    from .ordinary_addon_production import eligible as ordinary_addon_eligible
    if (not order.is_gift_snapshot and order.status == Order.Status.COMPLETED
            and order.aftersales.filter(status='OPEN').exists() and order.refund_records.count() == 1):
        from .points_checkout_test import ordinary_refund_production_order
        from .ordinary_funding_continuation import verified_refunded_continuation
        if (ordinary_refund_production_order(order, allow_open=True)
                or ordinary_addon_eligible(order, allow_open=True)):
            with verified_refunded_continuation(order, allow_open_case=True, allow_paid_addons=has_paid_addons(order)):
                from .ordinary_refunded_addon_settlement import settle_due_locked
                return settle_due_locked(order, trace_id=trace_id)
    if (not order.is_gift_snapshot and order.status == Order.Status.COMPLETED
            and order.aftersales.filter(status='OPEN').exists() and has_paid_addons(order)
            and not order.refund_records.exists() and ordinary_addon_eligible(order, allow_open=True)):
        from .points_settlement_test import settle_points_order
        return settle_points_order(order, operator=operator, operator_account=operator_account, trace_id=trace_id)
    if addon_settlement_enabled(order) and has_paid_addons(order):
        from .points_service_payment import needs_integrated_processing
        if needs_integrated_processing(order) and order.refund_records.exists():
            from .models import OrderOriginalShareTransfer
            if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
                from .segment_settlement import settle_due_segment_people
                return settle_due_segment_people(order, operator=operator, operator_account=operator_account,
                                                 trace_id=trace_id)
            if order.refund_records.count() != 1:
                if (order.refund_records.count() == 2
                        and (getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False)
                             or getattr(settings, 'SERVICE_POINTS_FOLLOWUP_ADDON_UNPAID_SETTLEMENT_TESTING', False)
                             or getattr(settings, 'SERVICE_POINTS_FOLLOWUP_ADDON_PAID_SETTLEMENT_TESTING', False))):
                    from .points_followup_settlement import enabled as followup_enabled, settle_followup_order
                    if followup_enabled():
                        return settle_followup_order(order, operator=operator, operator_account=operator_account, trace_id=trace_id)
                return []  # Follow-up refunds need their own settlement version.
            from .points_refunded_settlement import enabled as refunded_enabled, settle_refunded_points_order
            from .points_business_access import permits
            if (not refunded_enabled() or not (permits(order, phase='settlement', kind='addon')
                    or getattr(settings, 'SERVICE_POINTS_REFUND_ADDON_RELEASE_TESTING', False))):
                return []
            return settle_refunded_points_order(order, operator=operator, operator_account=operator_account, trace_id=trace_id)
        from .models import OrderOriginalShareTransfer
        from .negotiation_zero import ordinary_funding_enabled
        if (not needs_integrated_processing(order)
                and (order.refund_records.exists() or ordinary_funding_enabled(order, has_addons=True))
                and not OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
                and (getattr(settings, 'ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING', False)
                     or ordinary_addon_eligible(order) or ordinary_funding_enabled(order))):
            from .negotiation_followup_accounting import marked_followup_id
            followup_id = marked_followup_id(order)
            if followup_id is not None:
                from .models import NegotiationFollowupLink
                from .ordinary_addon_followup_settlement import is_followup, settle_locked
                link = NegotiationFollowupLink.objects.filter(pk=followup_id, order=order).first()
                if link and is_followup(link):
                    from .ordinary_followup_capability import stage_enabled
                    if not stage_enabled('ORDINARY_ADDON_FOLLOWUP_SETTLEMENT_INTERNAL_TESTING'):
                        return []
                    return settle_locked(order, link, trace_id=trace_id)
            from .ordinary_refunded_addon_settlement import reserve_locked
            if (ordinary_addon_eligible(order) or ordinary_funding_enabled(order)
                    or getattr(settings, 'ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING', False)):
                from .ordinary_refunded_addon_settlement import settle_due_locked
                return settle_due_locked(order, trace_id=trace_id)
            reserve_locked(order)
            return []  # Individual earnings still require the corresponding verified posting.
        from .segment_addon_settlement import enabled as segment_addon_enabled
        from .points_transfer_refund_access import permits as points_transfer_permits
        if (points_transfer_permits(order, phase='settlement')
                or (segment_addon_enabled() and OrderOriginalShareTransfer.objects.filter(
                    previous_segment__attribution__order=order).exists())):
            from .segment_settlement import settle_due_segment_people
            return settle_due_segment_people(order, operator=operator, operator_account=operator_account, trace_id=trace_id)
        from .points_settlement_test import settle_points_order
        return settle_points_order(order, operator=operator, operator_account=operator_account, trace_id=trace_id)
    # A refunded transferred ordinary order has its own whole-order funding
    # and per-segment add-on settlement; do not route it into the legacy
    # guard which has no knowledge of those facts.
    from .segment_settlement import _ordinary_transfer_refund
    if _ordinary_transfer_refund(order):
        from .ordinary_transfer_capability import permits as ordinary_transfer_permits
        if ordinary_transfer_permits(order):
            from .segment_settlement import settle_due_segment_people
            return settle_due_segment_people(order, operator=operator, operator_account=operator_account, trace_id=trace_id)
    from .addon_payment import guard_main_settlement
    guard_main_settlement(order)
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order):
        from .models import OrderOriginalShareTransfer
        if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
            from .segment_settlement import settle_due_segment_people
            return settle_due_segment_people(order, operator=operator, operator_account=operator_account, trace_id=trace_id)
        if order.refund_records.exists():
            if order.refund_records.count() == 2:
                from .points_followup_settlement import enabled as followup_settlement_enabled, settle_followup_order
                if followup_settlement_enabled():
                    return settle_followup_order(order, operator=operator, operator_account=operator_account, trace_id=trace_id)
            from .points_refunded_settlement import enabled as refunded_settlement_enabled, settle_refunded_points_order
            if getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True:
                # A transition can pass an unsaved COMPLETED object; wait until
                # completion is persisted before opening the settlement scope.
                if not Order.objects.filter(pk=order.pk, status=Order.Status.COMPLETED).exists():
                    return []
                from .points_business_access import service_order
                with service_order(order.pk, phase='settlement'):
                    return settle_refunded_points_order(order, operator=operator, operator_account=operator_account, trace_id=trace_id)
            if not refunded_settlement_enabled():
                return []
            return settle_refunded_points_order(order, operator=operator, operator_account=operator_account, trace_id=trace_id)
        from .points_checkout_test import settlement_enabled
        if (not settlement_enabled()
                and getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is not True):
            raise SettlementError('积分订单收益结算尚未开放，请联系客服核对')
        from .points_settlement_test import settle_points_order
        return settle_points_order(order, operator=operator, operator_account=operator_account, trace_id=trace_id)
    from .negotiation_zero import ordinary_funding_enabled
    zero_no_addon = ordinary_funding_enabled(order, has_addons=False)
    from .points_checkout_test import ordinary_refund_production_order
    from .points_checkout_test import ordinary_settlement_enabled
    from .models import OrderOriginalShareTransfer
    has_service_transfer = OrderOriginalShareTransfer.objects.filter(
        previous_segment__attribution__order=order).exists()
    if (order.funding_terms is not None and not ordinary_settlement_enabled()
            and order.refund_records.count() == 1 and not order.is_gift_snapshot and not order.source_order_id
            and not has_service_transfer):
        from .ordinary_funding_continuation import verified_refunded_continuation
        from .ordinary_refunded_addon_settlement import settle_due_locked, verify_recorded_locked
        with verified_refunded_continuation(order):
            if order.settled_at:
                verify_recorded_locked(order)
                return []
            return settle_due_locked(order, trace_id=trace_id)
    if (not order.is_gift_snapshot and (order.refund_records.count() == 1 or zero_no_addon)
            and (ordinary_refund_production_order(order) or zero_no_addon
                 or getattr(settings, 'ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING', False))):
        from .models import OrderOriginalShareTransfer
        if not OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
            from .ordinary_refunded_addon_settlement import settle_due_locked, verify_recorded_locked
            if order.settled_at:
                verify_recorded_locked(order)
                return []
            return settle_due_locked(order, trace_id=trace_id)
    from .models import OrderPlatformSubsidy
    if OrderPlatformSubsidy.objects.filter(order=order).exists():
        if getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False):
            from .subsidies import settle_due_subsidy_order
            return settle_due_subsidy_order(order)
        # A saved subsidy remains authoritative even if the feature switch is
        # turned off; never settle its zero payment as an ordinary order.
        return []
    from .segment_settlement import _ordinary_transfer_refund
    if _ordinary_transfer_refund(order):
        from .segment_settlement import enabled as segment_enabled, settle_due_segment_people
        from .ordinary_transfer_capability import permits as ordinary_transfer_permits
        if not segment_enabled(order) or not (ordinary_transfer_permits(order)
                or getattr(settings, 'ORDINARY_TRANSFER_REFUND_SETTLEMENT_INTERNAL_TESTING', False)):
            return []
        return settle_due_segment_people(order, operator=operator, operator_account=operator_account, trace_id=trace_id)
    from .points_checkout_test import ordinary_settlement_enabled
    if order.funding_terms is not None and not order.is_gift_snapshot and not order.source_order_id:
        from .points_settlement_test import settle_points_order
        return settle_points_order(order, operator=operator, operator_account=operator_account, trace_id=trace_id)
    if order.settled_at:
        return []
    # A source-tracked payment must never fall through to the older split,
    # which treats gift and compensation as cash and ignores store funding.
    # The verified ordinary funding route above is required to pay these orders.
    if order.funding_allocations.exists():
        raise SettlementError('订单资金核对及补资结算尚未接通，请联系门店')
    from .negotiation_settlement import execution_for_order, settle_due_negotiated
    negotiation = execution_for_order(order, lock=True)
    if negotiation:
        return settle_due_negotiated(order, negotiation, operator=operator,
                                     operator_account=operator_account, trace_id=trace_id)
    from .segment_settlement import protection_enabled as segment_protected
    if segment_protected():
        from .models import OrderSegmentSettlementManifest
        if OrderSegmentSettlementManifest.objects.filter(order=order).exists():
            from .segment_settlement import enabled as segment_enabled, settle_due_segment_people
            return (settle_due_segment_people(order, operator=operator, operator_account=operator_account, trace_id=trace_id)
                    if segment_enabled() else [])
    from .segment_financial_capture import transfer_settlement_pending
    if transfer_settlement_pending(order):
        from .segment_settlement import enabled, settle_due_segment_people
        if enabled():
            return settle_due_segment_people(order, operator=operator, operator_account=operator_account, trace_id=trace_id)
        return []
    from .direct_payment_models import DirectPaymentIntent
    if DirectPaymentIntent.objects.filter(order_id=order.pk).exists():
        raise SettlementError('微信支付订单的渠道结算尚未开放')
    from .service_settlement import supports_partial_service_settlement, settle_due_service_shares
    if supports_partial_service_settlement(order):
        return settle_due_service_shares(order, operator=operator, operator_account=operator_account, trace_id=trace_id)
    from .financial_lock import lock_order_money
    lock_order_money()
    if order.customer_account_id or order.customer_id:
        get_wallet(account_id=order.customer_account_id, user_id=order.customer_id, for_update=True)
    if order.payment_status != Order.PaymentStatus.PAID:
        raise SettlementError('订单未付款或已退款，无法结算')
    if order.settlement_mode == Order.SettlementMode.REPORT_APPROVAL:
        from engagement.confirmation import settlement_ready, service_reports_mature
        if not settlement_ready(order):
            raise SettlementError('订单售后窗口尚未结束或客诉未处理，暂不能结算')
        reports = list(order.provider_reports.all())
        provider_ids = required_report_providers(order)
        approved_ids = {r.provider_id for r in reports if r.status == ProviderReport.Status.APPROVED}
        if not provider_ids or None in provider_ids or not provider_ids.issubset(approved_ids):
            raise SettlementError('所有打手报单审核通过后才能结算')
        if not service_reports_mature(order):
            raise SettlementError('报单收益冻结期尚未结束或时限快照待核对')
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
        order.inviter_commission if order.inviter_id or order.inviter_account_id else 0,
        order.shop_income,
    )

    settled_provider_ids = []
    settled_account_ids = []
    from wallet.income import hold_provider_income
    provider_ids = [row.provider_id for row in pending_rows] if provider_rows else [order.provider_id]
    profiles = {p.user_id: p for p in EscortProfile.objects.select_for_update().filter(user_id__in=[pk for pk in provider_ids if pk]).order_by('user_id')}

    zero_service_clock = None
    if provider_rows:
        if order.service_clock_version is not None:
            zero_profiles = [profiles.get(row.provider_id) for row in pending_rows if not row.provider_income]
            zero_profiles = [profile for profile in zero_profiles if profile is not None]
            if zero_profiles:
                from wallet.refund_recovery_order import tracking_enabled
                from wallet.models import RefundDepositRecoveryPermit
                from .models import OrderAddonPayment
                if (tracking_enabled()
                        and not OrderAddonPayment.objects.filter(intent__order=order).exists()
                        and RefundDepositRecoveryPermit.objects.filter(
                            order=order, closed_at__isnull=True,
                            profile_id__in=[profile.pk for profile in zero_profiles],
                        ).exists()):
                    from wallet.settlement_clocks import require_clock
                    zero_service_clock = require_clock()
        now = zero_service_clock.at if zero_service_clock is not None else timezone.now()
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
            hold_provider_income(income_tx, profile=profiles.get(row.provider_id),
                hold_hours=0 if order.service_clock_version is not None else order.income_hold_hours,
                deposit_percent=order.service_deposit_percent, apply_without_hold=order.service_clock_version is not None)
            row.settled_at = now
            row.save(update_fields=['settled_at'])
            if not row.provider_income:
                from wallet.refund_recovery_order import close_for_zero_service
                if zero_service_clock is not None:
                    close_for_zero_service(profiles.get(row.provider_id), row, now=zero_service_clock)
                else:
                    close_for_zero_service(profiles.get(row.provider_id), row)
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
        hold_provider_income(income_tx, profile=profiles.get(order.provider_id),
            hold_hours=0 if order.service_clock_version is not None else order.income_hold_hours,
            deposit_percent=order.service_deposit_percent, apply_without_hold=order.service_clock_version is not None)
        settled_provider_ids.append(order.provider_id)
        if order.provider_account_id:
            settled_account_ids.append(order.provider_account_id)

    if order.inviter_id or order.inviter_account_id:
        inviter_tx = _credit_wallet(
            order=order,
            amount=order.inviter_commission,
            tx_type=Transaction.TxType.INCOME,
            remark=f'推荐分佣：{order.order_no}',
            event='order.settle.inviter',
            trace_id=trace_id,
            user_id=order.inviter_id,
            account_id=order.inviter_account_id,
        )
        if inviter_tx:
            from .referral_evidence import record_referral_principal
            record_referral_principal(inviter_tx)
            transaction.on_commit(lambda: notify_wallet_update(inviter_tx.wallet))

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
    order.settled_at = zero_service_clock.at if zero_service_clock is not None else timezone.now()
    order.save(update_fields=['settled_at'])
    from engagement.services import award_order_points
    award_order_points(order)
    return settled_provider_ids


# ---------------------------------------------------------------------------
# 退款
# ---------------------------------------------------------------------------


def ensure_fulfillment_reports(order):
    """创建每个参与人的报单，金额取该参与人的分账快照。调用方持有订单锁。"""
    from .points_service_payment import needs_integrated_processing
    from .models import OrderOriginalShareTransfer
    if needs_integrated_processing(order) and OrderOriginalShareTransfer.objects.filter(
            previous_segment__attribution__order=order).exists():
        from .segment_reports import enabled as segment_reports_enabled
        from .points_transfer_capture import enabled as points_transfer_enabled
        if not segment_reports_enabled() or not points_transfer_enabled():
            raise SettlementError('积分转单逐段报单尚未开放')
        return
    rows = list(order.providers.all())
    # 极小金额均分可能产生 0 基数，仍在服务的成员也必须提供履约凭证。
    funded_ids = set()
    if getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False):
        from .models import OrderPlatformSubsidyShare
        from .subsidies import released_before_service
        shares = OrderPlatformSubsidyShare.objects.filter(subsidy__order=order).select_related('assignment')
        funded_ids = {share.assignment_id for share in shares
                      if share.amount > 0 or (share.assignment.released_at
                                              and not released_before_service(order, share.assignment))}
    participants = [
        (r.provider_id, r.provider_account_id, r.settlement_base, r.commission_rate)
        for r in rows if r.settlement_base > 0 or not r.released_at or r.pk in funded_ids
    ]
    if not rows and order.provider_id:
        participants = [(order.provider_id, order.provider_account_id, order.amount, order.commission_rate)]
    points_order = needs_integrated_processing(order)
    for user_id, account_id, amount, rate in participants:
        if not user_id:
            continue
        if points_order:
            from .points_funding_plan import PointsFundingPlanError
            from .points_report_test import verified_report_snapshot
            try:
                snapshot = verified_report_snapshot(order, account_id, user_id)
            except PointsFundingPlanError as exc:
                raise SettlementError('积分订单报单收益快照待核对') from exc
            amount, rate = snapshot.incoming_basis, snapshot.commission_rate
        report, created = ProviderReport.objects.get_or_create(
            provider_id=user_id, order=order,
            defaults={
                'provider_account_id': account_id, 'amount': amount,
                'game_name': order.service_name_snapshot or order.service.name,
                'description': order.remark, 'status': ProviderReport.Status.DRAFT,
                'commission_rate': rate,
            },
        )
        if points_order and not created and (report.provider_account_id != account_id or report.amount != amount
                                             or report.commission_rate != rate):
            raise SettlementError('积分订单报单归属或金额待核对')
        if report.status in (ProviderReport.Status.DRAFT, ProviderReport.Status.REJECTED):
            report.amount = amount
            report.commission_rate = rate
            report.save(update_fields=['amount', 'commission_rate', 'updated_at'])
        if created:
            from .notifier import notify_report_update
            transaction.on_commit(lambda report=report: notify_report_update(report))


def complete_fulfillment(order, *, operator=None, operator_account=None):
    """完成服务释放接单名额；新单等待全体报单通过，旧单继续原结算方式。"""
    from .refunds import negotiated_aftersale_blocks_completion
    if negotiated_aftersale_blocks_completion(order):
        from .state_machine import IllegalTransitionError
        raise IllegalTransitionError('售后处理中，暂不能结单，请等待客服处理')
    if order.is_gift_snapshot and order.settlement_mode == Order.SettlementMode.COMPLETION:
        settled = settle_order(order, operator=operator, operator_account=operator_account)
        ensure_fulfillment_reports(order)
        if not order.settled_at:
            ids = list(order.providers.values_list('provider_id', flat=True)) or [order.provider_id]
            refresh_escort_status(provider_ids=[pk for pk in ids if pk], exclude_order_ids=[order.pk])
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


def report_approval_evidence_error(order, report):
    if order.settlement_mode != Order.SettlementMode.REPORT_APPROVAL and not settings.REQUIRE_ORDER_EVIDENCE_IMAGES:
        return ''
    missing = missing_report_evidence(report)
    return '报单缺少：' + '、'.join(missing) if missing else ''


def required_report_providers(order):
    rows = list(order.providers.all())
    funded_ids = set()
    if rows and getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False):
        from .models import OrderPlatformSubsidyShare
        from .subsidies import released_before_service
        shares = OrderPlatformSubsidyShare.objects.filter(subsidy__order=order).select_related('assignment')
        funded_ids = {share.assignment_id for share in shares
                      if share.amount > 0 or (share.assignment.released_at
                                              and not released_before_service(order, share.assignment))}
    from .segment_financial_capture import retained_transfer_accounts
    retained_accounts = retained_transfer_accounts(order)
    return {r.provider_id for r in rows if r.settlement_base > 0 or not r.released_at or r.pk in funded_ids
            or r.provider_account_id in retained_accounts} if rows else {order.provider_id}


def report_edit_error(report, *, points_evidence=None):
    # Serializers can ask outside an operation transaction; use a short
    # order-first read scope for the same verified result as report actions.
    if (report.order_id and getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
            and not transaction.get_connection().in_atomic_block):
        from .points_business_access import report_scope
        from rest_framework.exceptions import ValidationError
        try:
            with transaction.atomic(), report_scope(report.pk):
                return _report_edit_error(report, points_evidence=points_evidence)
        except ValidationError:
            return '积分订单报单或收益快照待核对'
    return _report_edit_error(report, points_evidence=points_evidence)


def _report_edit_error(report, *, points_evidence=None):
    if report.status not in (ProviderReport.Status.DRAFT, ProviderReport.Status.REJECTED):
        return '当前报单不可修改凭证'
    if not report.order_id:
        return ''
    order = report.order
    from .points_service_payment import needs_integrated_processing
    if (needs_integrated_processing(order) if points_evidence is None else points_evidence):
        from .points_funding_plan import PointsFundingPlanError
        from .points_report_test import verified_report_snapshot
        try:
            snapshot = verified_report_snapshot(order, report.provider_account_id, report.provider_id)
        except PointsFundingPlanError:
            return '积分订单报单或收益快照待核对'
        if report.amount != snapshot.incoming_basis or report.commission_rate != snapshot.commission_rate:
            return '积分订单报单金额或抽成快照待核对'
    if order.status not in (Order.Status.GRABBED, Order.Status.IN_SERVICE, Order.Status.COMPLETED):
        return '订单已取消、退款或已回到待接单池，报单流程已停止'
    if order.payment_status != Order.PaymentStatus.PAID:
        from .negotiation_settlement import report_allowed
        try:
            if not report_allowed(order, report):
                return '订单已取消、退款或已回到待接单池，报单流程已停止'
        except SettlementError:
            return '协商退款凭证待核对，请联系客服'
    if report.provider_id not in required_report_providers(order):
        return '本人已退出服务且无结算份额，无需提交报单'
    return ''


def approve_order_report(report_id, *, operator, operator_account):
    lifecycle = None
    if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
        from club_accounts.provider_lifecycle import ProviderLifecycleError
        from engagement.settlement_lifecycle import capture
        order_id = ProviderReport.objects.filter(pk=report_id).values_list('order_id', flat=True).first()
        if not order_id:
            raise SettlementError('报单订单不存在')
        try:
            lifecycle = capture(order_id)
        except (ProviderLifecycleError, ValueError) as exc:
            raise SettlementError('结算陪玩账号待核对') from exc
    from .points_business_access import report_scope
    with transaction.atomic():
        if lifecycle is not None:
            from engagement.settlement_lifecycle import lock
            try:
                lock(lifecycle)
            except ProviderLifecycleError as exc:
                raise SettlementError('结算陪玩账号待核对') from exc
        with report_scope(report_id):
            return _approve_order_report(report_id, operator=operator, operator_account=operator_account,
                                         lifecycle=lifecycle)


def _approve_order_report(report_id, *, operator, operator_account, lifecycle=None):
    initial = ProviderReport.objects.only('order_id').get(pk=report_id)
    # 所有入口统一先锁订单、再锁报单，双陪同时审核不会互相反向等待。
    order = Order.objects.select_for_update().get(pk=initial.order_id)
    if lifecycle is not None:
        from engagement.settlement_lifecycle import verify
        try:
            verify(order.pk, lifecycle)
        except ValueError as exc:
            raise SettlementError('结算参与人或账号周期已变化，请重试') from exc
    report = ProviderReport.objects.select_for_update().get(pk=report_id)
    from .points_service_payment import needs_integrated_processing
    points_order = needs_integrated_processing(order)
    snapshot = None
    if points_order:
        from .points_funding_plan import PointsFundingPlanError
        from .points_report_test import verified_report_snapshot
        try:
            snapshot = verified_report_snapshot(order, report.provider_account_id, report.provider_id)
        except PointsFundingPlanError as exc:
            raise SettlementError('积分订单报单或收益快照待核对') from exc
        if report.amount != snapshot.incoming_basis or report.commission_rate != snapshot.commission_rate:
            raise SettlementError('积分订单报单金额或抽成快照待核对')
    if report.status != ProviderReport.Status.PENDING:
        raise SettlementError('该报单不是待审核状态，无法重复操作')
    if order.status != Order.Status.COMPLETED:
        raise SettlementError('订单未完成或已退款，不能通过报单')
    if order.payment_status != Order.PaymentStatus.PAID:
        from .negotiation_settlement import report_allowed
        if not report_allowed(order, report):
            raise SettlementError('订单未完成或已退款，不能通过报单')
    from .segment_financial_capture import transfer_income_pending
    if transfer_income_pending(order, report.provider_account_id):
        raise SettlementError('转单收益尚未完成逐段核对，暂不能审核')
    evidence_error = report_approval_evidence_error(order, report)
    if evidence_error:
        raise SettlementError(evidence_error)
    own_share = order.providers.filter(provider_id=report.provider_id).first()
    if not own_share and order.provider_id != report.provider_id:
        raise SettlementError('报单人不属于该订单')
    from .service_settlement import (
        legacy_report_clock_chain_enabled, legacy_report_review_subject_id,
    )
    clock = None
    review_subject_id = None
    if legacy_report_clock_chain_enabled(order):
        from wallet.settlement_clocks import require_clock
        clock = require_clock()
        review_subject_id = legacy_report_review_subject_id(
            order, report, clock, approved=True,
        )
    report.status = ProviderReport.Status.APPROVED
    report.commission_rate = snapshot.commission_rate if snapshot else own_share.commission_rate if own_share else order.commission_rate
    if points_order and order.refund_records.exists():
        from .negotiation_settlement import report_income
        negotiated, retained = report_income(report)
        if not negotiated or retained is None:
            raise SettlementError('积分协商后的保留收益待核对')
        report.payout_amount = retained
    else:
        report.payout_amount = snapshot.income_at_entry if snapshot else own_share.provider_income if own_share else order.provider_income
    report.auditor = operator
    report.auditor_account = operator_account
    report.audited_at = clock.at if clock is not None else timezone.now()
    report.save(update_fields=['status', 'commission_rate', 'payout_amount', 'auditor', 'auditor_account', 'audited_at'])
    if clock is not None:
        from wallet.settlement_clocks import record
        record(order, 'SUBMISSION', review_subject_id, 'REVIEW', clock)
    # Direct payment has no trusted channel settlement integration yet. Keep
    # the verified report even when the normal settlement window has passed;
    # the isolated payment path must not credit wallets or promise payout.
    from .direct_payment_models import DirectPaymentIntent
    is_direct_payment = DirectPaymentIntent.objects.filter(order_id=order.pk).exists()
    settlement_failed = False
    if not order.is_gift_snapshot and not order.settled_at:
        provider_ids = required_report_providers(order)
        approved_ids = set(order.provider_reports.filter(status=ProviderReport.Status.APPROVED).values_list('provider_id', flat=True))
        from engagement.confirmation import aftersale_wait_elapsed, settlement_ready, service_reports_mature, refresh_report_clock, notify_committed_order
        previous_clock = (order.report_ready_at, order.consumption_ready_at)
        refresh_report_clock(order)
        # Match the maturity task: an old order without its customer account
        # stays available for review, regardless of when its report is approved.
        from .service_settlement import supports_partial_service_settlement
        partial = supports_partial_service_settlement(order)
        from .points_checkout_test import ordinary_refund_production_order
        negotiated_partial = bool(not points_order and partial and ordinary_refund_production_order(order))
        def settle_reviewed_order():
            if clock is not None:
                from wallet.settlement_clocks import clock_scope
                with clock_scope(clock):
                    return settle_order(order, operator=operator, operator_account=operator_account)
            return settle_order(order, operator=operator, operator_account=operator_account)

        if points_order:
            from .points_checkout_test import settlement_enabled
            if settlement_enabled():
                try:
                    with transaction.atomic():
                        settle_reviewed_order()
                except (SettlementError, WalletAddressingError, ValueError):
                    settlement_failed = True
                    order.refresh_from_db()
        if (not points_order and not is_direct_payment and order.customer_account_id
                and ((aftersale_wait_elapsed(order, clock=clock) or negotiated_partial) if partial
                     else settlement_ready(order, clock=clock))
                and (partial or (provider_ids.issubset(approved_ids)
                                 and service_reports_mature(order, clock=clock)))):
            paid = []
            if partial:
                try:
                    with transaction.atomic():
                        paid = settle_reviewed_order()
                except (SettlementError, WalletAddressingError):
                    # A bad or underfunded share must not erase the review of
                    # another participant. Reconcile before retrying settlement.
                    settlement_failed = True
                    order.refresh_from_db()
            else:
                paid = settle_reviewed_order()
            if (paid or order.settled_at) and previous_clock == (order.report_ready_at, order.consumption_ready_at):
                notify_committed_order(order.pk)
    from wallet.settlement_display import legacy_customer_settlement_reason
    needs_review = points_order or is_direct_payment or settlement_failed or bool(legacy_customer_settlement_reason(order))
    own_share_settled = bool(order.settled_at or order.providers.filter(
        provider_id=report.provider_id, settled_at__isnull=False).exists())
    from site_messages.order_cycle import recipient_cycle
    report_cycle = recipient_cycle(order, report.provider_account_id)
    report_preview = ('凭证已通过，收益待核对，请联系客服' if needs_review else
        '凭证已通过，结算结果请查看钱包流水。' if own_share_settled else '凭证已通过，报单审核及适用等待期结束后自动结算')
    from site_messages.report_approval_notice import enabled as report_notice_enabled, record as record_report_notice
    if report_notice_enabled():
        record_report_notice(report, order, report_cycle, report_preview)
    else:
        transaction.on_commit(lambda: push_message_safe(
            recipient_id=report.provider_id, recipient_account_id=report.provider_account_id, title='报单审核通过',
            preview=report_preview, msg_type='ORDER', related_order_id=order.pk, action_url='/pages/report/index',
            recipient_business_cycle=report_cycle, require_cycle=True,
        ))
    from .notifier import notify_report_update
    transaction.on_commit(lambda: notify_report_update(report))
    return report

@transaction.atomic
def refund_to_customer(order, reason='订单退款', *, trace_id=None, operator=None, operator_account=None,
                       allow_settled_recovery=False, plan_token=None, refund_amount=None, client_request_id=None,
                       negotiated=None):
    """Refund an authorized amount; caller holds the order and saves its state."""
    from .financial_lock import lock_order_money
    from .refunds import (PAID_STATES, posted_settlement, recover_settlement, authorized_refund,
                          refunded_amount, parse_refund_amount, replay_refund, request_fingerprint, RefundError,
                          reject_zero_negotiation_refund, reject_additional_negotiation_refund)
    from .models import OrderRefund
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order):
        raise RefundError('积分订单退款处理尚未开放，请联系客服核对', code='POINTS_REFUND_UNAVAILABLE')
    reject_zero_negotiation_refund(order)
    reject_additional_negotiation_refund(order)
    allocation = negotiated['allocation'] if negotiated is not None else None
    intent = negotiated['intent'] if negotiated is not None else None
    existing = replay_refund(order,client_request_id,refund_amount,reason,intent)
    if existing:
        from django.core.exceptions import ValidationError as DjangoValidationError
        from .referral_refund_funding import verify_replay
        try:
            verify_replay(existing)
        except DjangoValidationError as exc:
            raise RefundError('推荐佣金退款凭据不一致，请先核对', code='REFUND_LEDGER_INCOMPLETE') from exc
        order._refund_replayed = True
        return existing
    from .segment_settlement import reject_legacy_refund
    reject_legacy_refund(order)
    if any(case.affected_provider_account_ids for case in order.aftersales.filter(status='OPEN')):
        raise RefundError('指定陪玩的售后退款分摊尚待核对，暂不能执行退款', code='REFUND_SCOPED_ALLOCATION_REQUIRED')
    if order.in_service_at:
        from .models import OrderPlatformSubsidy
        if OrderPlatformSubsidy.objects.filter(order_id=order.pk).exists():
            raise SettlementError('安全箱订单已开始服务，须先按协商结果核算陪玩报酬；该处理流程尚未开放')
    if order.payment_status not in PAID_STATES:
        # Ordinary cancellation can still close an unpaid legacy order.
        return None
    # Direct-payment refunds need a distinct channel state machine. Until that
    # exists, no legacy route may credit a WeChat payment to a local wallet.
    from .direct_payment_models import DirectPaymentIntent
    if DirectPaymentIntent.objects.filter(order_id=order.pk).exists():
        raise SettlementError('微信支付订单暂不支持此退款入口，请联系客服核对')
    lock_order_money()
    # A competing negotiated operation may have committed while this request
    # was waiting for the shared money lock.  Never reuse a pre-lock snapshot.
    reject_zero_negotiation_refund(order)
    reject_additional_negotiation_refund(order)
    prior = refunded_amount(order)
    # A fully discounted paid order can still be cancelled. There is no cash
    # to refund, but the order's consumed coupon and other rights must unwind.
    zero_payment = order.amount == 0 and prior == 0 and refund_amount is None
    amount = 0 if zero_payment else parse_refund_amount(refund_amount,order.amount-prior)
    if amount <= 0 and not zero_payment:
        raise RefundError('订单已无可退余款',code='REFUND_INVALID_AMOUNT')
    settled = posted_settlement(order)
    if zero_payment and settled:
        raise SettlementError('已结算的零支付订单请联系客服核对')
    full = prior+amount == order.amount
    if not full and negotiated is None and (not settled or order.status != 'COMPLETED'):
        raise RefundError('仅已结算完成订单支持部分退款',code='REFUND_PARTIAL_UNSETTLED')
    if not full and not client_request_id:
        raise RefundError('部分退款须提供操作凭证',code='REFUND_INVALID_REQUEST')
    if settled and not (allow_settled_recovery and authorized_refund(operator_account)):
        raise SettlementError('已结算订单仅可由有退款权限的后台核算追回')
    from engagement.confirmation import refund_within_aftersale_window
    if not refund_within_aftersale_window(order):
        raise SettlementError('售后期限已结束或截止时间无法核实，不能新建退款')
    if not settled and order.consumption_confirmed_at:
        raise SettlementError('订单已确认实销但结算记录不完整，请核对后处理')
    # Receipt precedes customer/provider wallets in the shared funding lock order.
    from .referral_refund_funding import prelock as prelock_referral_funding
    from django.core.exceptions import ValidationError as DjangoValidationError
    try:
        prelock_referral_funding(order)
    except DjangoValidationError as exc:
        raise RefundError('推荐佣金退款到账凭据待核对', code='REFUND_REFERRAL_STORE_FUNDING_REQUIRED') from exc
    trace_id = trace_id or new_trace_id()
    customer_wallet = get_wallet(user_id=order.customer_id,account_id=order.customer_account_id,for_update=True) if amount else None
    # Refunds may downgrade VIP even when the optional transition switch is
    # off. Keep the existing wallet -> configuration -> coupon lock order so
    # current earned coupon terms can be preserved before the grade changes.
    from engagement.models import ClubOperationConfig
    from engagement.services import config
    config()
    vip_operation = ClubOperationConfig.objects.select_for_update().get(pk=1)
    refund = OrderRefund.objects.create(order=order,amount=amount,reason=reason,operator=operator,operator_account=operator_account,
        client_request_id=client_request_id or None,request_fingerprint=request_fingerprint(amount,reason,intent),
        negotiated_allocation=allocation or [])
    from .original_attribution import mark_review
    mark_review(order, '退款后的个人原价份额尚未核对')
    changed_wallets = [customer_wallet] if customer_wallet else []
    if settled:
        changed_wallets += recover_settlement(order,refund,plan_token=plan_token,operator=operator,operator_account=operator_account,
                                              negotiated=negotiated)
        customer_wallet.refresh_from_db()
    if customer_wallet:
        before = customer_wallet.balance
        customer_wallet.balance += amount
        customer_wallet.save(update_fields=['balance'])
        customer_transaction = Transaction.objects.create(wallet=customer_wallet,order=order,amount=amount,tx_type=Transaction.TxType.REFUND,
            balance_before=before,balance_after=customer_wallet.balance,remark=f'{reason}：{order.order_no}',
            operator=operator,operator_account=operator_account)
        refund.customer_transaction = customer_transaction
        refund.save(update_fields=['customer_transaction'])
        log_fields = dict(account_id=order.customer_account_id, user_id=order.customer_id,
            order_no=order.order_no, order_id=order.pk, tx_type=Transaction.TxType.REFUND, amount=amount,
            balance_before=before, balance_after=customer_wallet.balance, trace_id=trace_id, reason=reason)
        transaction.on_commit(lambda: log_money_event('order.refund', **log_fields), robust=True)
    order.payment_status = Order.PaymentStatus.REFUNDED if full else Order.PaymentStatus.PARTIALLY_REFUNDED
    order.refunded_at = timezone.now()
    from engagement.services import reverse_order_points, release_order_allocations
    if order.funding_terms is not None and amount:
        from engagement.funding_wallet import refund_locked_order_funding
        refund_locked_order_funding(order, customer_wallet, amount)
    if order.funding_terms is None and amount and not order.is_gift_snapshot:
        from engagement.default_funding_refunds import refund_default_sources
        refund_default_sources(order, amount)
    reverse_order_points(order,refund=refund if amount else None)
    if order.funding_terms is None and (not amount or order.is_gift_snapshot):
        release_order_allocations(order,refund=refund if amount else None)
    coupon_results = []
    if full:
        from coupons.benefits import restore_order_coupons
        from coupons.models import UserCoupon
        # Capture the outcome before restoration removes the order link.
        used_coupons = list(UserCoupon.objects.select_for_update().filter(
            order=order, status=UserCoupon.Status.USED,
        ).select_related('coupon').order_by('pk'))
        restore_order_coupons(order)
        coupon_results = [{'name': row.coupon.name,
                           'outcome': 'RETURNED' if row.restore_on_refund else 'NOT_RETURNED'}
                          for row in used_coupons]
    elif negotiated is not None:
        # A negotiated partial refund terminates the order; record the used
        # coupons' final outcome so the customer can verify it on the receipt.
        from coupons.models import UserCoupon
        used_coupons = UserCoupon.objects.select_for_update().filter(
            order=order, status=UserCoupon.Status.USED,
        ).select_related('coupon').order_by('pk')
        coupon_results = [{'name': row.coupon.name, 'outcome': 'NOT_RETURNED'} for row in used_coupons]
    from engagement.refunds import reverse_refund_benefits
    refund.benefit_effects = reverse_refund_benefits(order,refund,operator_account, vip_operation=vip_operation)
    if coupon_results:
        refund.benefit_effects['order_coupon_results'] = coupon_results
    refund.save(update_fields=['benefit_effects','recovered_amount','plan_fingerprint'])
    for wallet in {row.pk:row for row in changed_wallets}.values():
        transaction.on_commit(lambda wallet=wallet:notify_wallet_update(wallet))
    # Refunds also change the settlement display of approved reports. Refresh
    # every related report after commit, including for report-only reviewers.
    from .notifier import notify_report_update
    for report in ProviderReport.objects.filter(order=order):
        transaction.on_commit(lambda report=report: notify_report_update(report))
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
        if not order.auto_cancel_at:
            return
        from math import ceil
        from .tasks import auto_cancel_pending_order
        auto_cancel_pending_order.apply_async(
            args=[order.id],
            countdown=max(0, ceil((order.auto_cancel_at - timezone.now()).total_seconds())),
        )
    except Exception:
        return


def pending_timeout_deadline(from_time=None, minutes=PENDING_TIMEOUT_MINUTES):
    """返回 PENDING 订单的自动取消时间点。"""
    base = from_time or timezone.now()
    return base + timedelta(minutes=minutes)


def push_message_safe(*, recipient_id=None, recipient_account_id=None, title, preview='', detail='',
                      msg_type='SYSTEM', action_url='', related_order_id=None, assignment_alert=False,
                      recipient_business_cycle=None, require_cycle=False):
    """安全推送站内消息：站内信模块异常时静默失败，绝不影响主流程。"""
    if create_message is None or (recipient_id is None and recipient_account_id is None):
        return
    if related_order_id and not action_url:
        action_url = f'/pages/orderList/index?orderId={related_order_id}'
    try:
        writer = create_message
        if require_cycle:
            from site_messages.utils import create_cycle_message
            writer = create_cycle_message
        writer(
            recipient_id=recipient_id,
            recipient_account_id=recipient_account_id,
            recipient_business_cycle=recipient_business_cycle,
            title=title,
            preview=preview,
            detail=detail,
            msg_type=msg_type,
            action_url=action_url,
            related_order_id=related_order_id,
            assignment_alert=assignment_alert,
        )
    except Exception:
        return
