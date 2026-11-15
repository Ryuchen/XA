"""Recover recharge gift decisions from their actual approval and ledger."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction


def enabled():
    return bool(getattr(settings, 'RECHARGE_GIFT_DECISION_NOTICE_RECOVERY_ENABLED', False))


def record(approval):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('充值赠送审批通知须与审批同事务保存')
    from .models import RechargeGiftDecisionNotice
    recharge = approval.recharge
    facts = {'account_id': recharge.account_id, 'business_cycle': recharge.account_business_cycle,
             'decision': approval.status, 'decided_at': approval.decided_at,
             'amount': recharge.gift_amount, 'gift_tx_id': recharge.gift_tx_id}
    notice, created = RechargeGiftDecisionNotice.objects.get_or_create(approval=approval, defaults=facts)
    if not created and any(getattr(notice, key) != value for key, value in facts.items()):
        raise ValueError('充值赠送通知来源不一致')
    transaction.on_commit(lambda pk=notice.pk: deliver(pk), robust=True)


def deliver(notice_id):
    from club_accounts.models import ClubAccount
    from wallet.models import RechargeGiftApproval
    from wallet.recharge_approval import RechargeApprovalError, _verify_original, _verify_completed
    from .models import RechargeGiftDecisionNotice, Message
    from .notifier import notify_messages_after_commit
    hint = RechargeGiftDecisionNotice.objects.filter(pk=notice_id).values('account_id').first()
    if not hint:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=hint['account_id']).first()
        notice = RechargeGiftDecisionNotice.objects.select_for_update().filter(pk=notice_id, account_id=hint['account_id']).first()
        if not notice or notice.status != 'PENDING':
            return
        approval = RechargeGiftApproval.objects.select_related('recharge__recharge_tx__wallet', 'recharge__gift_tx').filter(pk=notice.approval_id).first()
        recharge = approval.recharge if approval else None
        wallet = recharge.recharge_tx.wallet if recharge and recharge.recharge_tx_id else None
        cycle = notice.business_cycle
        valid = bool(type(cycle) is int and cycle > 0 and recharge and recharge.account_id == notice.account_id
            and recharge.account_business_cycle == cycle and approval.status == notice.decision
            and notice.decision in ('APPROVED', 'REJECTED') and approval.decided_at == notice.decided_at
            and approval.decided_by_id and recharge.gift_amount == notice.amount and recharge.gift_tx_id == notice.gift_tx_id)
        if valid:
            try:
                _verify_original(recharge, approval, wallet)
                _verify_completed(recharge, approval, wallet)
            except RechargeApprovalError:
                valid = False
        if not valid:
            notice.status, notice.note = 'REVIEW', '充值赠送审批、流水或原周期待核对'
        elif not account or account.account_type != 'BOSS' or not account.license_membership_active or account.business_cycle != cycle:
            notice.status, notice.note = 'SKIPPED', '原账号业务周期已变化'
        else:
            from stores.context import current_currency_name
            whole, fraction = divmod(notice.amount, 10)
            amount = f'{whole}.{fraction}' if fraction else str(whole)
            preview = (f'赠送 {amount} {current_currency_name()}已按审批结果到账' if notice.amount
                       else '审批结果已生效，本次无赠送金额')
            notice.message = Message.objects.create(recipient_account=account, recipient_business_cycle=cycle,
                type='SYSTEM', title='充值赠送审批完成', preview=preview[:255])
            notice.status = 'SENT'
            notify_messages_after_commit(account.pk, business_cycle=cycle)
        notice.completed_at = timezone.now()
        notice.save(update_fields=['status', 'note', 'message', 'completed_at'])
