"""Bounded background reconciliation of committed tenant withdrawal audits."""
import logging

from celery import Task, shared_task
from django.conf import settings
from django.db import DatabaseError, transaction

from .context import store_context
from .models import (PlatformWithdrawalAuditDispatchCursor, PlatformWithdrawalAuditScanCursor, Store)
from .platform_withdrawal_audit import read_event, record_committed_event

logger = logging.getLogger(__name__)
MAX_STORES = 25
MAX_EVENTS = 100


class StoreReadUnavailable(Exception):
    pass


@shared_task(base=Task, name='stores.withdrawal_audit_tasks.scan_store', ignore_result=False)
def scan_store(store_id):
    if not getattr(settings, 'WITHDRAW_AUDIT_AUTO_RECOVERY_ENABLED', False):
        return {'status': 'DISABLED', 'store_id': store_id}
    if type(store_id) is not int or store_id <= 0:
        return {'status': 'INVALID_STORE'}
    try:
        with transaction.atomic(using='default'):
            from .database_identity import lock_store_registration
            lock_store_registration()
            store = Store.objects.filter(pk=store_id, initialized_at__isnull=False).first()
            if store is None:
                return {'status': 'STORE_NOT_READY', 'store_id': store_id}
            cursor, _ = PlatformWithdrawalAuditScanCursor.objects.get_or_create(store=store)
            cursor = PlatformWithdrawalAuditScanCursor.objects.select_for_update().get(pk=cursor.pk)
            try:
                with store_context(store):
                    if store.database_alias not in settings.DATABASES:
                        raise StoreReadUnavailable()
                    from wallet.models import WithdrawalPlatformEvent
                    events = list(WithdrawalPlatformEvent.objects.filter(pk__gt=cursor.last_event_id)
                                  .order_by('pk').values_list('pk', 'withdrawal_id')[:MAX_EVENTS + 1])
            except (DatabaseError, ValueError) as exc:
                raise StoreReadUnavailable() from exc
            selected = events[:MAX_EVENTS]
            created = already = 0
            review_events = []
            for event_id, withdrawal_id in selected:
                try:
                    try:
                        facts = read_event(store, withdrawal_id, event_id)
                    except DatabaseError as exc:
                        raise StoreReadUnavailable() from exc
                    _, was_created = record_committed_event(store, facts)
                    if was_created:
                        created += 1
                    else:
                        already += 1
                except ValueError:
                    review_events.append({'event_id': event_id, 'withdrawal_id': withdrawal_id})
            # Wrap around even if a lower event ID committed after this scan.
            cursor.last_event_id = selected[-1][0] if len(events) > MAX_EVENTS else 0
            cursor.save(update_fields=['last_event_id', 'updated_at'])
        result = {'status': 'REVIEW' if review_events else 'OK', 'store_id': store_id,
                  'scanned': len(selected), 'recovered': created, 'already_recorded': already,
                  'review': len(review_events), 'review_events': review_events, 'next_after': cursor.last_event_id}
        if review_events:
            logger.warning('Withdrawal audit scan requires review for store %s: %s events', store_id, len(review_events))
        return result
    except StoreReadUnavailable:
        logger.warning('Withdrawal audit scan could not read store %s', store_id)
        return {'status': 'STORE_UNAVAILABLE', 'store_id': store_id}
    except DatabaseError:
        logger.warning('Withdrawal audit scan could not reach platform control for store %s', store_id)
        return {'status': 'CONTROL_UNAVAILABLE', 'store_id': store_id}


@shared_task(base=Task, name='stores.withdrawal_audit_tasks.dispatch', ignore_result=False)
def dispatch():
    if not getattr(settings, 'WITHDRAW_AUDIT_AUTO_RECOVERY_ENABLED', False):
        return {'status': 'DISABLED', 'queued': 0}
    try:
        with transaction.atomic(using='default'):
            from .database_identity import lock_store_registration
            lock_store_registration()
            PlatformWithdrawalAuditDispatchCursor.objects.get_or_create(pk=1)
            cursor = PlatformWithdrawalAuditDispatchCursor.objects.select_for_update().get(pk=1)
            ids = list(Store.objects.filter(initialized_at__isnull=False, pk__gt=cursor.last_store_id)
                       .order_by('pk').values_list('pk', flat=True)[:MAX_STORES + 1])
            selected = ids[:MAX_STORES]
            for store_id in selected:
                scan_store.apply_async(args=[store_id])
            cursor.last_store_id = selected[-1] if len(ids) > MAX_STORES else 0
            cursor.save(update_fields=['last_store_id', 'updated_at'])
        return {'status': 'OK', 'queued': len(selected), 'next_after': cursor.last_store_id}
    except DatabaseError:
        logger.warning('Withdrawal audit dispatcher could not reach platform control')
        return {'status': 'CONTROL_UNAVAILABLE', 'queued': 0}
