import logging
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import Throttled, ValidationError
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from club_accounts.permissions import IsClubAccountAuthenticated

from .models import AdminAuditLog
from .order_export import audit_filters, build_order_csv, export_filter_facts, filters_digest
from .permissions import get_account_permissions, is_console_account
from .serializers import AdminOrderWithIdentitySerializer
from .views import OrderViewSet
from stores.context import current_database, current_store


logger = logging.getLogger(__name__)
MAX_ORDER_EXPORT_ROWS = 5000
ORDER_EXPORT_DENIAL_RATE = '20/min'


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip() or None
    return request.META.get('REMOTE_ADDR')


def _record_export_audit(request, *, export_id, filters, filter_digest, status_code, result, row_count=None):
    account = getattr(request, 'account', None)
    body = {
        'action': 'export',
        'export_id': export_id,
        'filters': filters,
        'filters_digest': filter_digest,
        'row_limit': MAX_ORDER_EXPORT_ROWS,
        'result': result,
    }
    if row_count is not None:
        body['row_count'] = row_count
    with transaction.atomic(using='default'):
        AdminAuditLog.objects.using('default').create(
            operator=getattr(request, 'legacy_user', None),
            operator_account=account,
            operator_name=(account.nickname or account.username)[:150] if account else '',
            method=request.method,
            path=request.path[:255],
            resource='order-export',
            object_id=export_id,
            request_body=body,
            status_code=status_code,
            ip=_client_ip(request),
        )


def _order_export_allowed(request):
    if getattr(settings, 'LEGACY_FORCE_AUTH_COMPAT', False) and getattr(request.user, 'is_superuser', False):
        return True
    account = getattr(request, 'account', None)
    return is_console_account(account) and 'order:export' in get_account_permissions(account)


def _audited_order_export_denial(request):
    account = getattr(request, 'account', None)
    return (
        getattr(request, 'successful_authenticator', None) is not None
        and is_console_account(account)
        and not _order_export_allowed(request)
    )


class OrderExportDenialThrottle(SimpleRateThrottle):
    """只限制会写入拒绝审计的已认证请求。"""

    scope = 'order_export_denial'

    def get_rate(self):
        return ORDER_EXPORT_DENIAL_RATE

    def get_cache_key(self, request, view):
        if not _audited_order_export_denial(request):
            return None
        account = request.account
        database = current_database() or account._state.db or 'default'
        ident = f'account:{database}:{account.pk}'
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class OrderExportPermission(BasePermission):
    message = '无订单导出权限'

    def has_permission(self, request, view):
        if getattr(request, 'successful_authenticator', None) is None:
            return False
        if _order_export_allowed(request):
            return True
        if not is_console_account(getattr(request, 'account', None)):
            return False
        # DRF 在权限检查之后才执行视图 throttle；这里必须先限制再写审计。
        throttle = OrderExportDenialThrottle()
        if not throttle.allow_request(request, view):
            raise Throttled(wait=throttle.wait())
        export_id = str(uuid4())
        try:
            _record_export_audit(
                request, export_id=export_id, filters=audit_filters(request.query_params),
                filter_digest=filters_digest(export_filter_facts(request.query_params)),
                status_code=status.HTTP_403_FORBIDDEN, result='PERMISSION_DENIED',
            )
        except Exception:
            logger.warning('订单导出拒绝审计写入失败: export_id=%s', export_id, exc_info=True)
        return False


class StaffOrderExportView(APIView):
    permission_classes = [IsClubAccountAuthenticated, OrderExportPermission]

    def get(self, request):
        store = current_store()
        if store is None:
            return Response({'code': 404, 'msg': '缺少所属门店，无法导出订单'}, status=404)
        filters = audit_filters(request.query_params)
        filter_digest = filters_digest(export_filter_facts(request.query_params))
        export_id = str(uuid4())
        order_view = OrderViewSet()
        order_view.action = 'export'
        order_view.request = request
        order_view.format_kwarg = None
        try:
            queryset = order_view.get_queryset()
            orders = list(queryset[:MAX_ORDER_EXPORT_ROWS + 1])
        except ValidationError:
            _record_export_audit(
                request, export_id=export_id, filters=filters, filter_digest=filter_digest,
                status_code=status.HTTP_400_BAD_REQUEST, result='INVALID_FILTERS',
            )
            return Response({'code': 400, 'msg': '订单筛选条件无效，请核对后重试'}, status=400)
        if len(orders) > MAX_ORDER_EXPORT_ROWS:
            _record_export_audit(
                request, export_id=export_id, filters=filters, filter_digest=filter_digest,
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, result='ROW_LIMIT_EXCEEDED',
                row_count=MAX_ORDER_EXPORT_ROWS + 1,
            )
            return Response(
                {'code': 413, 'msg': f'导出结果超过 {MAX_ORDER_EXPORT_ROWS} 条，请缩小筛选范围后重试'},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        rows = AdminOrderWithIdentitySerializer(
            orders, many=True, context={'request': request},
        ).data
        account = request.account
        exported_at = timezone.now()
        try:
            content = build_order_csv(
                rows, store_slug=store.slug, store_name=store.name,
                operator_name=account.nickname or account.username, operator_id=account.pk,
                export_id=export_id, exported_at=exported_at, filters=export_filter_facts(request.query_params),
            )
        except Exception:
            logger.exception('订单导出文件生成失败: export_id=%s', export_id)
            _record_export_audit(
                request, export_id=export_id, filters=filters, filter_digest=filter_digest,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, result='EXPORT_FAILED',
            )
            return Response({'code': 500, 'msg': '导出文件生成失败，请稍后重试'}, status=500)
        try:
            _record_export_audit(
                request, export_id=export_id, filters=filters, filter_digest=filter_digest,
                status_code=status.HTTP_200_OK, result='SUCCESS', row_count=len(rows),
            )
        except Exception:
            logger.exception('订单导出审计写入失败，已阻止文件返回: export_id=%s', export_id)
            return Response({'code': 500, 'msg': '导出审计写入失败，文件未生成'}, status=500)
        filename = f'XA-orders-{timezone.localtime(exported_at).strftime("%Y%m%d-%H%M%S")}-{export_id[:8]}.csv'
        response = HttpResponse(content, content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['Cache-Control'] = 'no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        response['X-Export-Id'] = export_id
        return response
