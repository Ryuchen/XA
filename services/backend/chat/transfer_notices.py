"""Store-local, recoverable notice of an actual support-session transfer."""
import logging

from django.utils import timezone
from stores.db import transaction

logger = logging.getLogger(__name__)


def record(event):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('咨询转交通知须在接待事务内记录')
    if event.action != 'TRANSFER' or not event.current_id:
        return
    # The transfer transaction already resolved the actual target account.
    # Persist its business cycle with the assignment event, never infer it
    # from the account at delivery time.
    event.transfer_notice_pending = True
    event.transfer_notice_cycle = event.current.business_cycle
    event.save(update_fields=['transfer_notice_pending', 'transfer_notice_cycle'])
    transaction.on_commit(lambda event_id=event.pk: deliver_safely(event_id), robust=True)


def deliver_safely(event_id):
    try:
        deliver(event_id)
    except Exception:
        logger.exception('咨询转交通知待恢复 event_id=%s', event_id)


def deliver(event_id):
    from club_accounts.models import ClubAccount
    from console.permissions import get_account_permissions, is_console_account
    from site_messages.models import Message
    from site_messages.notifier import notify_messages_after_commit
    from .models import ChatAssignmentEvent, ChatSession

    hint = ChatAssignmentEvent.objects.filter(pk=event_id).values('current_id', 'session_id').first()
    if not hint or not hint['current_id']:
        return
    with transaction.atomic():
        account = ClubAccount.objects.select_for_update().filter(pk=hint['current_id']).first()
        session = ChatSession.objects.select_for_update().filter(pk=hint['session_id']).first()
        event = ChatAssignmentEvent.objects.select_for_update().filter(pk=event_id,
            current_id=hint['current_id'], session_id=hint['session_id']).first()
        if not event or not event.transfer_notice_pending or event.transfer_notice_completed_at:
            return
        cycle = event.transfer_notice_cycle
        if type(cycle) is not int or cycle <= 0 or event.action != 'TRANSFER':
            event.transfer_notice_error = '转交目标来源周期待核对'
            event.save(update_fields=['transfer_notice_error'])
            return
        if (not account or account.account_type != ClubAccount.AccountType.STAFF
                or account.business_cycle != cycle or not is_console_account(account)
                or not {'chat:view', 'chat:reply'}.issubset(get_account_permissions(account))
                or not session or session.claimed_by_id != account.pk or session.claim_version != event.version):
            event.transfer_notice_error = '原接待人员、权限或会话归属已变化'
            event.transfer_notice_completed_at = timezone.now()
            event.save(update_fields=['transfer_notice_error', 'transfer_notice_completed_at'])
            return
        message = Message.objects.create(recipient_account=account, recipient_business_cycle=cycle,
            type='SUPPORT', title='有咨询转交给你', preview='有一条咨询已转交给你，请在客服工作台查看。')
        event.transfer_notice_message, event.transfer_notice_completed_at = message, timezone.now()
        event.transfer_notice_error = ''
        event.save(update_fields=['transfer_notice_message', 'transfer_notice_completed_at', 'transfer_notice_error'])
        notify_messages_after_commit(account.pk, business_cycle=cycle)
