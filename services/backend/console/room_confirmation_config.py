"""Versioned, store-local configuration for room confirmation waits."""
import json
from collections.abc import Mapping

from django.db import IntegrityError
from rest_framework.response import Response
from rest_framework.views import APIView
from stores.db import transaction

from orders.room_confirmation_config import CONFIG_KEY, MAX_MINUTES, current_rules, parse_rules
from wallet.models import SystemConfig
from .models import AdminAuditLog
from .permissions import IsConsoleUser, get_account_permissions


class RoomConfirmationConfigError(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def save_room_confirmation(minutes, version, audit):
    if (type(minutes) is not int or not 1 <= minutes <= MAX_MINUTES or type(version) is not int
            or not 0 <= version < 2147483647):
        raise RoomConfirmationConfigError('确认时限须为有效整数分钟，版本须为非负整数', 400)
    try:
        with transaction.atomic():
            if version and not SystemConfig.objects.filter(key=CONFIG_KEY).exists():
                raise RoomConfirmationConfigError('房间确认时限已变化，请刷新后重新核对')
            initial = {'minutes': minutes, 'version': 1}
            row, created = SystemConfig.objects.get_or_create(key=CONFIG_KEY, defaults={
                'value': json.dumps(initial, separators=(',', ':')), 'remark': '房间待老板确认时限（分钟）'})
            row = SystemConfig.objects.select_for_update().get(pk=row.pk)
            old = parse_rules(None if created else row.value)
            if old['version'] != version:
                raise RoomConfirmationConfigError('房间确认时限已变化，请刷新后重新核对')
            new = {'minutes': minutes, 'version': version + 1}
            if not created:
                row.value = json.dumps(new, separators=(',', ':'))
                row.save(update_fields=['value', 'updated_at'])
            audit(row, old, new)
            return new
    except IntegrityError:
        raise RoomConfirmationConfigError('房间确认时限已变化，请刷新后重新核对') from None


class RoomConfirmationConfigView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        if not {'order:view', 'dictionary:view', 'dictionary:edit'}.intersection(get_account_permissions(request.account)):
            return Response({'code': 403, 'msg': '无权查看房间确认时限'}, status=403)
        try:
            return Response({'code': 0, 'data': current_rules()})
        except ValueError as exc:
            return Response({'code': 409, 'msg': str(exc)}, status=409)

    def put(self, request):
        if 'dictionary:edit' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无权修改房间确认时限'}, status=403)
        if not isinstance(request.data, Mapping) or set(request.data) != {'minutes', 'version'}:
            return Response({'code': 400, 'msg': '请填写房间确认时限和版本'}, status=400)
        def audit(row, old, new):
            AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
                operator_name=(request.account.nickname or request.account.username)[:150], method='PUT',
                path=request.path[:255], resource='room-confirmation-deadline', object_id=str(row.pk),
                request_body={'before': old, 'after': new}, status_code=200)
        try:
            data = save_room_confirmation(**request.data, audit=audit)
        except RoomConfirmationConfigError as exc:
            return Response({'code': exc.status, 'msg': str(exc)}, status=exc.status)
        except ValueError as exc:
            return Response({'code': 409, 'msg': str(exc)}, status=409)
        request._request._business_audit_recorded = True
        return Response({'code': 0, 'data': data, 'msg': '房间确认时限已保存'})
