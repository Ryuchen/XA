"""陪玩净收益入账、押金抵扣及到期解冻，调用方必须持有业务事务。"""

from datetime import timedelta
import logging

from django.conf import settings
from stores.db import transaction
from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from .models import IncomeHold, Transaction, Wallet, get_platform_wallet

PRIORITY_GIFT_FREEZE_REMARK = '礼物收益等待先到期的退款押金补差'


def valid_freeze_transaction(hold):
    freeze = hold.freeze_transaction
    source = hold.income_transaction
    valid = bool(freeze and source and source.wallet_id == hold.wallet_id
        and freeze.wallet_id == hold.wallet_id and freeze.order_id == source.order_id
        and source.status == Transaction.Status.SUCCESS and source.tx_type in (Transaction.TxType.INCOME, Transaction.TxType.GIFT)
        and source.amount >= hold.amount > 0 and not source.reversal_of_id
        and source.balance_after == source.balance_before + source.amount
        and freeze.tx_type == Transaction.TxType.EARNINGS_FREEZE
        and freeze.status == Transaction.Status.SUCCESS and freeze.amount == -hold.amount
        and freeze.pk > source.pk and not freeze.reversal_of_id
        and freeze.balance_after == freeze.balance_before + freeze.amount)
    if not valid:
        return False
    reversals = freeze.reversals.filter(status=Transaction.Status.SUCCESS)
    returned = 0
    for item in reversals:
        if (item.wallet_id != hold.wallet_id or item.order_id != source.order_id
                or item.tx_type != Transaction.TxType.EARNINGS_FREEZE or item.amount <= 0
                or item.balance_after != item.balance_before + item.amount):
            return False
        returned += item.amount
    return returned == hold.recovered_amount


def _provider_business_cycle(wallet):
    if not wallet.account_id:
        return None
    from club_accounts.models import ClubAccount
    return ClubAccount.objects.filter(pk=wallet.account_id, account_type=ClubAccount.AccountType.PROVIDER
        ).values_list('business_cycle', flat=True).first()


