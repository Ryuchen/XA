"""Resolve a store recharge gift approval and credit the gift exactly once."""
from django.utils import timezone
from stores.db import transaction

from .models import RechargeGiftApproval, Transaction
from .services import get_wallet, WalletAddressingError


class RechargeApprovalError(ValueError):
    pass


def _verify_original(record, approval, wallet):
    snapshot = record.bonus_snapshot
    if (not wallet or not record.account_id or wallet.account_id != record.account_id
            or not isinstance(snapshot, dict) or snapshot.get('amount') != record.amount
            or snapshot.get('gift_amount') != approval.standard_amount
            or type(snapshot.get('remaining')) is not int or snapshot['remaining'] < 0
            or not isinstance(snapshot.get('matches'), list)
            or not isinstance(snapshot.get('version'), str) or len(snapshot['version']) != 64
            or snapshot.get('points_rule') != 'VIP_AT_PAYMENT'
            or type(record.account_business_cycle) is not int or record.account_business_cycle < 1
            or approval.requested_amount == approval.standard_amount):
        raise RechargeApprovalError('充值原档位或账号周期待核对')
    used, gifts = 0, 0
    for row in snapshot['matches']:
        if (not isinstance(row, dict) or any(type(row.get(k)) is not int for k in
                ('id', 'amount', 'gift_amount', 'count', 'gift_total'))
                or row['id'] < 1 or row['amount'] < 1 or row['gift_amount'] < 0 or row['count'] < 1
                or row['gift_total'] != row['gift_amount'] * row['count']):
            raise RechargeApprovalError('充值原档位组合待核对')
        used += row['amount'] * row['count']
        gifts += row['gift_total']
    if used + snapshot['remaining'] != record.amount or gifts != approval.standard_amount:
        raise RechargeApprovalError('充值原档位组合与标准赠送不一致')
    tx = record.recharge_tx
    if (not tx or tx.wallet_id != wallet.pk or tx.tx_type != Transaction.TxType.TOPUP
            or tx.status != Transaction.Status.SUCCESS or tx.amount != record.amount
            or tx.operator_account_id != record.operator_account_id
            or tx.balance_after != tx.balance_before + tx.amount or tx.reversal_of_id or tx.reversals.exists()
            or tx.created_at < record.created_at):
        raise RechargeApprovalError('充值本金到账流水待核对')


def _verify_completed(record, approval, wallet):
    amount = approval.requested_amount if approval.status == 'APPROVED' else approval.standard_amount
    tx = record.gift_tx
    if record.gift_amount != amount:
        raise RechargeApprovalError('审批结果与实际赠送金额不一致')
    if not amount:
        if tx:
            raise RechargeApprovalError('零赠送不应存在到账流水')
        return
    from engagement.models import PointFundingLot
    if (not tx or tx.wallet_id != wallet.pk or tx.tx_type != Transaction.TxType.GIFT
            or tx.status != Transaction.Status.SUCCESS or tx.amount != amount
            or tx.operator_account_id != approval.decided_by_id
            or tx.balance_after != tx.balance_before + amount or tx.reversal_of_id or tx.reversals.exists()
            or not PointFundingLot.objects.filter(account_id=record.account_id,
                source_key=f'wallet-adjust:{tx.pk}', kind=PointFundingLot.Kind.GIFT).exists()):
        raise RechargeApprovalError('审批赠送流水或资金来源待核对')


def _original_wallet(record):
    try:
        return get_wallet(account_id=record.account_id, user_id=record.user_id, for_update=True, create=False)
    except WalletAddressingError as exc:
        raise RechargeApprovalError('充值账号与钱包归属待核对') from exc


def resolve_recharge_gift(recharge_id, decision, reason, actor, legacy_user):
    if decision not in ('APPROVED', 'REJECTED'):
        raise RechargeApprovalError('请选择通过或驳回')
    with transaction.atomic():
        approval = RechargeGiftApproval.objects.select_for_update().select_related('recharge').get(recharge_id=recharge_id)
        if approval.status != RechargeGiftApproval.Status.PENDING:
            # A lost response may be retried; only the identical decision by
            # its original actor is a replay of this immutable approval.
            if (approval.status == decision and approval.decided_by_id == actor.pk
                    and approval.decision_reason == reason[:255] and approval.decided_at):
                record = approval.recharge
                wallet = _original_wallet(record)
                _verify_original(record, approval, wallet)
                _verify_completed(record, approval, wallet)
                return approval
            raise RechargeApprovalError('该申请已处理，请刷新后核对')
        record = approval.recharge
        wallet = _original_wallet(record)
        _verify_original(record, approval, wallet)
        from club_accounts.models import ClubAccount
        account = ClubAccount.objects.select_for_update().filter(pk=record.account_id,
            account_type=ClubAccount.AccountType.BOSS).first()
        if not account or not account.license_membership_active or account.business_cycle != record.account_business_cycle:
            raise RechargeApprovalError('充值所属老板周期已变化，请先核对')
        amount = approval.requested_amount if decision == 'APPROVED' else approval.standard_amount
        if not wallet.is_active:
            raise RechargeApprovalError('钱包已停用，请先核对账户状态')
        if amount and not wallet.account_id:
            raise RechargeApprovalError('钱包尚未关联老板账号，请先核对')
        if max(wallet.balance + amount, wallet.total_gift + amount) > 2147483647:
            raise RechargeApprovalError('赠送到账后超过钱包范围，请先核对')
        if record.gift_tx_id or record.gift_amount:
            raise RechargeApprovalError('该充值已有赠送记录，请先核对')
        if amount:
            before = wallet.balance
            tx = Transaction.objects.create(wallet=wallet, amount=amount, tx_type=Transaction.TxType.GIFT,
                balance_before=before, balance_after=before + amount, remark='预存赠送审批到账',
                operator=legacy_user, operator_account=actor)
            wallet.balance += amount
            wallet.total_gift += amount
            wallet.save(update_fields=['balance', 'total_gift'])
            from engagement.services import record_wallet_adjustment
            from engagement.models import PointFundingLot
            record_wallet_adjustment(wallet=wallet, tx=tx, kind=PointFundingLot.Kind.GIFT)
            record.gift_tx = tx
        record.gift_amount = amount
        record.save(update_fields=['gift_amount', 'gift_tx'])
        approval.status, approval.decision_reason = decision, reason[:255]
        approval.decided_by, approval.decided_at = actor, timezone.now()
        approval.save(update_fields=['status', 'decision_reason', 'decided_by', 'decided_at'])
        from .notifier import notify_wallet_update
        transaction.on_commit(lambda: notify_wallet_update(wallet))
        from site_messages.recharge_gift_decision_notice import enabled as notice_enabled
        if notice_enabled():
            from site_messages.recharge_gift_decision_notice import record as record_notice
            record_notice(approval)
        else:
            from orders.services import push_message_safe
            from stores.context import current_store
            currency = current_store().currency_name if current_store() else '店币'
            from django.conf import settings
            guarded = getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
            preview = (f'赠送 {amount / 10:g} {currency}已按审批结果到账' if amount
                       else '审批结果已生效，本次无赠送金额')
            transaction.on_commit(lambda: push_message_safe(recipient_id=record.user_id, recipient_account_id=record.account_id,
                title='充值赠送审批完成', preview=preview, msg_type='SYSTEM',
                recipient_business_cycle=record.account_business_cycle, require_cycle=guarded))
        return approval
