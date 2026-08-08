"""DP-1 押金退还：运营后台把陪玩已缴押金退回其钱包的**唯一**实现。

为什么单独成文件
----------------
退还是缴纳（``wallet.views.DepositView.post``）的严格镜像操作，两者共用同
一套「四件事同生共死」的账务约束。把它塞进已经 3000 行的 ``console/views.py``
里，下次有人改缴纳侧的口径时不会想到这里还有一份对手戏，两边迟早走散。

账务口径（与 ``Transaction.TxType.DEPOSIT_INCOME`` 上的注释钉死的决定一致）
----------------------------------------------------------------------
退还**沿用缴纳时的两个枚举记负数**，不新开 ``DEPOSIT_REFUND``：

* 陪玩侧：``DEPOSIT`` / ``+amount``（钱回到陪玩钱包）
* 平台侧：``DEPOSIT_INCOME`` / ``-amount``（平台把这笔钱吐出来）

如此恒等式 ``sum(DEPOSIT) == -sum(DEPOSIT_INCOME) == -当前在缴押金总额``
在缴纳与退还之后都成立，对账脚本一条聚合走到底。

金额单位
--------
全链路内部账务单位整数（10 = 1 兴安币）。后端不做任何除法，
仅在给运营看的 ``msg`` 文案里 ``/10`` 换算成兴安币。
"""

from django.db import transaction
from django.db.models import Sum

from common.logging_utils import log_money_event, new_trace_id
from orders.services import count_active_orders
from users.models import EscortProfile
from wallet.models import Transaction, get_platform_wallet
from wallet.services import claim_fund_write, get_wallet, peek_fund_write

# 备注字段长度上限，与 ``Transaction.remark`` 保持一致。
REMARK_MAX_LENGTH = 255


def _overview(profile, wallet):
    """退还后的押金概览。

    字段口径与 ``wallet.views.DepositView._overview`` 完全一致，额外补一个
    ``active_order_count``：陪玩身上还挂着进行中的订单时，运营需要知道
    「这人还在服务中」。按 Q1 的结论这是**软提示**，不硬拦退还
    —— 押金与产能是两码事，硬拦会让「退完押金再下线」变成死循环。
    """
    deposit_required = profile.deposit_required
    deposit_paid = profile.deposit_paid
    return {
        'deposit_required': deposit_required,
        'deposit_paid': deposit_paid,
        'deposit_remaining': max(deposit_required - deposit_paid, 0),
        'balance': wallet.balance,
        'active_order_count': count_active_orders(
            profile.account, provider_id=profile.user_id,
        ),
    }


def _platform_deposit_pool(platform_wallet):
    """平台钱包里**属于押金**的那部分余额。

    平台钱包同时归集抽成（``SHOP_INCOME``）、提现税（``WITHDRAW_TAX``）等
    多种收入，总余额够不代表押金池够。只按 ``DEPOSIT_INCOME`` 聚合，
    避免押金退还悄悄花掉平台的抽成收入。
    """
    total = Transaction.objects.filter(
        wallet=platform_wallet,
        tx_type=Transaction.TxType.DEPOSIT_INCOME,
        status=Transaction.Status.SUCCESS,
    ).aggregate(total=Sum('amount'))['total']
    return total or 0


