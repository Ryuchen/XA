"""Store-local admission for new activity subject to a registered certificate."""
from datetime import datetime, time, timedelta

from django.conf import settings
from django.db import DatabaseError, InterfaceError, transaction
from django.db.models import Q, Subquery
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError

from .context import current_store
from .models import Store, StoreBusinessCertificate


def enabled():
    return getattr(settings, 'STORE_CERTIFICATE_ENFORCEMENT_ENABLED', False)


def _evidence_at(store, checked_at):
    from .business_certificates import expired

    evidence = {'store_id': store.pk, 'checked_at': checked_at.isoformat()}
    try:
        # Business callers can already hold tenant locks. Read only the
        # registered metadata without acquiring a control-plane row lock.
        with transaction.atomic(using='default'):
            record = (StoreBusinessCertificate.objects.filter(store=store, created_at__lte=checked_at)
                      .values_list('pk', 'expires_on').first())
    except (DatabaseError, InterfaceError):
        return {**evidence, 'status': 'CONTROL_UNAVAILABLE'}
    if record is None:
        return {**evidence, 'status': 'MISSING'}
    identifier, expires_on = record
    return {
        **evidence, 'id': identifier, 'expires_on': expires_on.isoformat() if expires_on else None,
        'status': 'EXPIRED' if expired(expires_on, checked_at) else 'VALID',
    }


def check_admission():
    if not enabled():
        return None
    store = current_store()
    if store is None:
        return 'UNKNOWN', {'status': 'STORE_CONTEXT_UNAVAILABLE'}
    if store.status != Store.Status.ACTIVE:
        return None  # First opening and configuration have their own admission.
    evidence = _evidence_at(store, timezone.now())
    status = evidence['status']
    if status == 'CONTROL_UNAVAILABLE':
        return 'UNKNOWN', evidence
    return ('ALLOW' if status == 'VALID' else 'DENY'), evidence


def _interval_parts(store, started_at, ended_at):
    """Use one control read for the certificate at the start and replacements."""
    records = StoreBusinessCertificate.objects.filter(store=store)
    previous = records.filter(created_at__lte=started_at).order_by('-id').values('pk')[:1]
    try:
        with transaction.atomic(using='default'):
            rows = list(records.filter(Q(pk=Subquery(previous))
                | Q(created_at__gt=started_at, created_at__lt=ended_at))
                .order_by('created_at', 'id').values_list('pk', 'expires_on', 'created_at'))
    except (DatabaseError, InterfaceError):
        return [(started_at, ended_at, 'UNKNOWN', {'store_id': store.pk, 'status': 'CONTROL_UNAVAILABLE'})]

    from .business_certificates import CERTIFICATE_TIMEZONE, expired

    def parts_for(start, end, record):
        if record is None:
            return [(start, end, 'DENY', {'store_id': store.pk, 'status': 'MISSING'})]
        identifier, expires_on, _ = record
        metadata = {'store_id': store.pk, 'id': identifier, 'expires_on': expires_on.isoformat() if expires_on else None}
        denied = {**metadata, 'status': 'EXPIRED'}
        valid = {**metadata, 'status': 'VALID'}
        if expired(expires_on, start):
            return [(start, end, 'DENY', denied)]
        if not expired(expires_on, end - timedelta(microseconds=1)):
            return [(start, end, 'ALLOW', valid)]
        cutoff = datetime.combine(expires_on + timedelta(days=1), time.min, CERTIFICATE_TIMEZONE)
        return [(start, cutoff, 'ALLOW', valid), (cutoff, end, 'DENY', denied)]

    parts = []
    current, position = None, started_at
    for row in rows:
        if current is not None and row[0] < current[0]:
            continue  # A clock skew cannot make an older version current again.
        registered_at = row[2]
        if registered_at > position:
            parts.extend(parts_for(position, registered_at, current))
            position = registered_at
        current = row
    parts.extend(parts_for(position, ended_at, current))
    return parts


def interval_admission(started_at, ended_at):
    """Partition the half-open observation using the store's original facts."""
    if not enabled():
        return []
    store = current_store()
    if store is None:
        return [(started_at, ended_at, 'UNKNOWN', {'status': 'STORE_CONTEXT_UNAVAILABLE'})]
    if store.status != Store.Status.ACTIVE:
        return []
    return _interval_parts(store, started_at, ended_at)


def original_interval_milliseconds(evidence, started_at, ended_at, recorded_ms):
    store = current_store()
    if (not isinstance(evidence, dict) or store is None or evidence.get('store_id') != store.pk
            or started_at >= ended_at or type(recorded_ms) is not int or recorded_ms <= 0):
        raise ValidationError('原在线区间的证照与门店归属待核对')
    if evidence.get('status') == 'VALID':
        return recorded_ms
    if evidence.get('status') != 'CONTROL_UNAVAILABLE':
        raise ValidationError('原在线区间的证照状态待核对')
    eligible = 0
    for start, end, outcome, _ in _interval_parts(store, started_at, ended_at):
        if outcome == 'UNKNOWN':
            _unavailable_interval()
        if outcome == 'ALLOW':
            eligible += (end - start) // timedelta(milliseconds=1)
    if not eligible:
        raise ValidationError('原在线时段缺少有效的本店证照')
    # The interval ledger is the source for observed time. Recovered
    # certificate segments can only narrow that original amount.
    return min(eligible, recorded_ms)


def require_interval_admission(started_at, ended_at):
    _require_valid_interval(interval_admission(started_at, ended_at))


def _require_valid_interval(parts):
    for _, _, outcome, _ in parts:
        if outcome == 'UNKNOWN':
            _unavailable_interval()
        if outcome != 'ALLOW':
            raise ValidationError('原在线时段缺少有效的本店证照')


def _unavailable_interval():
    from .license_benefits import LicenseDecisionUnavailable
    raise LicenseDecisionUnavailable('原在线时段的门店证照暂不可核对，请稍后重试')


def require_original_certificate(evidence):
    """An otherwise admissible review still needs the original certificate."""
    if not isinstance(evidence, dict) or 'business_certificate' not in evidence:
        return
    certificate = evidence['business_certificate']
    store = current_store()
    if store is None or not isinstance(certificate, dict) or certificate.get('store_id') != store.pk:
        raise ValidationError('原证照与门店归属待核对')
    if certificate.get('status') == 'VALID':
        return  # The original check already verified this store's certificate.
    try:
        checked_at = parse_datetime(certificate.get('checked_at', ''))
    except (TypeError, ValueError, OverflowError):
        checked_at = None
    if certificate.get('status') != 'CONTROL_UNAVAILABLE' or checked_at is None or timezone.is_naive(checked_at):
        raise ValidationError('原证照状态待核对')
    status = _evidence_at(store, checked_at)['status']
    if status == 'CONTROL_UNAVAILABLE':
        from .license_benefits import LicenseDecisionUnavailable
        raise LicenseDecisionUnavailable('原证照暂不可核对，请稍后重试')
    if status != 'VALID':
        raise ValidationError('原业务发生时缺少有效的本店证照')
