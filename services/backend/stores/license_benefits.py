"""Online License admission for new benefits; existing assets stay usable."""
import logging
from functools import wraps

from django.conf import settings
from django.db import DatabaseError, InterfaceError

from .context import current_store
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError

logger = logging.getLogger(__name__)


class LicenseFeatureDenied(ValidationError):
    """A new optional entitlement cannot be issued at this time."""


class LicenseDecisionUnavailable(APIException):
    status_code = 503
    default_detail = '门店授权状态暂时无法核验，请稍后重试'
    default_code = 'license_unavailable'


def skip_denied_benefit(func):
    """Roll back optional VIP work without rolling back core settlement."""
    from stores.db import transaction as store_transaction

    @wraps(func)
    def wrapped(*args, **kwargs):
        try:
            with store_transaction.atomic():
                return func(*args, **kwargs)
        except LicenseFeatureDenied:
            return None
    return wrapped


def check_new_benefits(feature=None):
    """Evaluate new awards, purchases and configuration under both policies."""
    from .certificate_admission import check_admission
    outcome, evidence = check_license_benefits(feature)
    if outcome == 'DENY' or feature in ('AUDIT_LOG_VIEW', 'PAYROLL', 'OPERATING_COST_ROI'):
        return outcome, evidence
    result = check_admission()
    if result is None:
        return outcome, evidence
    certificate_outcome, certificate = result
    evidence['business_certificate'] = certificate
    if certificate_outcome == 'DENY':
        return 'DENY', evidence
    if certificate_outcome == 'UNKNOWN' and outcome == 'ALLOW':
        evidence.update(license_status=evidence['status'], status=certificate['status'])
        return 'UNKNOWN', evidence
    return outcome, evidence


def check_license_benefits(feature=None):
    """Distinguish a definite denial from an unavailable control plane.

    An unavailable control plane uses only a signed local snapshot. Without
    one, callers defer irreversible award decisions rather than guess.
    """
    if not getattr(settings, 'LICENSE_BENEFIT_ENFORCEMENT_ENABLED', True):
        return 'ALLOW', {'status': 'ENFORCEMENT_DISABLED'}
    if feature is not None:
        from .license_policy import FEATURE_DEFAULTS
        if feature not in FEATURE_DEFAULTS:
            raise ValueError('未知授权功能')
    store = current_store()
    if store is None:
        return 'UNKNOWN', {'status': 'STORE_CONTEXT_UNAVAILABLE'}
    try:
        from .license_snapshot import business_status
        status = business_status(store)
    except (DatabaseError, InterfaceError):
        logger.warning('License benefit admission could not be verified for store %s', store.slug)
        return 'UNKNOWN', {'status': 'CONTROL_UNAVAILABLE'}
    except ValueError:
        logger.warning('License feature configuration is invalid for store %s', store.slug)
        return 'UNKNOWN', {'status': 'LICENSE_CONFIGURATION_INVALID'}
    evidence = {key: status[key] for key in ('status', 'tier', 'grace_days', 'current', 'scheduled')}
    evidence['license_source'] = status['license_source']
    if status.get('snapshot_checked_at'):
        evidence['snapshot_checked_at'] = status['snapshot_checked_at']
    if status.get('verified_snapshot'):
        evidence['verified_snapshot'] = status['verified_snapshot']
    if feature is not None:
        evidence.update(feature=feature, feature_mode=status['feature_modes'][feature])
    allowed = status['allow_new_benefits'] and (feature is None or evidence['feature_mode'] == 'FULL')
    return ('ALLOW' if allowed else 'DENY'), evidence


def require_content_operations(*, for_existing_order=False):
    """Gate new operational content while preserving basic service notices."""
    check = check_license_benefits if for_existing_order else check_new_benefits
    outcome, _ = check('CONTENT_OPERATIONS')
    if outcome == 'UNKNOWN':
        raise LicenseDecisionUnavailable()
    if outcome != 'ALLOW':
        raise PermissionDenied('门店授权暂不支持内容运营，请联系平台')


def content_deactivation_only(instance, values, *, field='is_active'):
    """Existing content may be hidden even when new marketing is unavailable."""
    changed = {key for key, value in values.items() if getattr(instance, key) != value}
    return not changed or (changed == {field} and values[field] is False)


def require_promotion_configuration():
    """Authorize new public coupon or promotion configuration, not use of owned coupons."""
    outcome, _ = check_new_benefits('PROMOTIONS')
    if outcome == 'UNKNOWN':
        raise LicenseDecisionUnavailable()
    if outcome != 'ALLOW':
        raise PermissionDenied('门店授权暂不支持启用新的促销或领券活动')


def require_achievement_configuration(*, account=None):
    """Gate new achievement configuration, not earned history or statistics."""
    store = current_store()
    if store is not None and account is not None:
        from .setup_access import is_setup_admin
        if is_setup_admin(store, account):
            # A store may configure its catalogue before license activation.
            # The normal tier decision applies once it opens.
            return
    outcome, _ = check_new_benefits('ACHIEVEMENTS_RANKINGS')
    if outcome == 'UNKNOWN':
        raise LicenseDecisionUnavailable()
    if outcome != 'ALLOW':
        raise PermissionDenied('门店授权暂不支持配置成就，请联系平台')


def require_checkin_configuration(*, account=None):
    """Gate active check-in configuration, never redemption of an earned card."""
    store = current_store()
    if store is not None and account is not None:
        from .setup_access import is_setup_admin
        if is_setup_admin(store, account):
            return
    outcome, _ = check_new_benefits('CHECKIN')
    if outcome == 'UNKNOWN':
        raise LicenseDecisionUnavailable()
    if outcome != 'ALLOW':
        raise PermissionDenied('门店授权暂不支持修改签到规则，请联系平台')
