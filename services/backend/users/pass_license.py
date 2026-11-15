"""License admission for new pass purchases, separate from existing passes."""
from rest_framework.response import Response
from stores.license_benefits import check_new_benefits


def status(*, lock=False):
    from .pass_rules import pass_sales_enabled
    try:
        if not pass_sales_enabled(lock=lock):
            return 'DISABLED'
    except ValueError:
        return 'UNKNOWN'
    return check_new_benefits('PROVIDER_PASS')[0]


def refusal(outcome):
    if outcome == 'DISABLED':
        return Response({'code': 403, 'errorCode': 'PROVIDER_PASS_SALES_DISABLED',
                         'msg': '本店已暂停销售通行证'}, status=403)
    if outcome == 'UNKNOWN':
        return Response({'code': 503, 'errorCode': 'LICENSE_UNAVAILABLE',
                         'msg': '门店授权状态暂时无法核验，请稍后重试'}, status=503)
    if outcome != 'ALLOW':
        return Response({'code': 403, 'errorCode': 'PROVIDER_PASS_LICENSE_DENIED',
                         'msg': '门店授权暂不允许购买通行证，请联系平台'}, status=403)
    return None
