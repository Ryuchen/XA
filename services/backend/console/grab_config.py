"""Version-checked automatic grab window; configuration does not enable dispatch."""
import json
from collections.abc import Mapping

from django.db import IntegrityError
from rest_framework.response import Response
from rest_framework.views import APIView
from stores.db import transaction

from orders.grab_config import CONFIG_KEY, MAX_WINDOW_SECONDS, current_rules, parse_rules
from wallet.models import SystemConfig
from .models import AdminAuditLog
from .permissions import IsConsoleUser, get_account_permissions


class GrabConfigError(ValueError):
    def __init__(self, message, status=409, code='CONFIG_CHANGED'):
        super().__init__(message)
        self.status, self.code = status, code


def save_grab_window(seconds, version, audit):
    if (type(seconds) is not int or not 1 <= seconds <= MAX_WINDOW_SECONDS
            or type(version) is not int or version < 0):
        raise GrabConfigError('抢单窗口须为 1～86400 秒的整数，版本须为非负整数', 400, 'INVALID_CONFIG')
    try:
        with transaction.atomic():
            if version and not SystemConfig.objects.filter(key=CONFIG_KEY).exists():
                raise GrabConfigError('抢单窗口已变化，请刷新后重新核对')
            row, created = SystemConfig.objects.get_or_create(key=CONFIG_KEY, defaults={
                'value':json.dumps({'window_seconds':seconds, 'version':1}), 'remark':'本店自动抢单窗口秒数'})
            row = SystemConfig.objects.select_for_update().get(pk=row.pk)
            if created:
                if version != 0:
                    raise GrabConfigError('抢单窗口已变化，请刷新后重新核对')
                old = parse_rules(None)
                new = parse_rules(row.value)
            else:
                old = parse_rules(row.value)
                if old['version'] != version:
                    raise GrabConfigError('抢单窗口已变化，请刷新后重新核对')
                new = {'window_seconds':seconds, 'version':version+1}
                row.value = json.dumps(new, separators=(',', ':'))
                row.save(update_fields=['value', 'updated_at'])
            audit(row, old, new)
            return new
    except IntegrityError:
        raise GrabConfigError('抢单窗口已变化，请刷新后重新核对') from None


class GrabConfigView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        if not {'order:view', 'dictionary:view', 'dictionary:edit'}.intersection(get_account_permissions(request.account)):
            return Response({'code':403, 'msg':'无权查看抢单窗口'}, status=403)
        try:
            data = current_rules()
        except ValueError as exc:
            return Response({'code':409, 'msg':str(exc)}, status=409)
        return Response({'code':0, 'data':{**data, 'automatic_dispatch_available':False}})

    def put(self, request):
        if 'dictionary:edit' not in get_account_permissions(request.account):
            return Response({'code':403, 'msg':'无权修改抢单窗口'}, status=403)
        if not isinstance(request.data, Mapping) or set(request.data) != {'window_seconds', 'version'}:
            return Response({'code':400, 'msg':'请填写抢单窗口和配置版本'}, status=400)
        try:
            def audit(row, old, new):
                AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
                    operator_name=(request.account.nickname or request.account.username)[:150], method='PUT',
                    path=request.path[:255], resource='automatic-grab-window', object_id=str(row.pk),
                    request_body={'before':old, 'after':new}, status_code=200)
            data = save_grab_window(request.data['window_seconds'], request.data['version'], audit)
        except GrabConfigError as exc:
            return Response({'code':exc.status, 'errorCode':exc.code, 'msg':str(exc)}, status=exc.status)
        except ValueError as exc:
            return Response({'code':409, 'msg':str(exc)}, status=409)
        request._request._business_audit_recorded = True
        return Response({'code':0, 'data':{**data, 'automatic_dispatch_available':False}, 'msg':'抢单窗口已保存'})
