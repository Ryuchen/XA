"""Admission for new store payroll configuration, separate from wage settlement."""
from rest_framework.exceptions import APIException, PermissionDenied

from .license_benefits import check_new_benefits


class PayrollLicenseUnavailable(APIException):
    status_code = 503
    default_detail = '无法核验门店授权，请联系平台后重试'
    default_code = 'license_unavailable'


def require_payroll_configuration(*, account=None):
    if account is not None:
        from .context import current_store
        from .setup_access import is_setup_admin
        store = current_store()
        if store is not None and is_setup_admin(store, account):
            return
    outcome, evidence = check_new_benefits('PAYROLL')
    if evidence.get('status') == 'ENFORCEMENT_DISABLED':
        return
    if outcome == 'UNKNOWN':
        raise PayrollLicenseUnavailable()
    if evidence.get('feature_mode') not in ('BASIC', 'FULL'):
        raise PermissionDenied('门店授权暂不支持新增或修改薪资配置，请联系平台')
