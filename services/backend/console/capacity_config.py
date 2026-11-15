"""Store-scoped, version-checked provider capacity configuration."""
import json

from django.db import IntegrityError
from rest_framework.response import Response
from rest_framework.views import APIView
from stores.db import transaction

from orders.capacity import CONFIG_KEY, MAX_CAPACITY, current_capacity_rules, parse_rules
from wallet.models import SystemConfig
from .models import AdminAuditLog
from .permissions import IsConsoleUser, get_account_permissions


class CapacityConfigError(ValueError):
    def __init__(self, message, status=409, code='CONFIG_CHANGED'):
        super().__init__(message)
        self.status, self.code = status, code


def save_capacity(limit, version, audit):
    """Save once under the shared admission lock; audit is in the same transaction."""
    if (type(limit) is not int or not 1 <= limit <= MAX_CAPACITY
            or type(version) is not int or version < 0):
        raise CapacityConfigError('接单上限须为正整数，配置版本须为非负整数', 400, 'INVALID_CONFIG')
    try:
        with transaction.atomic():
            default = parse_rules(None)
            if version != 0 and not SystemConfig.objects.filter(key=CONFIG_KEY).exists():
                raise CapacityConfigError('接单名额已变化，请刷新后重新核对')
            row, _ = SystemConfig.objects.get_or_create(key=CONFIG_KEY, defaults={
                'value': json.dumps(default), 'remark': '本店陪玩同时接单名额'})
            row = SystemConfig.objects.select_for_update().get(pk=row.pk)
            old = parse_rules(row.value)
            if old['version'] != version:
                raise CapacityConfigError('接单名额已变化，请刷新后重新核对')
            new = {'max_concurrent_orders': limit, 'version': version + 1}
            row.value = json.dumps(new, separators=(',', ':'))
            fields = ['value', 'updated_at']
            if row.remark == '本店陪玩同时接单名额（尚未配置）':
                row.remark = '本店陪玩同时接单名额'
                fields.append('remark')
            row.save(update_fields=fields)
            audit(row, old, new)
            return new
    except IntegrityError:
        raise CapacityConfigError('接单名额已变化，请刷新后重新核对') from None


class CapacityConfigView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        if not {'order:view', 'dictionary:view', 'dictionary:edit'}.intersection(get_account_permissions(request.account)):
            return Response({'code': 403, 'msg': '无权查看接单名额'}, status=403)
        try:
            rules = current_capacity_rules()
        except ValueError as exc:
            return Response({'code': 409, 'msg': str(exc)}, status=409)
        return Response({'code': 0, 'data': rules})

    def put(self, request):
        if 'dictionary:edit' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无权修改接单名额'}, status=403)
        try:
            def audit(row, old, new):
                AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
                    operator_name=(request.account.nickname or request.account.username)[:150],
                    method='PUT', path=request.path[:255], resource='provider-capacity', object_id=str(row.pk),
                    request_body={'before': old, 'after': new}, status_code=200)
            new = save_capacity(request.data.get('max_concurrent_orders'), request.data.get('version'), audit)
        except CapacityConfigError as exc:
            return Response({'code': exc.status, 'errorCode': exc.code, 'msg': str(exc)}, status=exc.status)
        except ValueError as exc:
            return Response({'code': 409, 'msg': str(exc)}, status=409)
        request._request._business_audit_recorded = True
        return Response({'code': 0, 'data': new, 'msg': '接单名额已保存'})
