"""Durable, tenant-isolated SaaS license reminders and cancellation notices."""
from datetime import timedelta
import logging

from django.conf import settings
from django.db import DatabaseError, transaction
from django.db.models import F, Q
from django.utils import timezone

from .models import (LicenseNotice, PlatformLicenseNotice, PlatformRoleGrant, Store,
                     StoreLicenseActivatorGrant, StoreLicenseCode)
from .license_status import status_for_store

logger = logging.getLogger(__name__)


def _wake_after_commit():
    if not getattr(settings, 'LICENSE_NOTICE_DISPATCH_ENABLED', True):
        return

    def wake():
        try:
            from .license_tasks import deliver_license_notices
            deliver_license_notices.delay()
        except Exception:
            # A later periodic sweep retries the already committed outbox.
            logger.exception('License notification worker unavailable; outbox retained')
    transaction.on_commit(wake, using='default')


def enqueue_cancellation(code, *, now=None):
    now = now or timezone.now()
    notice, created = LicenseNotice.objects.get_or_create(event_key=f'cancel:{code.pk}', defaults={
        'store': code.store, 'license_code': code, 'kind': 'CANCEL', 'deadline': code.starts_at,
        'platform_delivered_at': now,
    })
    if created:
        _wake_after_commit()
    return notice


def _has_continuous_renewal(state):
    current, scheduled = state['current'], state['scheduled']
    if not current or not scheduled or scheduled['kind'] != 'RENEW':
        return False
    from django.utils.dateparse import parse_datetime
    return (parse_datetime(scheduled['starts_at']) <= parse_datetime(current['ends_at'])
            and parse_datetime(scheduled['ends_at']) > parse_datetime(current['ends_at']))


@transaction.atomic(using='default')
def collect_store_notice(store, *, now=None):
    """Queue only the latest missed threshold, never every missed threshold."""
    now = now or timezone.now()
    state = status_for_store(store, now=now)
    if not state['current'] or _has_continuous_renewal(state):
        return None
    code = StoreLicenseCode.objects.get(pk=state['current']['id'], store=store)
    remaining = code.ends_at - now
    if timedelta(0) < remaining <= timedelta(days=30):
        kind = 'D1' if remaining <= timedelta(days=1) else ('D7' if remaining <= timedelta(days=7) else 'D30')
        deadline = code.ends_at
        event_key = f'{kind}:{code.pk}'
    elif state['status'] == 'GRACE' or (state['status'] == 'EXPIRED' and state['grace_days'] == 0):
        # A zero-day grace has no separate grace-ending event.  If a worker
        # missed the whole grace period, send only the current GRACE_END event.
        kind = 'EXPIRY'
        deadline = code.ends_at
        event_key = f'{kind}:{code.pk}'
    elif state['status'] == 'EXPIRED':
        kind = 'GRACE_END'
        try:
            deadline = code.ends_at + timedelta(days=state['grace_days'])
        except OverflowError:
            return None
        event_key = f'{kind}:{code.pk}:{state["grace_days"]}'
    else:
        return None
    earlier = {'D30': (), 'D7': ('D30',), 'D1': ('D30', 'D7'),
               'EXPIRY': ('D30', 'D7', 'D1'), 'GRACE_END': ('D30', 'D7', 'D1', 'EXPIRY')}[kind]
    if earlier:
        # A store that was offline must receive the current warning once,
        # rather than a burst of every older, undelivered threshold.
        LicenseNotice.objects.filter(store=store, license_code=code, kind__in=earlier, suppressed_at__isnull=True).filter(
            Q(platform_delivered_at__isnull=True) | Q(store_delivered_at__isnull=True)
        ).update(suppressed_at=now)
    notice, created = LicenseNotice.objects.get_or_create(event_key=event_key, defaults={
        'store': store, 'license_code': code, 'kind': kind, 'deadline': deadline,
    })
    revived = bool(not created and notice.suppressed_at and not (notice.platform_delivered_at and notice.store_delivered_at))
    if revived:
        notice.suppressed_at, notice.next_attempt_at = None, None
        notice.save(update_fields=['suppressed_at', 'next_attempt_at'])
    if created or revived:
        _wake_after_commit()
    return notice


def collect_all_notices(*, now=None):
    for store in Store.objects.filter(initialized_at__isnull=False).exclude(status=Store.Status.PROVISIONING):
        try:
            with transaction.atomic(using='default'):
                locked = Store.objects.select_for_update().get(pk=store.pk)
                collect_store_notice(locked, now=now)
        except (DatabaseError, ValueError):
            logger.exception('Could not collect license reminders for store %s', store.slug)


def _text(notice):
    if notice.kind == 'CANCEL':
        return '待生效授权已取消', '平台已取消一项尚未生效的授权安排，请查看门店授权状态。'
    deadline = timezone.localtime(notice.deadline).strftime('%Y-%m-%d %H:%M')
    if notice.kind == 'EXPIRY':
        return '门店授权已到期', f'门店授权已于 {deadline} 到期，请查看授权状态并联系平台处理。'
    if notice.kind == 'GRACE_END':
        return '授权宽限期已结束', f'门店授权宽限期已于 {deadline} 结束，请联系平台处理。'
    return '门店授权即将到期', f'门店授权将于 {deadline} 到期，请及时联系平台处理。'


