"""Recover missed messages from committed withdrawal facts, not guessed payouts."""
import logging

from django.conf import settings
from django.db.models import Exists, OuterRef, Q

from club_accounts.models import ClubAccount
from site_messages.models import WithdrawalNoticeDelivery
from stores.db import transaction
from .models import WithdrawalPlatformEvent, WithdrawRequest
from .withdraw_notifications import PLATFORM_EVENTS, withdrawal_message

log = logging.getLogger(__name__)


def _project(withdrawal_id, *, platform_event_id=None, owner_event=None, retry_owner_review=False):
    initial = WithdrawRequest.objects.filter(pk=withdrawal_id).values('account_id').first()
    if not initial:
        return
    account = ClubAccount.objects.filter(pk=initial['account_id'], account_type='PROVIDER').first()
    use_lifecycle = bool(getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))
    if use_lifecycle and account:
        from club_accounts.provider_lifecycle import ensure_provider_lifecycle
        ensure_provider_lifecycle(account)
    with transaction.atomic():
        if use_lifecycle and account:
            from club_accounts.provider_lifecycle import lock_provider_operation
            lock_provider_operation(account)
        row = WithdrawRequest.objects.select_for_update().filter(pk=withdrawal_id, workflow_version=2).first()
        if not row or row.account_id != initial['account_id']:
            return
        if owner_event in ('submitted', 'owner_cancelled'):
            withdrawal_message(row, owner_event, retry_owner_review=retry_owner_review)
            return
        if platform_event_id is not None:
            event = WithdrawalPlatformEvent.objects.filter(pk=platform_event_id, withdrawal=row).first()
            if not event or event.action not in PLATFORM_EVENTS:
                return
            reason = event.detail.get('reason', '') if isinstance(event.detail, dict) else ''
            withdrawal_message(row, PLATFORM_EVENTS[event.action], reason=reason, platform_event=event)
            return
        if not row.audited_at:
            return
        has_platform = WithdrawalPlatformEvent.objects.filter(withdrawal=row).exists()
        if has_platform or row.status in (row.Status.STORE_APPROVED, row.Status.PLATFORM_APPROVED,
                                           row.Status.PAYMENT_UNKNOWN, row.Status.PAID):
            withdrawal_message(row, 'store_approved')
        elif row.status == row.Status.REJECTED:
            withdrawal_message(row, 'store_rejected', reason=row.audit_remark)


def recover_withdrawal_messages(limit=100):
    """Project only real store review or platform event rows missing a receipt."""
    if not getattr(settings, 'WITHDRAWAL_NOTICE_RECOVERY_ENABLED', False):
        return 0
    submitted_done = WithdrawalNoticeDelivery.objects.filter(withdrawal_id=OuterRef('pk'),
                                                              event_key__startswith='owner:', event_key__endswith=':submitted')
    cancelled_done = WithdrawalNoticeDelivery.objects.filter(withdrawal_id=OuterRef('pk'),
                                                              event_key__startswith='owner:', event_key__endswith=':owner_cancelled')
    submissions = list(WithdrawRequest.objects.filter(workflow_version=2).annotate(
        has_notice=Exists(submitted_done)).filter(has_notice=False).order_by('pk').values_list('pk', flat=True)[:limit])
    cancellations = list(WithdrawRequest.objects.filter(workflow_version=2, status=WithdrawRequest.Status.CANCELLED,
        release__actor_kind='OWNER').annotate(has_notice=Exists(cancelled_done)).filter(has_notice=False)
        .order_by('pk').values_list('pk', flat=True)[:limit])
    platform = WithdrawalPlatformEvent.objects.filter(withdrawal_id=OuterRef('pk'))
    local_done = WithdrawalNoticeDelivery.objects.filter(withdrawal_id=OuterRef('pk'),
                                                           event_key__startswith='store:')
    local = list(WithdrawRequest.objects.filter(workflow_version=2, audited_at__isnull=False)
        .annotate(has_platform=Exists(platform), has_notice=Exists(local_done))
        .filter(has_notice=False).filter(Q(has_platform=True) | Q(status__in=[
            WithdrawRequest.Status.STORE_APPROVED, WithdrawRequest.Status.PLATFORM_APPROVED,
            WithdrawRequest.Status.PAYMENT_UNKNOWN, WithdrawRequest.Status.PAID, WithdrawRequest.Status.REJECTED]))
        .order_by('audited_at', 'pk').values_list('pk', flat=True)[:limit])
    projected = WithdrawalNoticeDelivery.objects.filter(platform_event_id__isnull=False).values_list('platform_event_id', flat=True)
    events = list(WithdrawalPlatformEvent.objects.filter(action__in=PLATFORM_EVENTS).exclude(pk__in=projected)
                  .order_by('pk').values_list('withdrawal_id', 'pk')[:limit])
    for withdrawal_id, event_id, owner_event in ([(pk, None, 'submitted') for pk in submissions]
            + [(pk, None, 'owner_cancelled') for pk in cancellations]
            + [(pk, None, None) for pk in local] + [(pk, event_id, None) for pk, event_id in events]):
        try:
            _project(withdrawal_id, platform_event_id=event_id, owner_event=owner_event)
        except Exception:
            log.exception('提现消息恢复失败: withdraw_id=%s event_id=%s owner_event=%s', withdrawal_id, event_id, owner_event)
    return len(submissions) + len(cancellations) + len(local) + len(events)
