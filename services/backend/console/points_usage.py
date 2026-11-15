"""Audited editing of the three store-local point usage switches."""
import json
from collections.abc import Mapping

from django.db import IntegrityError
from rest_framework.response import Response
from rest_framework.views import APIView
from stores.db import transaction

from engagement.points_usage import FIELDS, current_usage, enabled, lock_usage
from .models import AdminAuditLog
from .permissions import IsConsoleUser, get_account_permissions


class PointsUsageChangeError(Exception):
    def __init__(self, message, status, code):
        super().__init__(message)
        self.status, self.code = status, code


def save_points_usage(data, *, may_edit_issuance, audit):
    if (not isinstance(data, Mapping) or set(data) != {*FIELDS, 'version', 'issuance_enabled', 'expected_issuance_enabled'}
            or any(type(data[f]) is not bool for f in FIELDS)
            or type(data['issuance_enabled']) is not bool or type(data['expected_issuance_enabled']) is not bool
            or type(data['version']) is not int or not 0 <= data['version'] < 2147483647):
        raise PointsUsageChangeError('请重新读取并填写积分开关和配置版本', 400, 'INVALID_CONFIG')
    with transaction.atomic():
        operation, row, old = lock_usage()
        if old['version'] != data['version'] or operation.points_enabled != data['expected_issuance_enabled']:
            raise PointsUsageChangeError('积分使用开关已变化，请刷新后核对', 409, 'CONFIG_CHANGED')
        old_issuance, new_issuance = operation.points_enabled, data['issuance_enabled']
        if old_issuance != new_issuance and not may_edit_issuance:
            raise PointsUsageChangeError('无权修改积分发放开关', 403, 'FORBIDDEN')
        new = {**{field: data[field] for field in FIELDS}, 'version': old['version'] + 1}
        row.value = json.dumps(new, separators=(',', ':'))
        row.save(update_fields=['value', 'updated_at'])
        if old_issuance != new_issuance:
            operation.points_enabled = new_issuance
            operation.save(update_fields=['points_enabled', 'updated_at'])
        before, after = {**old, 'issuance_enabled': old_issuance}, {**new, 'issuance_enabled': new_issuance}
        audit(row, before, after)
        return {**new, 'issuance_enabled': new_issuance, 'issuance_configured': True,
                'usage_enabled': bool(new_issuance and all(new[f] for f in FIELDS)),
                'enforcement_available': True}


class PointsUsageConfigView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        if not {'dictionary:view', 'dictionary:edit'}.intersection(get_account_permissions(request.account)):
            return Response({'code': 403, 'msg': '无权查看积分使用开关'}, status=403)
        if not enabled():
            return Response({'code': 0, 'data': {'enforcement_available': False}})
        try:
            issuance, rules = current_usage()
        except ValueError as exc:
            return Response({'code': 409, 'msg': str(exc)}, status=409)
        issuance_effective = True if issuance is None else issuance
        return Response({'code': 0, 'data': {**rules, 'issuance_enabled': issuance_effective,
                         'issuance_configured': issuance is not None,
                         'usage_enabled': bool(issuance_effective and all(rules[f] for f in FIELDS)),
                         'enforcement_available': True}})

    def put(self, request):
        if 'dictionary:edit' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无权修改积分使用开关'}, status=403)
        if not enabled():
            return Response({'code': 409, 'msg': '积分使用联动尚未开放'}, status=409)
        try:
            def audit(row, before, after):
                AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
                    operator_name=(request.account.nickname or request.account.username)[:150], method='PUT',
                    path=request.path[:255], resource='points-usage-switches', object_id=str(row.pk),
                    request_body={'before': before, 'after': after}, status_code=200)
            data = save_points_usage(request.data,
                may_edit_issuance='engagement:edit' in get_account_permissions(request.account), audit=audit)
        except PointsUsageChangeError as exc:
            return Response({'code': exc.status, 'errorCode': exc.code, 'msg': str(exc)}, status=exc.status)
        except IntegrityError:
            return Response({'code': 409, 'msg': '配置已变化，请刷新后核对'}, status=409)
        except ValueError as exc:
            return Response({'code': 409, 'msg': str(exc)}, status=409)
        request._request._business_audit_recorded = True
        return Response({'code': 0, 'data': data})
