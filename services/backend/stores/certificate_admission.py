"""Store-local admission for new activity subject to a registered certificate."""
from django.conf import settings
from django.db import DatabaseError, transaction
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
    except DatabaseError:
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
