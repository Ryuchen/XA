"""陪玩净收益入账、押金抵扣及到期解冻，调用方必须持有业务事务。"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import IncomeHold, Transaction, Wallet, get_platform_wallet


def hold_provider_income(income_tx, *, profile, hold_hours):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('收益冻结须与收益入账处于同一事务')
    if income_tx is None:
        return None
    from .notifier import notify_wallet_update
    wallet = income_tx.wallet
    # All provider settlement callers pass through here, including zero-hour
    # holds. Publish once after both the credit and any freeze have committed.
    transaction.on_commit(lambda: notify_wallet_update(wallet))
    if hold_hours <= 0:
        return None
    wallet = Wallet.objects.select_for_update().get(pk=income_tx.wallet_id)
    from .club_rules import deposit_income_percent
    percent = deposit_income_percent()
    deposit = 0
    if profile and profile.deposit_mode == 'EARNINGS':
        remaining = max(profile.effective_deposit_required - profile.deposit_paid, 0)
        deposit = min(income_tx.amount * percent // 100, remaining)
    if deposit:
        before = wallet.balance
        wallet.balance -= deposit
        profile.deposit_paid += deposit
        profile.save(update_fields=['deposit_paid'])
        Transaction.objects.create(wallet=wallet, order=income_tx.order, amount=-deposit,
                                   tx_type=Transaction.TxType.DEPOSIT,
                                   balance_before=before, balance_after=wallet.balance,
                                   remark=f'订单净收益抵扣押金（{percent}%，应缴{profile.effective_deposit_required / 100:g}元）')
        platform = get_platform_wallet(for_update=True)
        before_platform = platform.balance
        platform.balance += deposit
        platform.save(update_fields=['balance'])
        Transaction.objects.create(wallet=platform, order=income_tx.order, amount=deposit,
                                   tx_type=Transaction.TxType.DEPOSIT_INCOME,
                                   balance_before=before_platform, balance_after=platform.balance,
                                   remark='订单净收益抵扣押金')
    held = income_tx.amount - deposit
    if held <= 0:
        wallet.save(update_fields=['balance'])
        return None
    before = wallet.balance
    wallet.balance -= held
    wallet.frozen_amount += held
    wallet.save(update_fields=['balance', 'frozen_amount'])
    hold = IncomeHold.objects.create(wallet=wallet, income_transaction=income_tx,
                                    amount=held, deposit_amount=deposit,
                                    release_at=timezone.now() + timedelta(hours=hold_hours))
    Transaction.objects.create(wallet=wallet, order=income_tx.order, amount=-held,
                               tx_type=Transaction.TxType.EARNINGS_FREEZE,
                               balance_before=before, balance_after=wallet.balance,
                               remark=f'订单收益冻结{hold_hours}小时')
    return hold


def release_due_income(*, now=None, limit=500):
    now = now or timezone.now()
    due = list(IncomeHold.objects.filter(released_at__isnull=True, cancelled_at__isnull=True, release_at__lte=now)
               .order_by('wallet_id', 'pk').values_list('pk', 'wallet_id','income_transaction__order_id')[:limit])
    released = 0
    for hold_id, wallet_id, order_id in due:
        with transaction.atomic():
            # The release ledger INSERT checks its order FK. Lock that order
            # before its wallet, matching refund/settlement and avoiding the
            # implicit wallet -> order shared-lock cycle on MySQL.
            if order_id is not None:
                from orders.models import Order
                Order.objects.select_for_update().get(pk=order_id)
            wallet = Wallet.objects.select_for_update().get(pk=wallet_id)
            hold = IncomeHold.objects.select_for_update().get(pk=hold_id)
            if hold.released_at or hold.cancelled_at or hold.release_at > now:
                continue
            remaining = hold.amount-hold.recovered_amount
            if remaining < 0 or wallet.frozen_amount < remaining:
                raise ValueError(f'收益冻结余额不足：hold={hold.pk}')
            before = wallet.balance
            wallet.balance += remaining
            wallet.frozen_amount -= remaining
            wallet.save(update_fields=['balance', 'frozen_amount'])
            Transaction.objects.create(wallet=wallet, order=hold.income_transaction.order,
                                       amount=remaining, tx_type=Transaction.TxType.EARNINGS_RELEASE,
                                       balance_before=before, balance_after=wallet.balance,
                                       remark='订单收益冻结到期解冻')
            hold.released_at = now
            hold.save(update_fields=['released_at'])
            from .notifier import notify_wallet_update
            transaction.on_commit(lambda wallet=wallet: notify_wallet_update(wallet))
            released += 1
    return released
