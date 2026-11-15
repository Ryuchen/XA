"""License limits on viewing tenant audit logs, never on writing them."""
from datetime import timedelta

from django.utils import timezone
from rest_framework.exceptions import APIException, PermissionDenied

from .license_benefits import check_new_benefits


class AuditLicenseUnavailable(APIException):
    status_code = 503
    default_detail = '无法核验门店授权，请联系平台后重试'
    default_code = 'license_unavailable'


def restrict_audit_logs(queryset):
    """Basic recent view covers the preceding 30 days, inclusive at cutoff.

    READ_ONLY (including expired licenses) retains this bounded security view.
    License checks never change the separate role permission or audit writes.
    """
    outcome, evidence = check_new_benefits('AUDIT_LOG_VIEW')
    if evidence.get('status') == 'ENFORCEMENT_DISABLED':
        return queryset
    if outcome == 'UNKNOWN':
        raise AuditLicenseUnavailable()
    mode = evidence['feature_mode']
    if mode == 'FULL':
        return queryset
    if mode in ('RECENT', 'READ_ONLY'):
        return queryset.filter(created_at__gte=timezone.now() - timedelta(days=30))
    raise PermissionDenied('门店授权暂不允许查看审计日志，请联系门店管理员')
