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
全链路内部账务单位整数（10 = 1 浪花）。后端不做任何除法，
仅在给运营看的 ``msg`` 文案里 ``/10`` 换算成浪花。
"""

from stores.db import transaction
from stores.context import current_currency_name
from django.db.models import Q, Sum

from common.logging_utils import log_money_event, new_trace_id
from orders.services import count_active_orders, ensure_provider_dispatch_locks, lock_provider_dispatch
from users.models import EscortProfile
from wallet.models import Transaction, get_platform_wallet
from wallet.services import WalletAddressingError, claim_fund_write, get_wallet, peek_fund_write

# 备注字段长度上限，与 ``Transaction.remark`` 保持一致。
REMARK_MAX_LENGTH = 255


def _overview(profile, wallet):
    """退还后的押金概览。"""
    deposit_required = profile.effective_deposit_required
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


def deposit_refund_eligibility(profile, wallet):
    """按当前门店真实订单、售后和收益冻结计算阻断原因，不修改数据。"""
    from orders.models import AfterSale, Order, OrderPlatformSubsidyShare, OrderProvider
    from wallet.models import (IncomeHold, ProviderReport, RefundDepositDeficit,
                               RefundDepositRecoveryPermit)

    account_id, user_id = profile.account_id, profile.user_id
    def owner_query(prefix=''):
        query = Q(pk__in=[])
        if account_id is not None:
            query |= Q(**{f'{prefix}provider_account_id': account_id})
        if user_id is not None:
            query |= Q(**{f'{prefix}provider_id': user_id})
        return query

    active_owner = owner_query()
    # Transferred-out participants still wait for the entire order to end;
    # their service income cannot be settled while another player continues.
    active_owner |= owner_query('providers__')
    active = Order.objects.filter(active_owner, status__in=[Order.Status.GRABBED, Order.Status.IN_SERVICE]).distinct().count()
    from .deposit_reservation_clearance import reservation_clearance
    reservation_evidence = reservation_clearance(profile)
    active_reservations = reservation_evidence['active']
    participant = owner_query('order__') | owner_query('order__providers__')
    from orders.aftersale_scope import applies_to
    aftersales = sum(applies_to(case, account_id) for case in
                     AfterSale.objects.filter(participant, status='OPEN').distinct())

    assigned = owner_query()
    shares = OrderProvider.objects.filter(assigned, Q(settlement_base__gt=0) | Q(provider_income__gt=0), settled_at__isnull=True,
        order__status__in=[Order.Status.COMPLETED, Order.Status.CANCELLED],
        order__payment_status__in=[Order.PaymentStatus.PAID, Order.PaymentStatus.PARTIALLY_REFUNDED]).select_related('order')
    from .deposit_clearance import verified_segment_credit
    from .deposit_points_clearance import verified_initial_person
    share_orders = set()
    for share in shares:
        segmented, amount = verified_segment_credit(share.order, account_id)
        if not segmented:
            initial, person = verified_initial_person(share.order, share)
            if initial:
                segmented, amount = True, person.amount if person else None
        if not segmented or amount is None:
            share_orders.add(share.order_id)
    direct = owner_query()
    legacy_orders = Order.objects.filter(direct, providers__isnull=True, settled_at__isnull=True, amount__gt=0,
        status__in=[Order.Status.COMPLETED, Order.Status.CANCELLED],
        payment_status__in=[Order.PaymentStatus.PAID, Order.PaymentStatus.PARTIALLY_REFUNDED])
    pending_subsidy_orders = OrderPlatformSubsidyShare.objects.filter(
        owner_query('assignment__'),
        paid_at__isnull=True, amount__gt=0,
    ).values('subsidy__order_id')
    pending_orders = Order.objects.filter(Q(pk__in=share_orders) | Q(pk__in=legacy_orders.values('pk'))
                                          | Q(pk__in=pending_subsidy_orders)).count()
    from .deposit_addon_clearance import addon_clearance
    addon_evidence = addon_clearance(profile, wallet)
    pending_reports = ProviderReport.objects.filter(
        owner_query(),
        Q(status__in=[ProviderReport.Status.DRAFT, ProviderReport.Status.PENDING], amount__gt=0)
        | Q(status=ProviderReport.Status.APPROVED, payout_amount__gt=0),
        order__isnull=True, transaction__isnull=True).count()
    holds = IncomeHold.objects.filter(wallet=wallet, released_at__isnull=True, cancelled_at__isnull=True).count() if wallet else 0
    # Existing obligations remain binding even when new recovery orders are
    # disabled. The refund transaction already holds this provider's dispatch
    # and profile locks, shared with deposit recovery and permit creation.
    refund_gap = RefundDepositDeficit.objects.filter(
        profile=profile, account_id=account_id, remaining__gt=0).exists()
    recovery_order = RefundDepositRecoveryPermit.objects.filter(
        profile=profile, account_id=account_id, closed_at__isnull=True).exists()
    reasons = []
    if account_id is None:
        reasons.append('陪玩业务账号归属待核对')
    if not profile.deposit_paid:
        reasons.append('没有已缴押金')
    if active:
        reasons.append(f'仍有 {active} 笔进行中的订单')
    if active_reservations:
        reasons.append(f'仍有 {active_reservations} 笔已确认待完成的预约')
    if aftersales:
        reasons.append(f'仍有 {aftersales} 笔未结束的售后')
    if pending_orders or pending_reports:
        reasons.append(f'仍有 {pending_orders + pending_reports} 笔待结算收益')
    if addon_evidence['pending']:
        reasons.append(f"仍有 {addon_evidence['pending']} 笔待结算加购收益")
    if holds:
        reasons.append(f'仍有 {holds} 笔收益冻结未释放')
    if refund_gap:
        reasons.append('退款押金缺口尚未结清')
    if recovery_order:
        reasons.append('补差订单尚未结清')
    # Do not infer a historical start from the first preview or the refund
    # request: prior business may already have been settled for over a week.
    # An unprovable or not-yet-defined start must never permit a cash movement.
    from .deposit_clearance import refund_wait_state
    if (active or active_reservations or aftersales or pending_orders or pending_reports
            or addon_evidence['pending'] or holds or refund_gap or recovery_order):
        wait = {'status': 'BLOCKED', 'cleared_at': None, 'eligible_at': None}
    elif not profile.deposit_paid:
        wait = {'status': 'NOT_APPLICABLE', 'cleared_at': None, 'eligible_at': None}
    else:
        wait = refund_wait_state(profile, wallet, addon_evidence=addon_evidence, reservation_evidence=reservation_evidence)
    if wait['status'] == 'WAITING':
        reasons.append('业务结清后须等待 7 天')
    elif wait['status'] == 'REVIEW':
        reasons.append('业务结清时间待核对')
    return {'can_refund': not reasons, 'reasons': reasons, 'active_order_count': active,
            'active_reservation_count': active_reservations,
            'open_aftersale_count': aftersales,
            'pending_income_count': pending_orders + pending_reports + addon_evidence['pending'],
            'pending_addon_income_count': addon_evidence['pending'],
            'unreleased_income_count': holds, 'refund_deposit_gap': refund_gap,
            'recovery_order_pending': recovery_order, 'wait_status': wait['status'],
            'cleared_at': wait['cleared_at'], 'eligible_at': wait['eligible_at']}


def preview_deposit_refund(profile):
    """只读预览；最终资格仍在退款事务中重新计算。"""
    from wallet.models import Wallet
    if not profile.account_id:
        return {'can_refund': False, 'reasons': ['陪玩未绑定业务账户']}
    by_account = Wallet.objects.filter(account_id=profile.account_id).first()
    by_user = Wallet.objects.filter(user_id=profile.user_id).first() if profile.user_id else None
    if ((by_account and by_user and by_account.pk != by_user.pk)
            or (by_account and by_account.user_id not in (None, profile.user_id))
            or (by_user and by_user.account_id not in (None, profile.account_id))):
        return {'can_refund': False, 'reasons': ['钱包归属存在冲突，请先核对']}
    return deposit_refund_eligibility(profile, by_account or by_user)


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


def refund_deposit(request, profile_id, amount, reason, client_request_id, *, application_id=None):
    """把陪玩已缴押金退回其钱包。

    Args:
        request: DRF 请求对象，需带 ``account`` / ``legacy_user``（操作人）。
        profile_id: ``EscortProfile`` 主键。
        amount: 退还金额，内部账务单位整数。
        reason: 退还原因，必填，落在两条流水的 remark 上。
        client_request_id: 客户端幂等键，同一退押金操作重试时复用。

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

    identity = EscortProfile.objects.filter(pk=profile_id).values('account_id').first()
    if identity is None:
        return 404, '陪玩不存在', None
    if identity['account_id'] is None:
        return 403, '该陪玩未绑定业务账户', None
    lifecycle_provider = None
    from django.conf import settings
    if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
        from club_accounts.models import ClubAccount
        lifecycle_provider = ClubAccount.objects.filter(pk=identity['account_id'], account_type='PROVIDER').first()
        if lifecycle_provider is None:
            return 409, '陪玩账号归属待核对', None
        from club_accounts.provider_lifecycle import ensure_provider_lifecycle, ProviderLifecycleError
        try:
            ensure_provider_lifecycle(lifecycle_provider)
        except ProviderLifecycleError:
            return 503, '陪玩账号状态暂不可核验，请联系平台', None
    ensure_provider_dispatch_locks([identity['account_id']])
    with transaction.atomic():
        if lifecycle_provider:
            from club_accounts.provider_lifecycle import ProviderLifecycleError, lock_provider_operation
            try:
                lock_provider_operation(lifecycle_provider)
            except ProviderLifecycleError:
                return 503, '陪玩账号状态暂不可核验，请联系平台', None
        # ---- 锁序：业务方在前、平台在后（倒序即死锁）----
        # 生命周期锁（启用时）-> 接单名额锁 -> 陪玩档案 -> 陪玩钱包 -> 平台钱包。
        # 全站涉及平台钱包的事务都必须最后锁它，否则与缴纳、结算等
        # 事务交叉时会形成环。
        lock_provider_dispatch([identity['account_id']])
        try:
            profile = EscortProfile.objects.select_for_update().get(pk=profile_id)
        except EscortProfile.DoesNotExist:
            return 404, '陪玩不存在', None
        if profile.account_id is None:
            return 403, '该陪玩未绑定业务账户', None
        if profile.account_id != identity['account_id']:
            return 409, '陪玩账号已变化，请刷新后重新核对', None
        if lifecycle_provider and not ClubAccount.objects.filter(pk=lifecycle_provider.pk,
                account_type=ClubAccount.AccountType.PROVIDER,
                business_cycle=lifecycle_provider.business_cycle).exists():
            return 409, '陪玩账号周期已变化，请刷新后重新核对', None

        from wallet.models import DepositRefundApplication
        from club_accounts.models import ClubAccount
        current_account = ClubAccount.objects.select_for_update().filter(pk=profile.account_id).first() if application_id else None
        pending_applications = DepositRefundApplication.objects.select_for_update().filter(profile=profile, status='PENDING')
        application = pending_applications.filter(pk=application_id).first() if application_id else None
        if application_id and (not application or application.account_id != profile.account_id
                or not current_account or application.business_cycle != current_account.business_cycle
                or not current_account.license_membership_active or application.amount != amount):
            return 409, '申请已处理或账号周期、金额已变化，请核对申请状态', None
        if not application_id and pending_applications.exists():
            return 409, '该陪玩有待处理退押金申请，请从申请办理', None

        from wallet.deposit_cycle import deposit_cycle
        try:
            deposit_cycle(profile)
        except ValueError as exc:
            return 409, str(exc), None

        deposit_paid = profile.deposit_paid
        if deposit_paid == 0:
            return 400, '该陪玩无已缴押金', None
        if amount > deposit_paid:
            return 400, f'退还金额超过已缴押金 {deposit_paid / 10:g} {current_currency_name()}', None

        try:
            escort_wallet = get_wallet(account=profile.account, user_id=profile.user_id, for_update=True, create=False)
        except WalletAddressingError:
            return 409, '钱包归属存在冲突，请先核对', None
        eligibility = deposit_refund_eligibility(profile, escort_wallet)
        if not eligibility['can_refund']:
            return 409, '；'.join(eligibility['reasons']) + '，暂不能退还押金', eligibility
        if escort_wallet is None:
            escort_wallet = get_wallet(account=profile.account, user_id=profile.user_id, for_update=True)
        from wallet.refund_deposit import MAX_LEDGER_AMOUNT
        if escort_wallet.balance + amount > MAX_LEDGER_AMOUNT:
            return 409, '退还后钱包余额超过系统范围，请先核对', None
        platform_wallet = get_platform_wallet(for_update=True)

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

        deposit_return = Transaction.objects.create(
            wallet=escort_wallet,
            deposit_owner_id=profile.account_id,
            deposit_business_cycle=profile.deposit_business_cycle,
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
            deposit_owner_id=profile.account_id,
            deposit_business_cycle=profile.deposit_business_cycle,
            deposit_pair=deposit_return,
            amount=-amount,
            tx_type=Transaction.TxType.DEPOSIT_INCOME,
            balance_before=platform_before,
            balance_after=platform_wallet.balance,
            status=Transaction.Status.SUCCESS,
            remark=f'退还押金：{profile.display_name} {reason}'[:REMARK_MAX_LENGTH],
            operator=operator,
            operator_account=operator_account,
        )

        from site_messages.deposit_event_notice import record_refund
        record_refund(deposit_return)
        if application is not None:
            from django.utils import timezone
            application.status = 'REFUNDED'
            application.refund_transaction = deposit_return
            application.operator = operator_account
            application.resolution = reason
            application.processed_at = timezone.now()
            application.save(update_fields=['status', 'refund_transaction', 'operator', 'resolution', 'processed_at'])
            from site_messages.deposit_application_notice import refresh_delivered
            refresh_delivered(application)

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
        from wallet.notifier import notify_wallet_update
        transaction.on_commit(lambda: notify_wallet_update(escort_wallet))

    return 0, '押金退还成功', data