def _still_current(notice, now):
    if notice.kind == 'CANCEL':
        return True
    state = status_for_store(notice.store, now=now)
    if not state['current'] or state['current']['id'] != str(notice.license_code_id):
        return False
    if _has_continuous_renewal(state):
        return None  # Retain for retry if the scheduled renewal is canceled.
    if notice.kind == 'EXPIRY':
        return state['status'] == 'GRACE' or (state['status'] == 'EXPIRED' and state['grace_days'] == 0)
    if notice.kind == 'GRACE_END':
        try:
            deadline = notice.license_code.ends_at + timedelta(days=state['grace_days'])
        except OverflowError:
            return False
        return (state['status'] == 'EXPIRED' and state['grace_days'] > 0
                and notice.deadline == deadline and notice.deadline <= now)
    remaining = notice.deadline - now
    if not timedelta(0) < remaining <= timedelta(days=30):
        return False
    current_kind = 'D1' if remaining <= timedelta(days=1) else ('D7' if remaining <= timedelta(days=7) else 'D30')
    return notice.kind == current_kind


def deliver_notice(notice_id):
    """Idempotent control-plane + tenant delivery across separate databases."""
    from .context import store_context
    from club_accounts.models import ClubAccount
    from site_messages.models import LicenseNoticeDelivery, Message
    from site_messages.notifier import notify_messages_after_commit

    with transaction.atomic(using='default'):
        notice = LicenseNotice.objects.select_for_update().select_related('store', 'license_code').get(pk=notice_id)
        if notice.suppressed_at or (notice.platform_delivered_at and notice.store_delivered_at):
            return
        now = timezone.now()
        eligible = _still_current(notice, now)
        if eligible is None:
            return
        if not eligible:
            notice.suppressed_at = now
            notice.save(update_fields=['suppressed_at'])
            return
        if not notice.platform_delivered_at:
            accounts = PlatformRoleGrant.objects.filter(role='SUPER_ADMIN', scope_slug='*', is_active=True,
                account__is_active=True).values_list('account_id', flat=True).distinct()
            platform_ids = list(accounts)
            for account_id in platform_ids:
                PlatformLicenseNotice.objects.get_or_create(notice=notice, account_id=account_id)
            if platform_ids:
                notice.platform_delivered_at = now
                notice.save(update_fields=['platform_delivered_at'])
    # Platform delivery is committed independently: an unavailable tenant must
    # not prevent a platform administrator from seeing the reminder.
    with transaction.atomic(using='default'):
        notice = LicenseNotice.objects.select_for_update().select_related('store', 'license_code').get(pk=notice_id)
        if notice.suppressed_at or notice.store_delivered_at:
            return
        now = timezone.now()
        eligible = _still_current(notice, now)
        if eligible is None:
            return
        if not eligible:
            notice.suppressed_at = now
            notice.save(update_fields=['suppressed_at'])
            return
        store = notice.store
        from .dynamic_database import store_connection_ready
        if not store_connection_ready(store):
            return
        delegated = list(StoreLicenseActivatorGrant.objects.filter(store=store, is_active=True)
                         .values_list('account_id', 'account_no'))
        title, detail = _text(notice)
        with store_context(store), transaction.atomic(using=store.database_alias):
            staff = ClubAccount.objects.filter(account_type='STAFF', is_active=True, can_login=True,
                admin_membership__is_active=True).filter(
                    Q(admin_membership__roles__code='super_admin', admin_membership__roles__is_active=True)
                    | Q(pk__in=[item[0] for item in delegated])).distinct()
            allowed_delegates = set(delegated)
            recipients = []
            for account in staff:
                superuser = account.admin_membership.roles.filter(code='super_admin', is_active=True).exists()
                if superuser or (account.pk, account.account_no) in allowed_delegates:
                    recipients.append(account)
            delivered = 0
            for account in recipients:
                locked_account = ClubAccount.objects.select_for_update().filter(pk=account.pk,
                    account_type='STAFF', is_active=True, can_login=True).first()
                if (not locked_account or locked_account.account_no != account.account_no
                        or not staff.filter(pk=account.pk).exists()):
                    continue
                delivery, _ = LicenseNoticeDelivery.objects.get_or_create(notice_id=notice.pk, account=account)
                if delivery.message_id is None:
                    message = Message.objects.create(recipient_account=locked_account,
                        recipient_business_cycle=locked_account.business_cycle,
                        type='SYSTEM', title=title, detail=detail)
                    delivery.message = message
                    delivery.save(update_fields=['message'])
                    notify_messages_after_commit(account.pk, business_cycle=locked_account.business_cycle)
                if delivery.message_id:
                    delivered += 1
        if delivered:
            notice.store_delivered_at = now
            notice.save(update_fields=['store_delivered_at'])


def drain_notices(*, batch_size=100):
    now = timezone.now()
    pending = list(LicenseNotice.objects.filter(suppressed_at__isnull=True)
        .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
        .filter(Q(platform_delivered_at__isnull=True) | Q(store_delivered_at__isnull=True))
        .order_by('created_at', 'id').values_list('id', flat=True)[:batch_size])
    for notice_id in pending:
        try:
            deliver_notice(notice_id)
        except Exception:
            logger.exception('License notice %s remains pending', notice_id)
        finally:
            # A broken/offline store must not occupy the entire next batch.
            LicenseNotice.objects.filter(pk=notice_id, suppressed_at__isnull=True).filter(
                Q(platform_delivered_at__isnull=True) | Q(store_delivered_at__isnull=True)
            ).update(next_attempt_at=timezone.now() + timedelta(minutes=1), attempts=F('attempts') + 1)
    return len(pending)
