"""Test-only at-least-once dispatch of committed direct-payment events.

There is no public endpoint or production scheduler. A production deployment
must explicitly wire the scanner and monitor it before enabling direct payment.
"""
import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .direct_payment import _require_test_domain
from .direct_payment_delivery_models import DirectPaymentInbox, DirectPaymentOutbox
from .direct_payment_models import DirectPaymentIntent
from .models import Order

logger = logging.getLogger(__name__)


def process_payment_outbox(outbox_id, *, lease_seconds=120):
    """Claim one event, tolerate replay, and never perform HTTP under a lock.

    Group messages use the existing per-order/round/channel record and its
    delivery CAS. WebSocket is only a refresh hint; DONE is not proof that a
    person received a notification. Pending and expired leases can be retried.
    """
    _require_test_domain()
    if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
        raise ValueError('invalid outbox lease')
    token = uuid.uuid4().hex
    with transaction.atomic():
        event = DirectPaymentOutbox.objects.select_for_update().get(pk=outbox_id)
        now = timezone.now()
        if event.status == DirectPaymentOutbox.Status.DONE:
            return 'done'
        if event.status == DirectPaymentOutbox.Status.REVIEW:
            return 'review'
        if event.status == DirectPaymentOutbox.Status.CLAIMED and event.lease_until and event.lease_until > now:
            return 'busy'
        event.status, event.lease_token = DirectPaymentOutbox.Status.CLAIMED, token
        event.lease_until, event.attempts = now + timedelta(seconds=lease_seconds), event.attempts + 1
        event.last_error = ''
        event.save(update_fields=['status', 'lease_token', 'lease_until', 'attempts', 'last_error', 'updated_at'])
        intent_id = event.intent_id
    try:
        with transaction.atomic():
            # Inbox processing locks inbox -> order -> intent. Match that order
            # so a concurrent event-content conflict cannot race publication.
            inboxes = list(DirectPaymentInbox.objects.select_for_update().filter(intent_id=intent_id).order_by('pk'))
            intent_head = DirectPaymentIntent.objects.only('order_id').get(pk=intent_id)
            order = Order.objects.select_for_update().get(pk=intent_head.order_id)
            intent = DirectPaymentIntent.objects.select_for_update().get(pk=intent_id)
            event = DirectPaymentOutbox.objects.select_for_update().get(pk=outbox_id)
            if event.status != DirectPaymentOutbox.Status.CLAIMED or event.lease_token != token:
                return 'superseded'
            from .direct_payment_quote import quote_matches_intent
            if any(item.has_conflict for item in inboxes) or intent.status != DirectPaymentIntent.Status.PAID or not quote_matches_intent(intent, order):
                event.status, event.last_error = DirectPaymentOutbox.Status.REVIEW, 'PAYMENT_EVIDENCE_REVIEW'
                event.lease_token, event.lease_until = '', None
                event.save(update_fields=['status', 'last_error', 'lease_token', 'lease_until', 'updated_at'])
                return 'review'
            if (order.status != Order.Status.PENDING or order.payment_status != Order.PaymentStatus.PAID
                    or order.pool_entered_at != event.pool_entered_at or order.reject_count != event.reject_count):
                event.status, event.last_error = DirectPaymentOutbox.Status.DONE, 'STALE_EVENT'
                event.completed_at, event.lease_until, event.lease_token = timezone.now(), None, ''
                event.save(update_fields=['status', 'last_error', 'completed_at', 'lease_until', 'lease_token', 'updated_at'])
                return 'stale'
            if order.auto_cancel_at and order.auto_cancel_at <= timezone.now():
                event.status, event.last_error = DirectPaymentOutbox.Status.REVIEW, 'ORDER_DEADLINE_EXPIRED'
                event.lease_token, event.lease_until = '', None
                event.save(update_fields=['status', 'last_error', 'lease_token', 'lease_until', 'updated_at'])
                return 'review'
            if order.dispatch_route == Order.DispatchRoute.PUBLIC:
                from integrations.kook_config import get_kook_config
                from .dispatching import MODELS, queue_dispatch, send_task_safely
                active = [('KOOK', bool(get_kook_config().auto_new_orders)),
                          ('WECOM', bool(settings.WECOM_DISPATCH_NEW_ORDERS))]
                for channel, enabled in active:
                    if not enabled:
                        continue
                    model = MODELS[channel]
                    existed = model.objects.filter(order=order, sequence=order.reject_count).exists()
                    record = queue_dispatch(order, channel)
                    if record is None:
                        raise RuntimeError('enabled dispatch channel is unavailable')
                    if existed and record and record.status == model.Status.PENDING:
                        # The process could have died after record commit and
                        # before the first Celery enqueue; delivery uses CAS.
                        transaction.on_commit(lambda channel=channel, pk=record.pk: send_task_safely(channel, pk))
        # These are post-commit hints. A periodic timeout scanner and UI refresh
        # remain necessary; neither call proves an offline user was notified.
        from .notifier import notify_order_update
        from .services import schedule_auto_cancel
        latest = Order.objects.get(pk=intent_head.order_id)
        notify_order_update(latest)
        schedule_auto_cancel(latest)
        with transaction.atomic():
            event = DirectPaymentOutbox.objects.select_for_update().get(pk=outbox_id)
            if event.status != DirectPaymentOutbox.Status.CLAIMED or event.lease_token != token:
                return 'superseded'
            event.status, event.completed_at = DirectPaymentOutbox.Status.DONE, timezone.now()
            event.lease_token, event.lease_until = '', None
            event.save(update_fields=['status', 'completed_at', 'lease_token', 'lease_until', 'updated_at'])
        return 'done'
    except Exception:
        # External client exceptions may contain credentials or personal data.
        logger.warning('直接支付待派事件处理失败: outbox_id=%s', outbox_id)
        DirectPaymentOutbox.objects.filter(pk=outbox_id, status=DirectPaymentOutbox.Status.CLAIMED,
                                           lease_token=token).update(status=DirectPaymentOutbox.Status.PENDING,
                                               lease_token='', lease_until=None, last_error='DELIVERY_RETRY')
        return 'retry'


def scan_payment_outbox(limit=100):
    """Explicit recovery hook; a production scheduler is not yet wired."""
    _require_test_domain()
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('invalid scan limit')
    now = timezone.now()
    from django.db.models import Q
    ids = list(DirectPaymentOutbox.objects.filter(
        Q(status=DirectPaymentOutbox.Status.PENDING)
        | Q(status=DirectPaymentOutbox.Status.CLAIMED, lease_until__lte=now)
        | Q(status=DirectPaymentOutbox.Status.CLAIMED, lease_until__isnull=True),
    ).order_by('id').values_list('id', flat=True)[:limit])
    return [(pk, process_payment_outbox(pk)) for pk in ids]
