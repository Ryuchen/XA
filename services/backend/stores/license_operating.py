"""License boundaries for operating costs and on-demand ROI reports."""
from rest_framework.exceptions import APIException, PermissionDenied

from console.payroll import OperatingCostViewSet as BaseCostViewSet, OperatingRoiView as BaseRoiView, require
from .license_benefits import check_new_benefits


class OperatingLicenseUnavailable(APIException):
    status_code = 503
    default_detail = '无法核验门店授权，请联系平台后重试'
    default_code = 'license_unavailable'


def _mode():
    outcome, evidence = check_new_benefits('OPERATING_COST_ROI')
    if evidence.get('status') == 'ENFORCEMENT_DISABLED':
        return 'FULL'
    if outcome == 'UNKNOWN':
        raise OperatingLicenseUnavailable()
    return evidence['feature_mode']


def _deny():
    raise PermissionDenied('门店授权暂不支持此经营分析操作，请联系门店管理员')


class OperatingCostViewSet(BaseCostViewSet):
    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        mode = _mode()
        allowed = ('BASIC', 'FULL', 'READ_ONLY') if request.method in ('GET', 'HEAD', 'OPTIONS') else ('BASIC', 'FULL')
        if mode not in allowed:
            _deny()


class OperatingRoiView(BaseRoiView):
    def get(self, request):
        require(request, 'roi:view')
        # ROI is calculated on demand, not a persisted historical report.
        if _mode() != 'FULL':
            _deny()
        return super().get(request)
