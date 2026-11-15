"""Store-local notices for recorded cash deposits and wallet returns."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction


def enabled():
    return bool(getattr(settings, 'DEPOSIT_EVENT_NOTICE_RECOVERY_ENABLED', False))


def _amount(value):
    from stores.context import current_currency_name
    whole, fraction = divmod(value, 10)
    return f'{whole}.{fraction} {current_currency_name()}' if fraction else f'{whole} {current_currency_name()}'


def record_topup(topup):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('押金通知须与登记同事务保存')
    from .models import DepositEventNotice
    defaults = {'kind': 'TOPUP', 'account_id': topup.account_id, 'business_cycle': topup.account_business_cycle,
                'preview': f'已登记现金押金 {_amount(topup.amount)}，请查看押金记录。'}
    notice, created = DepositEventNotice.objects.get_or_create(topup=topup, defaults=defaults)
    if not created and any(getattr(notice, key) != value for key, value in defaults.items()):
        raise ValueError('现金押金通知来源不一致')
    transaction.on_commit(lambda pk=notice.pk: deliver(pk), robust=True)


def record_refund(refund):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('押金退还通知须与流水同事务保存')
    from .models import DepositEventNotice
    defaults = {'kind': 'REFUND', 'account_id': refund.deposit_owner_id,
                'business_cycle': refund.deposit_business_cycle,
                'preview': f'押金 {_amount(refund.amount)} 已退回你的钱包，请查看钱包流水。'}
    notice, created = DepositEventNotice.objects.get_or_create(refund=refund, defaults=defaults)
    if not created and any(getattr(notice, key) != value for key, value in defaults.items()):
        raise ValueError('押金退还通知来源不一致')
    transaction.on_commit(lambda pk=notice.pk: deliver(pk), robust=True)


def deliver(notice_id):
    from club_accounts.models import ClubAccount
    from wallet.models import CashDepositTopup, Transaction
    from .models import DepositEventNotice, Message
    from .notifier import notify_messages_after_commit
    initial = DepositEventNotice.objects.filter(pk=notice_id).values('account_id').first()
    if not initial:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=initial['account_id']).first()
        notice = DepositEventNotice.objects.select_for_update().filter(pk=notice_id).first()
        if not notice or notice.status != 'PENDING':
            return
        cycle = notice.business_cycle
        valid = type(cycle) is int and cycle > 0
        if valid and notice.kind == 'TOPUP':
            topup = CashDepositTopup.objects.select_related('profile', 'platform_transaction__wallet__user').filter(pk=notice.topup_id).first()
            tx = topup.platform_transaction if topup else None
            valid = bool(topup and topup.account_id == notice.account_id and topup.profile.account_id == notice.account_id
                and topup.account_business_cycle == cycle and topup.amount > 0
                and topup.paid_after == topup.paid_before + topup.amount and tx
                and tx.tx_type == 'DEPOSIT_INCOME' and tx.status == 'SUCCESS' and tx.amount == topup.amount
                and tx.operator_account_id == topup.operator_id and tx.balance_after == tx.balance_before + tx.amount
                and tx.wallet.user and tx.wallet.user.username == settings.PLATFORM_SYSTEM_USERNAME
                and not tx.reversal_of_id and not tx.reversals.exists())
        elif valid and notice.kind == 'REFUND':
            tx = Transaction.objects.select_related('wallet').filter(pk=notice.refund_id).first()
            pair = Transaction.objects.select_related('wallet__user').filter(deposit_pair_id=notice.refund_id).first()
            valid = bool(tx and tx.deposit_owner_id == notice.account_id and tx.deposit_business_cycle == cycle
                and tx.wallet.account_id == notice.account_id and tx.tx_type == 'DEPOSIT' and tx.status == 'SUCCESS'
                and tx.amount > 0 and tx.balance_after == tx.balance_before + tx.amount
                and not tx.reversal_of_id and not tx.reversals.exists() and pair
                and pair.deposit_owner_id == notice.account_id and pair.deposit_business_cycle == cycle
                and pair.tx_type == 'DEPOSIT_INCOME' and pair.status == 'SUCCESS' and pair.amount == -tx.amount
                and pair.balance_after == pair.balance_before + pair.amount and pair.wallet.user
                and pair.wallet.user.username == settings.PLATFORM_SYSTEM_USERNAME
                and not pair.reversal_of_id and not pair.reversals.exists())
        else:
            valid = False
        if not valid:
            notice.status, notice.note = 'REVIEW', '押金流水或原业务周期待核对'
        elif not account or account.account_type != 'PROVIDER' or not account.license_membership_active or account.business_cycle != cycle:
            notice.status, notice.note = 'SKIPPED', '原账号业务周期已变化'
        else:
            notice.message = Message.objects.create(recipient_account=account, recipient_business_cycle=cycle,
                type='SYSTEM', title='现金押金已登记' if notice.kind == 'TOPUP' else '押金已退回钱包',
                preview=notice.preview, action_url='/pages/wallet/index')
            notice.status = 'SENT'
            notify_messages_after_commit(account.pk, business_cycle=cycle)
        notice.completed_at = timezone.now()
        notice.save(update_fields=['status', 'note', 'message', 'completed_at'])
