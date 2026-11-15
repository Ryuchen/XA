"""Recover the original recharge receipt notice from committed wallet facts."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction


def enabled():
    return bool(getattr(settings, 'RECHARGE_RECEIPT_NOTICE_RECOVERY_ENABLED', False))


def record(recharge, *, gift_pending):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('充值通知须与充值流水同事务保存')
    from .models import RechargeReceiptNotice
    facts = {'account_id': recharge.account_id, 'business_cycle': recharge.account_business_cycle,
             'recharge_tx_id': recharge.recharge_tx_id, 'gift_tx_id': recharge.gift_tx_id,
             'gift_units': recharge.gift_amount, 'gift_pending': gift_pending}
    notice, created = RechargeReceiptNotice.objects.get_or_create(recharge=recharge, defaults=facts)
    if not created and any(getattr(notice, key) != value for key, value in facts.items()):
        raise ValueError('充值通知来源不一致')
    transaction.on_commit(lambda pk=notice.pk: deliver(pk), robust=True)


def deliver(notice_id):
    from club_accounts.models import ClubAccount
    from wallet.models import RechargeRecord, Transaction
    from .models import RechargeReceiptNotice, Message
    from .notifier import notify_messages_after_commit
    hint = RechargeReceiptNotice.objects.filter(pk=notice_id).values('account_id').first()
    if not hint:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=hint['account_id']).first()
        notice = RechargeReceiptNotice.objects.select_for_update().filter(pk=notice_id, account_id=hint['account_id']).first()
        if not notice or notice.status != 'PENDING':
            return
        recharge = RechargeRecord.objects.select_related('recharge_tx__wallet').filter(pk=notice.recharge_id).first()
        tx = recharge.recharge_tx if recharge else None
        cycle = notice.business_cycle
        valid = bool(type(cycle) is int and cycle > 0 and recharge and recharge.account_id == notice.account_id
            and recharge.account_business_cycle == cycle and recharge.amount > 0 and tx and tx.pk == notice.recharge_tx_id
            and tx.wallet.account_id == notice.account_id and tx.tx_type == 'TOPUP' and tx.status == 'SUCCESS'
            and tx.amount == recharge.amount and tx.balance_after == tx.balance_before + tx.amount
            and not tx.reversal_of_id and not tx.reversals.filter(status='SUCCESS').exists())
        if valid and notice.gift_pending:
            valid = bool(not notice.gift_units and not notice.gift_tx_id and hasattr(recharge, 'gift_approval'))
        elif valid:
            gift = Transaction.objects.filter(pk=notice.gift_tx_id).select_related('wallet').first() if notice.gift_tx_id else None
            valid = bool(recharge.gift_amount == notice.gift_units and recharge.gift_tx_id == notice.gift_tx_id
                and ((notice.gift_units == 0 and gift is None) or (gift and gift.wallet_id == tx.wallet_id
                    and gift.tx_type == 'GIFT' and gift.status == 'SUCCESS' and gift.amount == notice.gift_units
                    and gift.balance_after == gift.balance_before + gift.amount and not gift.reversal_of_id
                    and not gift.reversals.filter(status='SUCCESS').exists())))
        if not valid:
            notice.status, notice.note = 'REVIEW', '充值流水或原业务周期待核对'
        elif not account or account.account_type != 'BOSS' or not account.license_membership_active or account.business_cycle != cycle:
            notice.status, notice.note = 'SKIPPED', '原账号业务周期已变化'
        else:
            from stores.context import current_currency_name
            total = recharge.amount + notice.gift_units
            whole, fraction = divmod(total, 10)
            amount = f'{whole}.{fraction}' if fraction else str(whole)
            preview = f'客服确认收款，到账 {amount} {current_currency_name()}'
            if notice.gift_pending:
                preview += '；充值时赠送待审批，请查看当前审批状态'
            notice.message = Message.objects.create(recipient_account=account, recipient_business_cycle=cycle,
                type='SYSTEM', title='充值已到账', preview=preview[:255])
            notice.status = 'SENT'
            notify_messages_after_commit(account.pk, business_cycle=cycle)
        notice.completed_at = timezone.now()
        notice.save(update_fields=['status', 'note', 'message', 'completed_at'])
