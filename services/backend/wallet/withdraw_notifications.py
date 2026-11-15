"""Project committed withdrawal facts into tenant-local messages."""
import logging

from django.conf import settings
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


def _verify_owner_fact(row, event):
    """Verify the original withdrawal and, for cancellation, its release."""
    from .models import Transaction, WithdrawalRelease
    debit = Transaction.objects.select_related('wallet').filter(pk=row.transaction_id).first()
    if (row.workflow_version != 2 or not row.account_id or not row.account_business_cycle or not debit
            or debit.wallet.account_id != row.account_id or (row.user_id and debit.wallet.user_id != row.user_id)
            or debit.tx_type != Transaction.TxType.WITHDRAW or debit.amount != -row.amount
            or debit.balance_after != debit.balance_before + debit.amount or debit.reversal_of_id
            or debit.reversals.exists()):
        return False
    if event == 'submitted':
        return True
    release = WithdrawalRelease.objects.filter(withdrawal=row).first()
    return bool(row.status == row.Status.CANCELLED and release and release.actor_kind == 'OWNER'
        and release.actor_account_id == row.account_id and not release.platform_actor_id
        and release.wallet_id == debit.wallet_id and release.original_transaction_id == debit.pk
        and release.amount == row.amount and release.balance_after == release.balance_before + row.amount
        and release.frozen_before == release.frozen_after + row.amount
        and debit.status == Transaction.Status.FAILED)


def _existing_message(row, event, reason):
    """Keep the current development path usable until the outbox is migrated."""
    try:
        with transaction.atomic():
            from site_messages.models import Message
            from site_messages.notifier import notify_messages_after_commit
            from .notifier import owner_account_id
            title, detail = MESSAGES[event]
            if reason:
                detail += f' 原因：{reason}'
            account_id = owner_account_id(row)
            from club_accounts.models import ClubAccount
            if (not account_id or account_id != row.account_id or not row.account_business_cycle
                    or not ClubAccount.objects.filter(pk=account_id, account_type='PROVIDER',
                        business_cycle=row.account_business_cycle, license_membership_active=True).exists()):
                log.warning('提现站内消息未投递，收件账号或原周期待核对: withdraw_id=%s event=%s', row.pk, event)
                return None
            message = Message.objects.create(recipient_id=row.user_id, recipient_account_id=account_id,
                recipient_business_cycle=row.account_business_cycle,
                title=title, preview=detail[:255], detail=detail,
                action_url=f'/pages/wallet/index?withdraw_id={row.pk}')
            notify_messages_after_commit(message.recipient_account_id, business_cycle=message.recipient_business_cycle)
            return message
    except Exception:
        log.exception('提现站内消息未能写入: withdraw_id=%s event=%s', row.pk, event)
        return None


def withdrawal_message(row, event, *, reason='', platform_event=None, retry_owner_review=False):
    """Create once per real fact; a failed projection is recovered by the scan.

    Call while the withdrawal is locked. A savepoint prevents an unavailable
    notification from rolling back the already authorized financial change.
    """
    if event in ('submitted', 'owner_cancelled'):
        try:
            with transaction.atomic():
                if not _verify_owner_fact(row, event):
                    log.warning('提现通知来源待核对: withdraw_id=%s event=%s', row.pk, event)
                    if getattr(settings, 'WITHDRAWAL_NOTICE_RECOVERY_ENABLED', False):
                        from site_messages.models import WithdrawalNoticeDelivery
                        WithdrawalNoticeDelivery.objects.get_or_create(
                            event_key=f'owner:{row.pk}:{event}',
                            defaults={'withdrawal': row, 'skipped_reason': OWNER_REVIEW_REASON})
                    return None
        except Exception:
            log.exception('提现通知来源暂不可核验: withdraw_id=%s event=%s', row.pk, event)
            return None
    if not getattr(settings, 'WITHDRAWAL_NOTICE_RECOVERY_ENABLED', False):
        return _existing_message(row, event, reason)
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
                if not (retry_owner_review and event in ('submitted', 'owner_cancelled')
                        and not delivery.message_id and delivery.skipped_reason == OWNER_REVIEW_REASON):
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
