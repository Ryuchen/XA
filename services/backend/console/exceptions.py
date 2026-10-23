from rest_framework.views import exception_handler as drf_exception_handler


def console_exception_handler(exc, context):
    """将 DRF 异常统一包成 {code, msg, errors}，与前端解包约定一致。"""
    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    detail = response.data
    msg = '请求失败'
    if isinstance(detail, dict):
        if 'detail' in detail:
            msg = str(detail['detail'])
        else:
            first = next(iter(detail.values()), None)
            if isinstance(first, (list, tuple)) and first:
                msg = str(first[0])
            elif first is not None:
                msg = str(first)
    elif isinstance(detail, (list, tuple)) and detail:
        msg = str(detail[0])

    response.data = {
        'code': response.status_code,
        'msg': msg,
        'errors': detail,
    }
    if response.status_code == 429:
        import math
        wait = max(1, math.ceil(getattr(exc, 'wait', None) or 1))
        response.data.update(errorCode='ORDER_RATE_LIMITED' if hasattr(exc, 'traffic_action') else 'RATE_LIMITED', retry_after=wait)
        if hasattr(exc, 'traffic_mode'):
            response.data.update(traffic_mode=exc.traffic_mode, traffic_action=exc.traffic_action)
        response['Retry-After'] = str(wait)
    return response