def refund_deposit(request, profile_id, amount, reason, client_request_id):
    """把陪玩已缴押金退回其钱包。

    Args:
        request: DRF 请求对象，需带 ``account`` / ``legacy_user``（操作人）。
        profile_id: ``EscortProfile`` 主键。
        amount: 退还金额，内部账务单位整数。
        reason: 退还原因，必填，落在两条流水的 remark 上。
        client_request_id: 客户端幂等键，可为空（走服务端派生键降级通道）。

    Returns:
        tuple[int, str, dict | None]: ``(code, msg, data)``；``code == 0``
        表示成功，``data`` 为 :func:`_overview` 的结果。业务错误一律以
        ``code`` 表达（400/403/404/409），由视图层包成 HTTP 200 信封。
    """
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return 400, '金额非法', None
    if amount <= 0:
        return 400, '金额非法', None

    reason = (reason or '').strip()
    if not reason:
        return 400, '请填写退还原因', None

    trace_id = new_trace_id()

    # 只读探测重放：首次退还成功后 deposit_paid 变小，重放同样的金额会先撞上
    # 「退还金额超过已缴押金」返回 400，把「上一笔其实已经退成功了」这个真实
    # 原因盖掉。必须在**状态校验之前**把「这是重放」讲清楚。
    replayed, replay_reason = peek_fund_write(
        account=request.account, request_id=client_request_id,
    )
    if replayed:
        log_money_event(
            'deposit.refund.duplicate',
            account_id=request.account.pk,
            amount=amount,
            trace_id=trace_id,
            idempotent_hit=True,
            reason=replay_reason,
        )
        return 409, replay_reason, None

    with transaction.atomic():
        # ---- 锁序：业务方在前、平台在后（倒序即死锁）----
        # 锁1 陪玩档案 -> 锁2 陪玩钱包 -> 锁3 平台钱包。
        # 全站涉及平台钱包的事务都必须最后锁它，否则与缴纳、结算等
        # 事务交叉时会形成环。
        try:
            profile = EscortProfile.objects.select_for_update().get(pk=profile_id)
        except EscortProfile.DoesNotExist:
            return 404, '陪玩不存在', None
        if profile.account_id is None:
            return 403, '该陪玩未绑定业务账户', None

        escort_wallet = get_wallet(
            account=profile.account, user_id=profile.user_id, for_update=True,
        )
        platform_wallet = get_platform_wallet(for_update=True)

        deposit_paid = profile.deposit_paid
        if deposit_paid == 0:
            return 400, '该陪玩无已缴押金', None
        if amount > deposit_paid:
            return 400, f'退还金额超过已缴押金 {deposit_paid / 10:g} 兴安币', None

        # 护栏①：平台钱包总余额兜底。余额不足还硬退会击穿
        # ``wallet_balance_nonnegative`` 约束，直接 500。
        if platform_wallet.balance < amount:
            return 400, '平台钱包余额不足，无法退还', None
        # 护栏②：押金池专款专用，不得挪用抽成/税费收入。
        if _platform_deposit_pool(platform_wallet) < amount:
            return 400, '平台押金账户余额不足', None

        # 全部校验通过后、动钱之前才占幂等键：校验失败的分支会正常 return，
        # 事务照常提交 —— 提前占键会让运营改正参数后无法用同一个键重试。
        allowed, claim_reason = claim_fund_write(
            account=request.account,
            request_id=client_request_id,
            scope='deposit_refund',
            fingerprint=f'{profile_id}:{amount}',
        )
        if not allowed:
            log_money_event(
                'deposit.refund.duplicate',
                account_id=request.account.pk,
                amount=amount,
                trace_id=trace_id,
                idempotent_hit=True,
                reason=claim_reason,
            )
            return 409, claim_reason, None

        # ---- 四件事同生共死 ----
        # ① 陪玩钱包加钱 ② 已缴押金减少 ③ 陪玩侧 DEPOSIT 正数流水
        # ④ 平台钱包扣钱 + 平台侧 DEPOSIT_INCOME 负数流水
        # 任何一件失败都必须整体回滚，不存在「押金减了钱没到账」的中间态。
        escort_before = escort_wallet.balance
        escort_wallet.balance += amount
        escort_wallet.save(update_fields=['balance'])

        profile.deposit_paid -= amount
        profile.save(update_fields=['deposit_paid'])

        operator = getattr(request, 'legacy_user', None)
        operator_account = getattr(request, 'account', None)

        Transaction.objects.create(
            wallet=escort_wallet,
            amount=amount,
            tx_type=Transaction.TxType.DEPOSIT,
            balance_before=escort_before,
            balance_after=escort_wallet.balance,
            status=Transaction.Status.SUCCESS,
            remark=f'退还押金：{reason}'[:REMARK_MAX_LENGTH],
            operator=operator,
            operator_account=operator_account,
        )

        platform_before = platform_wallet.balance
        platform_wallet.balance -= amount
        platform_wallet.save(update_fields=['balance'])
        Transaction.objects.create(
            wallet=platform_wallet,
            amount=-amount,
            tx_type=Transaction.TxType.DEPOSIT_INCOME,
            balance_before=platform_before,
            balance_after=platform_wallet.balance,
            status=Transaction.Status.SUCCESS,
            remark=f'退还押金：{profile.display_name} {reason}'[:REMARK_MAX_LENGTH],
            operator=operator,
            operator_account=operator_account,
        )

        # 两条日志共享同一个 trace_id，排障时一条链能串起收付两侧。
        log_money_event(
            'deposit.refund',
            account_id=profile.account_id,
            user_id=profile.user_id,
            tx_type=Transaction.TxType.DEPOSIT,
            amount=amount,
            balance_before=escort_before,
            balance_after=escort_wallet.balance,
            trace_id=trace_id,
            operator_id=getattr(operator, 'pk', None),
            reason=reason,
        )
        log_money_event(
            'deposit.platform_debit',
            account_id=profile.account_id,
            tx_type=Transaction.TxType.DEPOSIT_INCOME,
            amount=-amount,
            balance_before=platform_before,
            balance_after=platform_wallet.balance,
            trace_id=trace_id,
            operator_id=getattr(operator, 'pk', None),
            reason=reason,
        )

        data = _overview(profile, escort_wallet)

    return 0, '押金退还成功', data
