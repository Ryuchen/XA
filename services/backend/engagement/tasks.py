from datetime import timedelta
from celery import shared_task
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import MemberProfile
from .services import broadcast


def enqueue_broadcast(pk):
    """Broker downtime must never turn a committed purchase into a failure."""
    import logging
    try:
        publish_broadcast.apply_async(args=[pk], retry=False)
    except Exception:
        logging.getLogger(__name__).exception('Broadcast %s awaits outbox retry', pk)


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def publish_broadcast(pk):
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer
    from .models import Broadcast
    with transaction.atomic():
        row = Broadcast.objects.select_for_update().filter(pk=pk).first()
        if not row or row.notified_at:
            return False
        now = timezone.now()
        if not row.published_at:
            row.published_at = now
            row.save(update_fields=['published_at'])
    # Make the feed readable before waking clients. A crash between delivery and
    # acknowledgement may repeat this harmless invalidation, never the event.
    with transaction.atomic():
        row = Broadcast.objects.select_for_update().get(pk=pk)
        if row.notified_at:
            return False
        now = timezone.now()
        if row.is_visible and row.starts_at <= now < row.expires_at:
            layer = get_channel_layer()
            if layer:
                async_to_sync(layer.group_send)('club_bosses', {'type': 'club_feed_update', 'data': {'id': row.pk}})
        row.notified_at = now
        row.save(update_fields=['notified_at'])
    return True


@shared_task
def drain_broadcast_outbox():
    from .models import Broadcast
    ids = list(Broadcast.objects.filter(notified_at__isnull=True).order_by('id').values_list('id', flat=True)[:200])
    for pk in ids:
        enqueue_broadcast(pk)
    return len(ids)


@shared_task
def publish_birthdays():
    today = timezone.localdate()
    profiles = MemberProfile.objects.filter(birthday__month=today.month, birthday__day=today.day,
        birthday_public=True, account__is_active=True, account__can_login=True,
        account__can_view=True, account__account_type='BOSS').select_related('account')
    count = 0
    for profile in profiles.iterator():
        with transaction.atomic():
            broadcast('BIRTHDAY', f'birthday:{profile.account_id}:{profile.account.business_cycle}:{today.year}', account=profile.account)
            count += 1
    return count


@shared_task
def grant_member_coupons():
    from club_accounts.models import ClubAccount
    from coupons.benefits import grant_periodic
    from .models import VipTier
    accounts = ClubAccount.objects.filter(account_type='BOSS', is_active=True,
        boss_type_id__in=VipTier.objects.filter(active=True).values('boss_type_id'))
    count = 0
    import logging
    for account in accounts.iterator():
        try:
            grant_periodic(account)
            count += 1
        except Exception:
            logging.getLogger(__name__).exception('Membership coupon grant failed for account %s; next scan retries', account.pk)
    return count


@shared_task
def confirm_due_consumption():
    from orders.models import Order
    from .confirmation import process_order
    ids = (Order.objects.filter(status='COMPLETED', payment_status='PAID',
        customer_account__isnull=False).filter(
            Q(is_gift_snapshot=False, consumption_confirmed_at__isnull=True)
            | Q(settlement_mode='REPORT_APPROVAL', settled_at__isnull=True))
        .filter(completed_at__lte=timezone.now() - timedelta(hours=72), report_ready_at__isnull=False)
        .order_by('pk').values_list('pk', flat=True).iterator(chunk_size=200))
    count = 0
    import logging
    for pk in ids:
        try:
            count += int(process_order(pk))
        except Exception:
            logging.getLogger(__name__).exception('Consumption confirmation failed for order %s; next scan retries', pk)
    return count
