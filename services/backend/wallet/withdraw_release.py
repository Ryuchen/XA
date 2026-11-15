"""Return one unpaid withdrawal's own freeze and record its money movement."""
from stores.db import transaction

from .models import Transaction, WithdrawalRelease


def release_locked(withdrawal, wallet, *, actor_kind, actor_account=None, platform_actor_id=None, reason=''):
    """Caller holds the withdrawal and wallet locks in the tenant transaction."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('提现解冻必须在门店资金事务内执行')
    if (actor_kind not in ('OWNER', 'STORE', 'PLATFORM')
            or (actor_kind == 'PLATFORM') != (platform_actor_id is not None)
            or (actor_kind == 'PLATFORM') == (actor_account is not None)
            or (actor_kind == 'OWNER' and actor_account.pk != withdrawal.account_id)
            or not isinstance(reason, str) or len(reason) > 255):
        raise ValueError('提现解冻操作人或原因无效')
    debit = Transaction.objects.select_for_update().filter(pk=withdrawal.transaction_id).first()
    if (not wallet or wallet.account_id != withdrawal.account_id
            or (withdrawal.user_id is not None and wallet.user_id != withdrawal.user_id)
            or not debit or debit.wallet_id != wallet.pk or debit.tx_type != Transaction.TxType.WITHDRAW
            or debit.status != Transaction.Status.PENDING or debit.amount != -withdrawal.amount
            or wallet.frozen_amount < withdrawal.amount or wallet.balance + withdrawal.amount > 2147483647
            or WithdrawalRelease.objects.filter(withdrawal=withdrawal).exists()):
        raise ValueError('提现冻结资金状态异常，请核对')
    balance_before, frozen_before = wallet.balance, wallet.frozen_amount
    wallet.balance += withdrawal.amount
    wallet.frozen_amount -= withdrawal.amount
    wallet.save(update_fields=['balance', 'frozen_amount'])
    debit.status, debit.remark = Transaction.Status.FAILED, '提现未付款，冻结金额已退回'
    debit.save(update_fields=['status', 'remark', 'updated_at'])
    return WithdrawalRelease.objects.create(withdrawal=withdrawal, wallet=wallet, original_transaction=debit,
        amount=withdrawal.amount, balance_before=balance_before, balance_after=wallet.balance,
        frozen_before=frozen_before, frozen_after=wallet.frozen_amount, actor_kind=actor_kind,
        actor_account=actor_account, platform_actor_id=platform_actor_id, reason=reason)
