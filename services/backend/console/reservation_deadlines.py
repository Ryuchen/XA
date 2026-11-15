"""Versioned, audited reservation timeout configuration."""
import json
from collections.abc import Mapping

from django.db import IntegrityError
from rest_framework.response import Response
from rest_framework.views import APIView
from stores.db import transaction

from orders.reservation_deadlines import CONFIG_KEY, MAX_MINUTES, current_rules, parse_rules, valid_window, WINDOW_DEFAULTS, CONFIG_FIELDS
from wallet.models import SystemConfig
from .models import AdminAuditLog
from .permissions import IsConsoleUser, get_account_permissions


class ReservationDeadlineConfigError(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def save_deadlines(confirmation_minutes, payment_minutes, version, audit, **window):
    window = window or dict(WINDOW_DEFAULTS)
    if (any(type(value) is not int or not 1 <= value <= MAX_MINUTES
            for value in (confirmation_minutes, payment_minutes))
            or type(version) is not int or version < 0 or not valid_window(window)):
        raise ReservationDeadlineConfigError('确认和付款时限须为有效的整数分钟，版本须为非负整数', 400)
    try:
        with transaction.atomic():
            if version and not SystemConfig.objects.filter(key=CONFIG_KEY).exists():
                raise ReservationDeadlineConfigError('预约时限已变化，请刷新后重新核对')
            initial = {'confirmation_minutes': confirmation_minutes, 'payment_minutes': payment_minutes, 'version': 1, **window}
            row, created = SystemConfig.objects.get_or_create(key=CONFIG_KEY, defaults={
                'value': json.dumps(initial, separators=(',', ':')), 'remark': '预约确认和付款时限（分钟）'})
            row = SystemConfig.objects.select_for_update().get(pk=row.pk)
            old = parse_rules(None if created else row.value)
            if old['version'] != version:
                raise ReservationDeadlineConfigError('预约时限已变化，请刷新后重新核对')
            new = {'confirmation_minutes': confirmation_minutes, 'payment_minutes': payment_minutes,
                   'version': version + 1, **window}
            if not created:
                row.value = json.dumps(new, separators=(',', ':'))
                row.save(update_fields=['value', 'updated_at'])
            audit(row, old, new)
            return new
    except IntegrityError:
        raise ReservationDeadlineConfigError('预约时限已变化，请刷新后重新核对') from None


class ReservationDeadlineConfigView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        if not {'order:view', 'dictionary:view', 'dictionary:edit'}.intersection(get_account_permissions(request.account)):
            return Response({'code': 403, 'msg': '无权查看预约时限'}, status=403)
        try:
            return Response({'code': 0, 'data': current_rules()})
        except ValueError as exc:
            return Response({'code': 409, 'msg': str(exc)}, status=409)

    def put(self, request):
        if 'dictionary:edit' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无权修改预约时限'}, status=403)
        if not isinstance(request.data, Mapping) or set(request.data) != CONFIG_FIELDS:
            return Response({'code': 400, 'msg': '请填写确认付款时限、预约时间范围和版本'}, status=400)
        def audit(row, old, new):
            AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
                operator_name=(request.account.nickname or request.account.username)[:150], method='PUT',
                path=request.path[:255], resource='reservation-deadlines', object_id=str(row.pk),
                request_body={'before': old, 'after': new}, status_code=200)
        try:
            data = save_deadlines(**request.data, audit=audit)
        except ReservationDeadlineConfigError as exc:
            return Response({'code': exc.status, 'msg': str(exc)}, status=exc.status)
        except ValueError as exc:
            return Response({'code': 409, 'msg': str(exc)}, status=409)
        request._request._business_audit_recorded = True
        return Response({'code': 0, 'data': data, 'msg': '预约时限已保存'})
