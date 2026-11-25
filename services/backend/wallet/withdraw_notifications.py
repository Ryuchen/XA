"""Project committed withdrawal facts into tenant-local messages."""
import logging

from django.utils.dateparse import parse_datetime
from stores.db import transaction

log = logging.getLogger(__name__)

MESSAGES = {
    'submitted': ('提现申请已提交', '提现申请已提交，请查看提现记录了解当前进度。'),
    'owner_cancelled': ('提现申请已取消', '你已取消提现申请，冻结金额已退回可用余额。'),
    'store_approved': ('提现门店初审通过', '门店已初审通过，请查看提现记录了解当前进度。'),
    'store_rejected': ('提现审核未通过', '门店已驳回申请，冻结金额已退回可用余额。'),
    'platform_approved': ('提现平台终审通过', '平台财务已终审通过，请查看提现记录了解当前进度。'),
    'payment_unknown': ('提现打款待核实', '该笔打款曾进入待核实状态，请查看提现记录了解当前进度。'),
    'paid': ('提现已登记打款', '平台财务已登记线下打款，请核对收款账户。'),
    'rejected': ('提现审核未通过', '平台财务已驳回申请，冻结金额已退回可用余额。'),
    'cancelled': ('提现已取消', '平台已取消申请，冻结金额已退回可用余额。'),
}
PLATFORM_EVENTS = {'approve': 'platform_approved', 'mark-unknown': 'payment_unknown',
                   'paid': 'paid', 'reject': 'rejected', 'cancel': 'cancelled'}
OWNER_REVIEW_REASON = '提现原始申请或解冻凭证待核对'


