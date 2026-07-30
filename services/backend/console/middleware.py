"""后台操作审计中间件：自动记录管理员对后台的写操作。

采集范围：路径以 API_ADMIN_PREFIX 开头、方法为写操作(POST/PUT/PATCH/DELETE)、
且响应状态 < 400 的请求。请求体中的敏感字段会被脱敏后落库。
"""
import json

from .models import AdminAuditLog
from .permissions import is_console_account

WRITE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}
API_ADMIN_PREFIX = '/api/admin/'
SENSITIVE_KEYS = {'password', 'old_password', 'new_password', 'token', 'refresh', 'secret'}
MAX_BODY_CHARS = 4000


def _mask(value):
    if isinstance(value, dict):
        return {
            k: ('***' if k.lower() in SENSITIVE_KEYS else _mask(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_mask(v) for v in value]
    return value


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _parse_resource(path):
    """从 /api/admin/orders/12/refund/ 解析出 resource=orders、object_id=12。"""
    tail = path[len(API_ADMIN_PREFIX):].strip('/')
    parts = tail.split('/') if tail else []
    resource = parts[0] if parts else ''
    object_id = parts[1] if len(parts) > 1 and parts[1].isdigit() else ''
    return resource[:100], object_id[:64]


class AdminAuditMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        method = request.method
        need_audit = (
            method in WRITE_METHODS
            and path.startswith(API_ADMIN_PREFIX)
        )
        # 在视图消费请求流之前缓存 body，避免响应阶段无法再读取
        raw_body = b''
        if need_audit:
            try:
                raw_body = request.body
            except Exception:
                raw_body = b''

        response = self.get_response(request)

        if need_audit and getattr(response, 'status_code', 500) < 400:
            self._record(request, response, path, method, raw_body)
        return response

    @staticmethod
    def _record(request, response, path, method, raw_body):
        account = getattr(request, 'account', None)
        if not is_console_account(account):
            return
        body = {}
        if raw_body:
            try:
                parsed = json.loads(raw_body.decode('utf-8'))
                body = _mask(parsed) if isinstance(parsed, (dict, list)) else {}
                if len(json.dumps(body, ensure_ascii=False)) > MAX_BODY_CHARS:
                    body = {'_truncated': True}
            except (ValueError, UnicodeDecodeError):
                body = {}
        resource, object_id = _parse_resource(path)
        try:
            AdminAuditLog.objects.create(
                operator=getattr(request, 'legacy_user', None),
                operator_account=account,
                operator_name=(account.nickname or account.username)[:150],
                method=method,
                path=path[:255],
                resource=resource,
                object_id=object_id,
                request_body=body,
                status_code=response.status_code,
                ip=_client_ip(request),
            )
        except Exception:
            # 审计失败绝不阻断主流程
            pass
