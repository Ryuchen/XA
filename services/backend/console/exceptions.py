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
    return response
