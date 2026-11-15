"""Recover committed pool notifications whose enqueue or worker was lost.

Only version-2 pending jobs and publisher exceptions are retried. A publisher
exception may still have delivered a task; the sender's locked state check
prevents duplicate external sends. An external delivery with an unknown
outcome is never automatically repeated.
"""
from datetime import timedelta
import logging

from django.db.models import Q
from stores.db import transaction

from .dispatching import MODELS, _pool_group_enabled, send_task_safely
from .models import Order

LEASE_SECONDS = 90
logger = logging.getLogger(__name__)


def recover_due(*, limit=100):
    if not _pool_group_enabled():
        return 0
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError('群发恢复批量无效')
    from .grab_group_dispatch import current_time
    now = current_time()
    count = 0
    eligible_status = Q(status='PENDING') | Q(status='FAILED', pool_enqueue_failed=True)
    lease_expired = Q(pool_recovery_lease_until__isnull=True) | Q(pool_recovery_lease_until__lte=now)
    # A restart may lose the activation callback. Find records still using
    # an older, later visibility deadline and wake them for the real latest
    # reopened round; the sender still rechecks the locked order before I/O.
    from django.db.models import F, OuterRef, Subquery
    from .models import OrderGrabRound
    from .dispatching import reschedule_pool_dispatch
    from .grab_clock_contract import TOLERANCE_NS, production_enabled
    latest = OrderGrabRound.objects.filter(pool__order_id=OuterRef('order_id')).order_by('-number')
    for channel, model in MODELS.items():
        overdue_reschedules = (model.objects.filter(eligible_status, content_contract_version=2,
            order__status=Order.Status.PENDING, sequence=F('order__reject_count'))
            .annotate(latest_number=Subquery(latest.values('number')[:1]),
                      latest_opened=Subquery(latest.values('opened_at')[:1]),
                      latest_activation=Subquery(latest.values('opening_evidence__state')[:1]),
                      latest_activation_upper=Subquery(latest.values('opening_evidence__activation_upper_at')[:1]))
            .filter(latest_number__gt=1, latest_activation='OBSERVED'))
        if production_enabled():
            threshold = F('latest_activation_upper') + timedelta(microseconds=TOLERANCE_NS // 1000)
        else:
            threshold = F('latest_opened')
        overdue_reschedules = (overdue_reschedules.filter(pool_delivery_due_at__gt=threshold)
            .order_by('pk').values_list('order_id', flat=True))
        rescheduled = 0
        for order_id in overdue_reschedules.iterator(chunk_size=200):
            try:
                result = reschedule_pool_dispatch(order_id)
                count += result
                rescheduled += bool(result)
                if rescheduled >= limit:
                    break
            except Exception:
                logger.exception('重开抢单群发重排待恢复 order_id=%s', order_id)
    for channel, model in MODELS.items():
        candidates = list(model.objects.filter(eligible_status, lease_expired,
            content_contract_version=2, pool_delivery_due_at__lte=now)
            .order_by('pool_delivery_due_at', 'pk').values_list('pk', 'order_id')[:limit])
        for record_id, order_id in candidates:
            with transaction.atomic():
                # Match the sender and reopening lock order to avoid deadlocks.
                order = Order.objects.select_for_update().filter(pk=order_id).first()
                if not order:
                    continue
                record = model.objects.select_for_update().filter(pk=record_id, order=order).first()
                if (not record or record.content_contract_version != 2 or not record.pool_delivery_due_at
                        or record.pool_delivery_due_at > now
                        or (record.pool_recovery_lease_until and record.pool_recovery_lease_until > now)
                        or not (record.status == 'PENDING' or
                                (record.status == 'FAILED' and record.pool_enqueue_failed))):
                    continue
                record.status = 'PENDING'
                record.last_error = ''
                record.pool_enqueue_failed = False
                record.pool_recovery_lease_until = now + timedelta(seconds=LEASE_SECONDS)
                record.save(update_fields=['status', 'last_error', 'pool_recovery_lease_until', 'pool_enqueue_failed'])
                transaction.on_commit(lambda channel=channel, pk=record.pk: send_task_safely(channel, pk))
                count += 1
    # Payment may have committed before the post-commit opening task or a
    # channel record was created. Only create a missing record; never retry a
    # previously attempted external delivery with an unknown outcome.
    from .models import KookDispatchRecord
    from .kook_dispatch import enqueue_kook_dispatch
    from .dispatching import enqueue_wecom_dispatch
    pending_orders = (Order.objects.filter(status=Order.Status.PENDING,
        grab_opening_intent__state='OBSERVED', grab_pool_snapshot__isnull=False).order_by('pk'))
    examined = 0
    from .grab_group_dispatch import delivery_timing, deadline_reached
    for order in pending_orders.iterator(chunk_size=200):
        try:
            _, deadline = delivery_timing(order)
        except ValueError:
            continue
        if deadline_reached(deadline):
            continue
        created = False
        for relation, enqueue in ((order.kook_dispatches, enqueue_kook_dispatch),
                                  (order.wecom_dispatches, enqueue_wecom_dispatch)):
            if relation.filter(sequence=order.reject_count).exists():
                continue
            try:
                queued = bool(enqueue(order, KookDispatchRecord.Trigger.NEW_ORDER))
                count += queued
                created = created or queued
            except Exception:
                logger.exception('抢单群发记录待恢复 order_id=%s', order.pk)
        examined += bool(created)
        if examined >= limit:
            break
    return count