def _verify_message_fact(row, event, platform_event=None):
    """Verify the original debit and the money fact asserted by the notice."""
    from .models import Transaction, WithdrawalPlatformEvent, WithdrawalRelease, WithdrawRequest
    debit = Transaction.objects.select_related('wallet').filter(pk=row.transaction_id).first()
    if (row.workflow_version != 2 or not row.account_id or not row.account_business_cycle or not debit
            or debit.wallet.account_id != row.account_id or (row.user_id and debit.wallet.user_id != row.user_id)
            or debit.tx_type != Transaction.TxType.WITHDRAW or debit.amount != -row.amount
            or debit.balance_after != debit.balance_before + debit.amount or debit.reversal_of_id
            or debit.reversals.exists()
            or WithdrawRequest.objects.filter(transaction_id=debit.pk).exclude(pk=row.pk).exists()):
        return False
    if event == 'submitted':
        return True
    if event == 'store_approved':
        if not row.audited_at or not row.auditor_id or not row.auditor_account_id:
            return False
        release = WithdrawalRelease.objects.filter(withdrawal=row).first()
        if row.status in (row.Status.REJECTED, row.Status.CANCELLED):
            approval = WithdrawalPlatformEvent.objects.filter(withdrawal=row, action='approve').first()
            return bool(release and release.actor_kind == 'PLATFORM' and approval
                and _verify_message_fact(row, 'platform_approved', approval))
        return bool(row.status in (row.Status.STORE_APPROVED, row.Status.PLATFORM_APPROVED,
                                   row.Status.PAYMENT_UNKNOWN, row.Status.PAID)
            and not release and debit.status != Transaction.Status.FAILED)
    if platform_event is not None:
        action = platform_event.action
        transitions = {
            'approve': (row.Status.STORE_APPROVED, row.Status.PLATFORM_APPROVED),
            'mark-unknown': (row.Status.PLATFORM_APPROVED, row.Status.PAYMENT_UNKNOWN),
            'reject': ((row.Status.STORE_APPROVED, row.Status.PLATFORM_APPROVED, row.Status.PAYMENT_UNKNOWN),
                       row.Status.REJECTED),
            'cancel': ((row.Status.STORE_APPROVED, row.Status.PLATFORM_APPROVED, row.Status.PAYMENT_UNKNOWN),
                       row.Status.CANCELLED),
            'paid': ((row.Status.PLATFORM_APPROVED, row.Status.PAYMENT_UNKNOWN), row.Status.PAID),
        }
        transition = transitions.get(action)
        expected_from, expected_to = transition if transition else (None, None)
        from_valid = (platform_event.from_status in expected_from if isinstance(expected_from, tuple)
                      else platform_event.from_status == expected_from)
        detail = platform_event.detail if isinstance(platform_event.detail, dict) else {}
        if (platform_event.withdrawal_id != row.pk or PLATFORM_EVENTS.get(action) != event or not row.audited_at
                or not from_valid or platform_event.to_status != expected_to
                or WithdrawalPlatformEvent.objects.filter(withdrawal=row, action=action).exclude(
                    pk=platform_event.pk).exists()):
            return False
        if action == 'approve':
            return bool(row.platform_reviewer_id == platform_event.platform_actor_id
                and row.platform_reviewed_at and row.status not in (row.Status.PENDING, row.Status.STORE_APPROVED))
        if action == 'mark-unknown':
            reason = detail.get('reason')
            mismatch = (detail.get('source_action') == 'paid'
                and type(detail.get('reported_paid_amount_fen')) is int
                and detail.get('expected_amount_fen') == row.actual_amount
                and isinstance(detail.get('reported_payout_reference'), str)
                and bool(detail['reported_payout_reference']))
            try:
                reported_at = parse_datetime(detail.get('reported_paid_at'))
            except (TypeError, ValueError):
                reported_at = None
            return bool(row.platform_reviewed_at
                and row.status in (row.Status.PAYMENT_UNKNOWN, row.Status.PAID, row.Status.REJECTED, row.Status.CANCELLED)
                and ((isinstance(reason, str) and bool(reason.strip())) or (mismatch and reported_at is not None)))
        if action == 'paid':
            try:
                paid_at = parse_datetime(detail.get('paid_at'))
            except (TypeError, ValueError):
                paid_at = None
            receipt_valid = bool(paid_at and paid_at.utcoffset() is not None and row.paid_at == paid_at
                and row.created_at <= paid_at
                and row.platform_reviewed_at and row.platform_reviewed_at <= paid_at
                and row.payout_recorded_at and paid_at <= row.payout_recorded_at
                and row.platform_payer_id == platform_event.platform_actor_id
                and isinstance(detail.get('payout_reference'), str)
                and bool(detail['payout_reference'].strip())
                and row.payout_reference == detail['payout_reference'].strip()
                and row.actual_amount > 0 and type(detail.get('paid_amount_fen')) is int
                and detail.get('paid_amount_fen') > 0
                and detail.get('paid_amount_fen') == row.actual_amount)
            return bool(receipt_valid and row.status == row.Status.PAID
                and debit.status == Transaction.Status.SUCCESS
                and not WithdrawalRelease.objects.filter(withdrawal=row).exists())
        release = WithdrawalRelease.objects.filter(withdrawal=row).first()
        reason = detail.get('reason')
        return bool(row.status == expected_to and release and detail.get('confirmed_unpaid') is True
            and type(detail.get('release_id')) is int and detail.get('release_id') == release.pk
            and isinstance(reason, str) and bool(reason.strip())
            and release.wallet_id == debit.wallet_id and release.original_transaction_id == debit.pk
            and release.amount == row.amount and release.balance_after == release.balance_before + row.amount
            and release.frozen_before == release.frozen_after + row.amount
            and debit.status == Transaction.Status.FAILED and release.actor_kind == 'PLATFORM'
            and not release.actor_account_id and release.platform_actor_id == platform_event.platform_actor_id
            and not WithdrawalPlatformEvent.objects.filter(withdrawal=row, action='paid').exists())
    if event not in ('owner_cancelled', 'store_rejected'):
        return False
    release = WithdrawalRelease.objects.filter(withdrawal=row).first()
    if (not release or release.wallet_id != debit.wallet_id or release.original_transaction_id != debit.pk
            or release.amount != row.amount or release.balance_after != release.balance_before + row.amount
            or release.frozen_before != release.frozen_after + row.amount
            or debit.status != Transaction.Status.FAILED):
        return False
    if event == 'owner_cancelled':
        return bool(row.status == row.Status.CANCELLED and release.actor_kind == 'OWNER'
            and release.actor_account_id == row.account_id and not release.platform_actor_id
            and not WithdrawalPlatformEvent.objects.filter(withdrawal=row).exists())
    return bool(row.status == row.Status.REJECTED and release.actor_kind == 'STORE'
        and release.actor_account_id == row.auditor_account_id and not release.platform_actor_id)


