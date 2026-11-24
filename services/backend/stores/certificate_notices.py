"""Calendar-day certificate reminders using the existing reminder outbox."""
from datetime import datetime, time
import logging

from django.db import DatabaseError, transaction
from django.db.models import Q
from django.utils import timezone

from .business_certificates import CERTIFICATE_TIMEZONE
from .models import LicenseNotice, Store, StoreBusinessCertificate

logger = logging.getLogger(__name__)


def _current_terms(store_id):
    # Callers hold the Store lock shared with recording and replacement.
    return (StoreBusinessCertificate.objects.select_for_update().filter(store_id=store_id)
            .values_list('pk', 'expires_on').first())


def _due_kind(expires_on, now):
    if expires_on is None:
        return None
    days = (expires_on - timezone.localdate(now, CERTIFICATE_TIMEZONE)).days
    if not 0 <= days <= 30:
        return None
    return 'D1' if days <= 1 else ('D7' if days <= 7 else 'D30')


def still_current(notice, now):
    terms = _current_terms(notice.store_id)
    return bool(terms and terms[0] == notice.business_certificate_id
                and notice.kind == _due_kind(terms[1], now))


def collect_certificate_notice(store, *, now=None):
    """Queue the current threshold and suppress stale, undelivered work."""
    from .license_notices import _queue_threshold

    now = now or timezone.now()
    terms = _current_terms(store.pk)
    pending = LicenseNotice.objects.filter(store=store, business_certificate__isnull=False,
        suppressed_at__isnull=True).filter(Q(platform_delivered_at__isnull=True) | Q(store_delivered_at__isnull=True))
    kind = _due_kind(terms[1], now) if terms else None
    if kind is None:
        pending.update(suppressed_at=now)
        return None
    certificate_id, expires_on = terms
    pending.exclude(business_certificate_id=certificate_id, kind=kind).update(suppressed_at=now)
    deadline = datetime.combine(expires_on, time.max, tzinfo=CERTIFICATE_TIMEZONE)
    return _queue_threshold(f'certificate:{certificate_id}:{kind}', {
        'store': store, 'business_certificate_id': certificate_id, 'kind': kind, 'deadline': deadline,
    })


def collect_all_certificate_notices(*, now=None):
    store_ids = Store.objects.filter(business_certificates__isnull=False).values_list('pk', flat=True).distinct()
    for store_id in store_ids:
        try:
            with transaction.atomic(using='default'):
                store = Store.objects.select_for_update().get(pk=store_id)
                collect_certificate_notice(store, now=now)
        except (DatabaseError, ValueError):
            logger.exception('Could not collect business certificate reminders for store %s', store_id)