def hold_gift_income(income_tx, *, paid_at, aftersale_hours, freeze_hours):
    """Freeze gift income until both payment-based clocks have elapsed."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('礼物收益冻结须与付款处于同一事务')
    if income_tx.amount <= 0:
        return None
    priority_wait = False
    if max(aftersale_hours, freeze_hours) == 0:
        from .refund_deposit import enabled as deficit_enabled, protection_enabled
        if protection_enabled():
            from users.models import EscortProfile
            from .models import RefundDepositDeficit
            profile = EscortProfile.objects.select_for_update().filter(
                user_id=income_tx.wallet.user_id, account_id=income_tx.wallet.account_id).first()
            outstanding = (income_tx.wallet.account_id and RefundDepositDeficit.objects.filter(
                account_id=income_tx.wallet.account_id, remaining__gt=0).exists())
            if outstanding and (not deficit_enabled() or not profile):
                raise ValueError('礼物收益对应的退款押金缺口待处理')
            if deficit_enabled() and profile and profile.account_id:
                wallet = Wallet.objects.select_for_update().get(pk=income_tx.wallet_id)
                if outstanding:
                    from .refund_priority import require_current_priority_read
                    require_current_priority_read()
                    priority_wait = bool(_due_income_candidates(paid_at, 1, wallet_id=wallet.pk))
                if not priority_wait:
                    from .refund_deposit import apply_released_income
                    apply_released_income(source=income_tx, profile=profile, wallet=wallet)
        if not priority_wait:
            return None
    wallet = Wallet.objects.select_for_update().get(pk=income_tx.wallet_id)
    if wallet.balance < income_tx.amount or wallet.frozen_amount + income_tx.amount > 2147483647:
        raise ValueError('礼物收益冻结金额异常')
    before = wallet.balance
    wallet.balance -= income_tx.amount
    wallet.frozen_amount += income_tx.amount
    wallet.save(update_fields=['balance', 'frozen_amount'])
    hold = IncomeHold.objects.create(wallet=wallet, income_transaction=income_tx, amount=income_tx.amount,
        account_business_cycle=_provider_business_cycle(wallet),
        release_at=paid_at + timedelta(hours=max(aftersale_hours, freeze_hours)))
    freeze = Transaction.objects.create(wallet=wallet, order=income_tx.order, amount=-income_tx.amount,
        tx_type=Transaction.TxType.EARNINGS_FREEZE, balance_before=before, balance_after=wallet.balance,
        remark=PRIORITY_GIFT_FREEZE_REMARK if priority_wait else '礼物收益等待售后及冻结期')
    hold.freeze_transaction = freeze
    hold.save(update_fields=['freeze_transaction'])
    from .refund_deposit import enabled as deficit_enabled
    if deficit_enabled():
        from .income_priority_evidence import record_hold
        record_hold(hold)
    return hold


def hold_provider_income(income_tx, *, profile, hold_hours, deposit_percent=None, apply_without_hold=False):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('收益冻结须与收益入账处于同一事务')
    if income_tx is None:
        return None
    from .notifier import notify_wallet_update
    wallet = income_tx.wallet
    # All provider settlement callers pass through here, including zero-hour
    # holds. Publish once after both the credit and any freeze have committed.
    transaction.on_commit(lambda: notify_wallet_update(wallet))
    deficit_repayment = 0
    priority_wait = False
    has_deficit = False
    supported = False
    from .refund_deposit import enabled as deficit_enabled, protection_enabled as deficit_protected
    if deficit_protected() and profile and profile.account_id:
        from .models import RefundDepositDeficit
        has_deficit = RefundDepositDeficit.objects.filter(profile=profile, remaining__gt=0).exists()
        supported = (income_tx.order_id and income_tx.tx_type == Transaction.TxType.INCOME
                     and income_tx.remark.startswith('订单收入：')
                     and income_tx.order.status in (income_tx.order.Status.IN_SERVICE,
                         income_tx.order.Status.COMPLETED, income_tx.order.Status.CANCELLED))
        if has_deficit and (not deficit_enabled() or not supported):
            raise ValueError('退款押金缺口须待该收益可直接释放后补足')
        if deficit_enabled() and hold_hours <= 0 and supported:
            from .refund_deposit import apply_released_income
            wallet = Wallet.objects.select_for_update().get(pk=income_tx.wallet_id)
            if has_deficit:
                from .refund_priority import require_current_priority_read
                require_current_priority_read()
                priority_wait = (income_tx.order.status != income_tx.order.Status.COMPLETED
                                 or bool(_due_income_candidates(timezone.now(), 1, wallet_id=wallet.pk)))
            if not priority_wait and income_tx.order.status == income_tx.order.Status.COMPLETED:
                deficit_repayment = apply_released_income(source=income_tx, profile=profile, wallet=wallet)
    if hold_hours <= 0 and not apply_without_hold and not priority_wait:
        return None
    wallet = Wallet.objects.select_for_update().get(pk=income_tx.wallet_id)
    from .club_rules import deposit_income_percent
    percent = deposit_income_percent() if deposit_percent is None else deposit_percent
    if type(percent) is not int or not 0 <= percent <= 100:
        raise ValueError('订单押金抵扣比例快照无效')
    deposit = 0
    initial_debit = initial_credit = None
    if profile and profile.deposit_mode == 'EARNINGS' and not (has_deficit and (priority_wait or hold_hours > 0)):
        remaining = max(profile.effective_deposit_required - profile.deposit_paid, 0)
        deposit = min((income_tx.amount - deficit_repayment) * percent // 100, remaining)
    if deposit:
        from .deposit_cycle import deposit_cycle
        deposit_cycle(profile, increase=True)
        before = wallet.balance
        wallet.balance -= deposit
        profile.deposit_paid += deposit
        profile.save(update_fields=['deposit_paid', 'deposit_business_cycle'])
        initial_debit = Transaction.objects.create(wallet=wallet, order=income_tx.order, amount=-deposit,
                                   deposit_owner_id=profile.account_id, deposit_business_cycle=profile.deposit_business_cycle,
                                   tx_type=Transaction.TxType.DEPOSIT,
                                   balance_before=before, balance_after=wallet.balance,
                                   remark=f'订单净收益抵扣押金（{percent}%，应缴{profile.effective_deposit_required / 100:g}元）')
        platform = get_platform_wallet(for_update=True)
        before_platform = platform.balance
        platform.balance += deposit
        platform.save(update_fields=['balance'])
        initial_credit = Transaction.objects.create(wallet=platform, order=income_tx.order, amount=deposit,
                                   deposit_pair=initial_debit,
                                   deposit_owner_id=profile.account_id, deposit_business_cycle=profile.deposit_business_cycle,
                                   tx_type=Transaction.TxType.DEPOSIT_INCOME,
                                   balance_before=before_platform, balance_after=platform.balance,
                                   remark='订单净收益抵扣押金')
    held = income_tx.amount - deficit_repayment - deposit
    if hold_hours <= 0 and not priority_wait:
        if deposit:
            wallet.save(update_fields=['balance'])
        return None
    if held <= 0:
        wallet.save(update_fields=['balance'])
        return None
    before = wallet.balance
    wallet.balance -= held
    wallet.frozen_amount += held
    wallet.save(update_fields=['balance', 'frozen_amount'])
    hold = IncomeHold.objects.create(wallet=wallet, income_transaction=income_tx,
                                    amount=held, deposit_amount=deposit,
                                    account_business_cycle=_provider_business_cycle(wallet),
                                    initial_deposit_debit=initial_debit, initial_deposit_credit=initial_credit,
                                    deferred_deposit_percent=(percent if has_deficit and (priority_wait or hold_hours > 0) else None),
                                    release_at=timezone.now() + timedelta(hours=hold_hours))
    freeze = Transaction.objects.create(wallet=wallet, order=income_tx.order, amount=-held,
                               tx_type=Transaction.TxType.EARNINGS_FREEZE,
                               balance_before=before, balance_after=wallet.balance,
                               remark=f'订单收益冻结{hold_hours}小时')
    hold.freeze_transaction = freeze
    hold.save(update_fields=['freeze_transaction'])
    if deficit_enabled() and supported:
        from .income_priority_evidence import record_hold
        record_hold(hold)
    return hold


def _legacy_service_aftersale_waiting(order, now):
    # Legacy completion-based settlement could freeze income immediately.
    # Its recorded freeze end and its original after-sale end are separate.
    if (order.status != order.Status.COMPLETED or order.is_gift_snapshot
            or order.service_clock_version is not None):
        return False
    from engagement.confirmation import aftersale_deadline
    deadline = aftersale_deadline(order)
    return deadline is None or now < deadline


def _due_income_candidates(now, limit, *, wallet_id=None):
    # Direct-payment settlement and trustworthy channel-clearing evidence are
    # not implemented. Exclude all such holds before the limit so they cannot
    # starve ordinary balance-funded or orderless historical income.
    from orders.models import AfterSale, Order
    from orders.aftersale_scope import applies_to
    from .refund_deposit import enabled, protection_enabled
    ordering = ('release_at', 'pk') if protection_enabled() else ('wallet_id', 'pk')
    candidates = IncomeHold.objects.filter(
        Q(income_transaction__order__isnull=True)
        | Q(income_transaction__order__direct_payment__isnull=True),
        released_at__isnull=True, cancelled_at__isnull=True, release_at__lte=now,
    ).annotate(_open_case=Exists(AfterSale.objects.filter(
        order_id=OuterRef('income_transaction__order_id'), status='OPEN'))).exclude(
        income_transaction__order__gift_aftersale_hours__isnull=False, _open_case=True,
    )
    # An unprovable hold remains pending for reconciliation, but must not
    # monopolize the batch and prevent other wallets from being released.
    if wallet_id is None:
        candidates = candidates.exclude(freeze_transaction__isnull=True)
        if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
            candidates = candidates.exclude(wallet__account__account_type='PROVIDER',
                wallet__account__license_membership_active=False).exclude(
                wallet__account__account_type='PROVIDER', account_business_cycle__isnull=True).exclude(
                Q(wallet__account__account_type='PROVIDER')
                & ~Q(account_business_cycle=F('wallet__account__business_cycle')))
    if wallet_id is not None:
        candidates = candidates.filter(wallet_id=wallet_id)
    if protection_enabled():
        from users.models import EscortProfile
        from .models import RefundDepositDeficit
        candidates = candidates.annotate(
            _refund_gap=Exists(RefundDepositDeficit.objects.filter(
                account_id=OuterRef('wallet__account_id'), remaining__gt=0)),
            _refund_profile=Exists(EscortProfile.objects.filter(
                account_id=OuterRef('wallet__account_id'), user_id=OuterRef('wallet__user_id'))))
        if enabled():
            candidates = candidates.exclude(_refund_gap=True, deposit_amount__gt=0,
                initial_deposit_debit__isnull=True).exclude(_refund_gap=True, deposit_amount__gt=0,
                initial_deposit_credit__isnull=True).exclude(
                _refund_gap=True, income_transaction__order__isnull=True).exclude(
                _refund_gap=True, _refund_profile=False).exclude(
                Q(_refund_gap=True, income_transaction__order__is_gift_snapshot=False)
                & ~Q(income_transaction__order__status='COMPLETED'))
        else:
            candidates = candidates.exclude(_refund_gap=True)
    # Scope is stored as a JSON list and can also contain legacy malformed
    # values, so use the same conservative interpretation as settlement.
    # Page before applying the requested limit: an unresolved case must not
    # prevent unrelated players' holds from being considered indefinitely.
    if limit <= 0:
        return []
    result = []
    cursor = None
    checked_wallets = {}
    while len(result) < limit:
        page_query = candidates
        if cursor is not None:
            field = 'release_at' if ordering[0] == 'release_at' else 'wallet_id'
            page_query = page_query.filter(
                Q(**{f'{field}__gt': cursor[0]}) | Q(**{field: cursor[0], 'pk__gt': cursor[1]}))
        page = list(page_query.order_by(*ordering).values_list(
            'pk', 'wallet_id', 'income_transaction__order_id', 'wallet__account_id', 'release_at',
            'amount', 'recovered_amount')[:500])
        if not page:
            break
        cases_by_order = {}
        order_ids = {row[2] for row in page if row[2] is not None}
        waiting_orders = {order.pk for order in Order.objects.filter(pk__in=order_ids).only(
            'status', 'is_gift_snapshot', 'service_clock_version', 'service_aftersale_hours', 'completed_at')
            if _legacy_service_aftersale_waiting(order, now)}
        for case in AfterSale.objects.filter(order_id__in=order_ids, status='OPEN').only(
                'order_id', 'affected_provider_account_ids'):
            cases_by_order.setdefault(case.order_id, []).append(case)
        for hold_id, candidate_wallet_id, order_id, account_id, _, amount, recovered in page:
            if order_id in waiting_orders:
                continue
            if any(applies_to(case, account_id) for case in cases_by_order.get(order_id, ())):
                continue
            if wallet_id is not None:
                if recovered > amount:
                    from rest_framework.exceptions import ValidationError
                    raise ValidationError('待释放收益金额异常，请先核对')
                if recovered == amount:
                    continue
            elif enabled() and account_id and RefundDepositDeficit.objects.filter(
                    account_id=account_id, remaining__gt=0).exists():
                if candidate_wallet_id not in checked_wallets:
                    from .refund_priority import earliest_safe_hold
                    from rest_framework.exceptions import ValidationError
                    try:
                        first, reason = earliest_safe_hold(now, candidate_wallet_id)
                    except ValidationError:
                        first, reason = None, '多笔待释放收益凭证异常，请先核对'
                    checked_wallets[candidate_wallet_id] = (first, reason)
                    if reason:
                        logging.getLogger(__name__).warning('%s wallet_id=%s', reason, candidate_wallet_id)
                first, reason = checked_wallets[candidate_wallet_id]
                # An unresolved preview is only a scheduling hint. Let the
                # locked release path retry same-connection evidence recovery;
                # it still refuses to post unless the final order is proved.
                if not reason and (not first or first[0] != hold_id):
                    continue
            result.append((hold_id, candidate_wallet_id, order_id))
            if len(result) == limit:
                break
        last = page[-1]
        cursor = (last[4] if ordering[0] == 'release_at' else last[1], last[0])
        if len(page) < 500:
            break
    return result


def _direct_income_blocked(order_id):
    # Caller has already locked the order. Intent is next in the lock order.
    from orders.direct_payment_models import DirectPaymentIntent
    return DirectPaymentIntent.objects.select_for_update().filter(order_id=order_id).exists()


def release_due_income(*, now=None, limit=500):
    now = now or timezone.now()
    due = _due_income_candidates(now, limit)
    released = 0
    for hold_id, wallet_id, order_id in due:
        lifecycle_identity = None
        lifecycle_provider = None
        if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
            lifecycle_identity = Wallet.objects.filter(pk=wallet_id).values('account_id', 'user_id',
                'account__account_type', 'user__role').first()
            if not lifecycle_identity:
                continue
            from club_accounts.models import ClubAccount
            is_provider = (lifecycle_identity['account__account_type'] == ClubAccount.AccountType.PROVIDER
                           or (lifecycle_identity['account_id'] is None and lifecycle_identity['user__role'] == 'PROVIDER'))
            if is_provider:
                lifecycle_provider = ClubAccount.objects.filter(pk=lifecycle_identity['account_id'],
                    account_type=ClubAccount.AccountType.PROVIDER).first()
                if not lifecycle_provider:
                    logging.getLogger(__name__).warning('收益钱包陪玩归属待核对 wallet_id=%s', wallet_id)
                    continue
                from club_accounts.provider_lifecycle import ensure_provider_lifecycle, ProviderLifecycleError
                try:
                    ensure_provider_lifecycle(lifecycle_provider)
                except ProviderLifecycleError:
                    logging.getLogger(__name__).warning('收益钱包生命周期锁不可用 wallet_id=%s', wallet_id)
                    continue
        with transaction.atomic():
            if lifecycle_provider:
                from club_accounts.provider_lifecycle import lock_provider_operation
                try:
                    lock_provider_operation(lifecycle_provider)
                except ProviderLifecycleError:
                    logging.getLogger(__name__).warning('收益钱包生命周期锁不可用 wallet_id=%s', wallet_id)
                    continue
                if not ClubAccount.objects.filter(pk=lifecycle_provider.pk,
                        account_type=ClubAccount.AccountType.PROVIDER,
                        license_membership_active=True, business_cycle=lifecycle_provider.business_cycle).exists():
                    logging.getLogger(__name__).warning('收益钱包账号周期已变化 wallet_id=%s', wallet_id)
                    continue
            # The release ledger INSERT checks its order FK. Lock that order
            # before its wallet, matching refund/settlement and avoiding the
            # implicit wallet -> order shared-lock cycle on MySQL.
            if order_id is not None:
                from orders.models import Order
                order = Order.objects.select_for_update().get(pk=order_id)
                # The candidate list is only a hint. Recheck under the order
                # lock before taking the wallet/hold locks. Until real channel
                # settlement exists, even a PAID test intent is not evidence
                # that income may be released.
                if _direct_income_blocked(order_id):
                    continue
                if _legacy_service_aftersale_waiting(order, now):
                    continue
                if order.gift_aftersale_hours is not None and order.aftersales.filter(status='OPEN').exists():
                    continue
            from .refund_deposit import enabled as deficit_enabled, protection_enabled as deficit_protected
            profile = None
            deferred_deposit = IncomeHold.objects.filter(pk=hold_id,
                deferred_deposit_percent__isnull=False).exists()
            if deficit_protected() or deferred_deposit:
                from orders.financial_lock import lock_order_money
                from users.models import EscortProfile
                from .models import RefundDepositDeficit
                lock_order_money()
                identity = Wallet.objects.filter(pk=wallet_id).values('user_id', 'account_id').first()
                if identity:
                    profile = EscortProfile.objects.select_for_update().filter(
                        user_id=identity['user_id'], account_id=identity['account_id']).first()
            wallet = Wallet.objects.select_for_update().get(pk=wallet_id)
            if lifecycle_identity is not None and (wallet.account_id != lifecycle_identity['account_id']
                                                   or wallet.user_id != lifecycle_identity['user_id']):
                logging.getLogger(__name__).warning('收益钱包归属已变化 wallet_id=%s', wallet_id)
                continue
            hold = IncomeHold.objects.select_for_update().get(pk=hold_id)
            if hold.released_at or hold.cancelled_at or hold.release_at > now:
                continue
            if hold.wallet_id != wallet_id or hold.income_transaction.order_id != order_id:
                continue
            if not valid_freeze_transaction(hold):
                logging.getLogger(__name__).warning('冻结收益原流水待核对 hold_id=%s', hold.pk)
                continue
            if lifecycle_provider and hold.account_business_cycle != lifecycle_provider.business_cycle:
                logging.getLogger(__name__).warning('冻结收益所属账号周期待核对 hold_id=%s', hold.pk)
                continue
            if lifecycle_provider and order_id is not None:
                from orders.models import OrderParticipationSegment, OrderProvider
                if (order.is_gift_snapshot and order.provider_account_id == lifecycle_provider.pk
                        and order.gift_provider_business_cycle != hold.account_business_cycle):
                    logging.getLogger(__name__).warning('礼物冻结收益来源周期待核对 hold_id=%s', hold.pk)
                    continue
                seat_cycles = list(OrderProvider.objects.filter(order_id=order_id,
                    provider_account_id=lifecycle_provider.pk).values_list('provider_business_cycle', flat=True))
                segment_cycles = list(OrderParticipationSegment.objects.filter(attribution__order_id=order_id,
                    provider_account_id=lifecycle_provider.pk).values_list('provider_business_cycle', flat=True))
                if (not order.is_gift_snapshot and order.provider_account_id == lifecycle_provider.pk
                        and not seat_cycles and not segment_cycles) or any(
                        cycle != hold.account_business_cycle for cycle in seat_cycles + segment_cycles):
                    logging.getLogger(__name__).warning('冻结收益履约来源周期待核对 hold_id=%s', hold.pk)
                    continue
            if order_id is not None:
                from orders.aftersale_scope import applies_to
                if any(applies_to(case, wallet.account_id) for case in order.aftersales.filter(status='OPEN')):
                    continue
            remaining = hold.amount-hold.recovered_amount
            if remaining < 0 or wallet.frozen_amount < remaining:
                raise ValueError(f'收益冻结余额不足：hold={hold.pk}')
            if deficit_protected() or deferred_deposit:
                outstanding = RefundDepositDeficit.objects.filter(account_id=wallet.account_id, remaining__gt=0).exists() if wallet.account_id else False
                # The unlocked candidate list can race with order changes. A
                # service hold deferred during partial settlement is eligible
                # only after the whole order has completed; gifts use their
                # own payment-based clocks instead.
                if (outstanding and order_id and not order.is_gift_snapshot
                        and order.status != order.Status.COMPLETED):
                    continue
                from .income_priority_evidence import initial_deposit_verified
                if outstanding and (not deficit_enabled() or not profile or not order_id
                                    or not initial_deposit_verified(hold)):
                    # Historical holds with already-deducted ordinary deposit
                    # require reconciliation before applying the new priority.
                    continue
                if outstanding and deficit_enabled():
                    # Other order rows are never locked here. The wallet is
                    # already locked before all hold writers, so these are
                    # current row locks and the following fact reads are RC.
                    due_holds = list(IncomeHold.objects.select_for_update().filter(
                        wallet=wallet, released_at__isnull=True, cancelled_at__isnull=True,
                        release_at__lte=now).order_by('pk').values_list('pk', flat=True)[:2])
                    if len(due_holds) > 1:
                        from .refund_priority import earliest_safe_hold, require_current_priority_read
                        from rest_framework.exceptions import ValidationError
                        try:
                            require_current_priority_read()
                            first, reason = earliest_safe_hold(now, wallet.pk, recover=True)
                        except ValidationError:
                            first, reason = None, '多笔待释放收益的当前顺序无法核对'
                        if reason or not first or first[0] != hold.pk:
                            if reason:
                                logging.getLogger(__name__).warning('%s wallet_id=%s', reason, wallet.pk)
                            continue
            before = wallet.balance
            wallet.balance += remaining
            wallet.frozen_amount -= remaining
            wallet.save(update_fields=['balance', 'frozen_amount'])
            released_tx = Transaction.objects.create(wallet=wallet, order=hold.income_transaction.order,
                                       amount=remaining, tx_type=Transaction.TxType.EARNINGS_RELEASE,
                                       balance_before=before, balance_after=wallet.balance,
                                       remark='订单收益冻结到期解冻')
            deficit_repayment = 0
            if deficit_enabled() and profile and profile.account_id and order_id and remaining:
                from .refund_deposit import apply_released_income
                deficit_repayment = apply_released_income(source=released_tx, profile=profile, wallet=wallet, hold=hold, now=now)
            if hold.deferred_deposit_percent is not None:
                percent = hold.deferred_deposit_percent
                if not profile or type(percent) is not int or not 0 <= percent <= 100:
                    raise ValueError('待处理收益押金比例或陪玩资料异常')
                ordinary = 0
                if profile.deposit_mode == 'EARNINGS':
                    ordinary = min((remaining - deficit_repayment) * percent // 100,
                                   max(profile.effective_deposit_required - profile.deposit_paid, 0))
                if ordinary:
                    from .deposit_cycle import deposit_cycle
                    deposit_cycle(profile, increase=True)
                    if wallet.balance < ordinary:
                        raise ValueError('待处理收益押金余额不足')
                    before = wallet.balance
                    wallet.balance -= ordinary
                    wallet.save(update_fields=['balance'])
                    profile.deposit_paid += ordinary
                    profile.save(update_fields=['deposit_paid', 'deposit_business_cycle'])
                    hold.deferred_deposit_debit = Transaction.objects.create(wallet=wallet, order=hold.income_transaction.order,
                        deposit_owner_id=profile.account_id, deposit_business_cycle=profile.deposit_business_cycle,
                        amount=-ordinary, tx_type=Transaction.TxType.DEPOSIT,
                        balance_before=before, balance_after=wallet.balance,
                        remark=f'订单净收益抵扣押金（{percent}%，延后处理）')
                    platform = get_platform_wallet(for_update=True)
                    before_platform = platform.balance
                    platform.balance += ordinary
                    platform.save(update_fields=['balance'])
                    hold.deferred_deposit_credit = Transaction.objects.create(wallet=platform, order=hold.income_transaction.order,
                        deposit_pair=hold.deferred_deposit_debit,
                        deposit_owner_id=profile.account_id, deposit_business_cycle=profile.deposit_business_cycle,
                        amount=ordinary, tx_type=Transaction.TxType.DEPOSIT_INCOME,
                        balance_before=before_platform, balance_after=platform.balance,
                        remark='订单净收益抵扣押金')
            hold.released_at = now
            hold.release_transaction = released_tx
            hold.save(update_fields=['released_at', 'release_transaction', 'deferred_deposit_debit', 'deferred_deposit_credit'])
            from .notifier import notify_wallet_update
            transaction.on_commit(lambda wallet=wallet: notify_wallet_update(wallet))
            released += 1
    return released
