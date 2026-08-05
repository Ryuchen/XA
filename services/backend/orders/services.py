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
from django.db.models import F, Q
from django.utils import timezone

from common.logging_utils import log_money_event, new_trace_id
from users.models import EscortProfile
from wallet.models import ProviderReport, Transaction, get_platform_wallet
from wallet.services import get_wallet

from .models import Order
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


# ---------------------------------------------------------------------------
# 陪玩产能与状态
# ---------------------------------------------------------------------------

def count_active_orders(
    account=None, *, account_id=None, provider_id=None, exclude_order_ids=(),
):
    """统计陪玩当前进行中的订单数（已接单 + 服务中）。

    兼容 ``Order.provider_account`` 外键与 ``OrderProvider`` 中间表两种关联，
    因此双陪订单对参与的每个打手都会计入一次。给出多个维度时取并集
    （同一订单只算一次，靠 ``distinct``）—— 历史数据里 account 与 legacy user
    两个维度并非总是齐全，只查一边会漏。

    Args:
        account: ``ClubAccount`` 实例（推荐维度，位置参数兼容旧调用）。
        account_id: ``ClubAccount`` 主键，已有 id 时免去一次对象查询。
        provider_id: 旧 ``CustomUser`` 主键。
        exclude_order_ids: 需要排除的订单 ID。状态机在 ``side_effect`` 里
            调用本函数时，目标订单的新状态尚未落库，必须显式排除自身，
            否则「刚完成的这一单」会把自己算成仍在进行中。

    Returns:
        int: 进行中的订单数；所有维度都为空时返回 0。
    """
    condition = Q()
    if account is not None:
        condition |= Q(provider_account=account) | Q(providers__provider_account=account)
    if account_id is not None:
        condition |= (
            Q(provider_account_id=account_id)
            | Q(providers__provider_account_id=account_id)
        )
    if provider_id is not None:
        condition |= Q(provider_id=provider_id) | Q(providers__provider_id=provider_id)
    if not condition:
        return 0

    queryset = Order.objects.filter(condition, status__in=ACTIVE_ORDER_STATUSES)
    if exclude_order_ids:
        queryset = queryset.exclude(id__in=list(exclude_order_ids))
    return queryset.distinct().count()


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
        provider_ids = [row.provider_id for row in provider_rows if row.provider_id]
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
    trace_id = trace_id or new_trace_id()
    require_order_evidence(order)

    provider_rows = list(order.providers.select_for_update().all())
    pending_rows = [
        row for row in provider_rows if row.provider_id and not row.settled_at
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

    if provider_rows:
        now = timezone.now()
        for row in pending_rows:
            _credit_wallet(
                order=order,
                amount=row.provider_income,
                tx_type=Transaction.TxType.INCOME,
                remark=f'订单收入：{order.order_no}',
                event='order.settle.provider',
                trace_id=trace_id,
                user_id=row.provider_id,
                account_id=row.provider_account_id,
            )
            row.settled_at = now
            row.save(update_fields=['settled_at'])
            settled_provider_ids.append(row.provider_id)
            if row.provider_account_id:
                settled_account_ids.append(row.provider_account_id)
    elif order.provider_id:
        _credit_wallet(
            order=order,
            amount=order.provider_income,
            tx_type=Transaction.TxType.INCOME,
            remark=f'订单收入：{order.order_no}',
            event='order.settle.provider',
            trace_id=trace_id,
            user_id=order.provider_id,
            account_id=order.provider_account_id,
        )
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
    return settled_provider_ids


# ---------------------------------------------------------------------------
# 退款
# ---------------------------------------------------------------------------

def refund_to_customer(order, reason='订单退款', *, trace_id=None):
    """全额退款到客户钱包并写流水。必须在 ``transaction.atomic`` 中调用。

    同时把订单占用的优惠券退回未使用状态 —— 单都退了还扣着券，
    等于用户白白损失一张券。

    Args:
        order: 待退款订单（调用方负责加锁）。
        reason: 流水备注前缀。
        trace_id: 追踪 ID；不传则自动生成。
    """
    trace_id = trace_id or new_trace_id()
    customer_wallet = get_wallet(
        user_id=order.customer_id,
        account_id=order.customer_account_id,
        for_update=True,
    )
    balance_before = customer_wallet.balance
    customer_wallet.balance += order.amount
    customer_wallet.save(update_fields=['balance'])
    Transaction.objects.create(
        wallet=customer_wallet,
        order=order,
        amount=order.amount,
        tx_type=Transaction.TxType.REFUND,
        balance_before=balance_before,
        balance_after=customer_wallet.balance,
        remark=f'{reason}：{order.order_no}',
    )
    log_money_event(
        'order.refund',
        account_id=order.customer_account_id,
        user_id=order.customer_id,
        order_no=order.order_no,
        order_id=order.pk,
        tx_type=Transaction.TxType.REFUND,
        amount=order.amount,
        balance_before=balance_before,
        balance_after=customer_wallet.balance,
        trace_id=trace_id,
        reason=reason,
    )
    order.payment_status = Order.PaymentStatus.REFUNDED
    order.refunded_at = timezone.now()

    from coupons.models import UserCoupon
    UserCoupon.objects.filter(
        order=order, status=UserCoupon.Status.USED,
    ).update(status=UserCoupon.Status.UNUSED, order=None, used_at=None)


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