def withdrawal_message(row, event, *, reason='', platform_event=None, retry_source_review=False):
    """Create once per real fact; a failed projection is recovered by the scan.

    Call while the withdrawal is locked. A savepoint prevents an unavailable
    notification from rolling back the already authorized financial change.
    """
    try:
        if event not in MESSAGES:
            raise ValueError('提现消息事件无效')
        if event in ('submitted', 'owner_cancelled'):
            if platform_event is not None:
                raise ValueError('本人提现事件不应关联平台事件')
            key = f'owner:{row.pk}:{event}'
        elif event in ('store_approved', 'store_rejected'):
            if platform_event is not None:
                raise ValueError('门店初审不应关联平台事件')
            key = f'store:{row.pk}:{event}'
        else:
            if (platform_event is None or platform_event.withdrawal_id != row.pk
                    or PLATFORM_EVENTS.get(platform_event.action) != event):
                raise ValueError('提现平台事件与消息不一致')
            if event in ('rejected', 'cancelled'):
                reason = platform_event.detail.get('reason', '') if isinstance(platform_event.detail, dict) else ''
            key = f'platform:{platform_event.pk}'
        with transaction.atomic():
            from club_accounts.models import ClubAccount
            from site_messages.models import Message, WithdrawalNoticeDelivery
            from site_messages.notifier import notify_messages_after_commit
            delivery, created = WithdrawalNoticeDelivery.objects.select_for_update().get_or_create(
                event_key=key, defaults={'withdrawal': row, 'platform_event': platform_event})
            if not created:
                if (delivery.withdrawal_id != row.pk
                        or delivery.platform_event_id != getattr(platform_event, 'pk', None)):
                    raise ValueError('提现消息记录与原事件不一致')
                if not (retry_source_review and not delivery.message_id
                        and delivery.skipped_reason == OWNER_REVIEW_REASON):
                    return delivery
            if not _verify_message_fact(row, event, platform_event):
                delivery.skipped_reason = OWNER_REVIEW_REASON
                delivery.save(update_fields=['skipped_reason'])
                log.warning('提现通知来源待核对: withdraw_id=%s event=%s', row.pk, event)
                return delivery
            account = ClubAccount.objects.filter(pk=row.account_id, account_type='PROVIDER',
                business_cycle=row.account_business_cycle, license_membership_active=True).first()
            if not account or not row.account_business_cycle:
                delivery.skipped_reason = '原提现账号或门店周期已变化'
                delivery.save(update_fields=['skipped_reason'])
                return delivery
            title, detail = MESSAGES[event]
            if reason:
                detail += f' 原因：{reason}'
            message = Message.objects.create(recipient_id=row.user_id, recipient_account=account,
                recipient_business_cycle=row.account_business_cycle,
                title=title, preview=detail[:255], detail=detail,
                action_url=f'/pages/wallet/index?withdraw_id={row.pk}')
            delivery.message = message
            delivery.skipped_reason = ''
            delivery.save(update_fields=['message', 'skipped_reason'])
            notify_messages_after_commit(account.pk, business_cycle=row.account_business_cycle)
            return delivery
    except Exception:
        log.exception('提现站内消息待恢复: withdraw_id=%s event=%s', row.pk, event)
        return None
