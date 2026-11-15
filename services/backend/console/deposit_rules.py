from django.db import IntegrityError
from stores.db import transaction
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
import json
import hashlib

from wallet.club_rules import (DEFAULT_DEPOSIT_TARGETS, DEPOSIT_PERCENT_KEY, DEPOSIT_TARGET_KEY,
    deposit_rules, parse_deposit_targets, parse_deposit_income_percent)
from wallet.models import SystemConfig
from .models import AdminAuditLog
from .permissions import IsConsoleUser, get_account_permissions


class DepositConfigError(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def deposit_percent_snapshot(row):
    identity = None if row is None else {'id': row.pk, 'value': row.value, 'updated_at': row.updated_at.isoformat()}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def current_deposit_setup():
    row = SystemConfig.objects.filter(key=DEPOSIT_PERCENT_KEY).first()
    raw = row.value if row else None
    valid = (isinstance(raw, str) and raw.isascii() and raw.isdigit()
             and len(raw) <= 10 and int(raw) <= 100)
    return {'targets': deposit_rules(), 'percent_fingerprint': deposit_percent_snapshot(row),
            'percent_state': 'VALID' if valid else ('MISSING' if row is None else 'INVALID')}


def save_deposit_targets(data, audit=None):
    if (any(type(data.get(k)) is not int or not 1 <= data[k] <= 2147483647
            for k in ('cash_target', 'earnings_target'))
            or type(data.get('expected_version')) is not int or data['expected_version'] < 0):
        raise DepositConfigError('请填写有效的押金目标及配置版本', 400)
    try:
        with transaction.atomic():
            row, _ = SystemConfig.objects.get_or_create(key=DEPOSIT_TARGET_KEY,
                defaults={'value': json.dumps(DEFAULT_DEPOSIT_TARGETS), 'remark': '门店陪玩押金目标'})
            row = SystemConfig.objects.select_for_update().get(pk=row.pk)
            current = parse_deposit_targets(row.value)
            if current['version'] != data['expected_version']:
                raise DepositConfigError('押金目标已变化，请刷新后核对')
            targets = {'cash_target': data['cash_target'], 'earnings_target': data['earnings_target'],
                       'version': current['version'] + 1}
            row.value = json.dumps(targets, separators=(',', ':'))
            row.save(update_fields=['value', 'updated_at'])
            if audit:
                audit(row, current, targets)
            return targets
    except IntegrityError:
        raise DepositConfigError('押金目标已变化，请刷新后核对') from None


def save_platform_deposit_percent(percent, fingerprint, audit):
    if type(percent) is not int or not 0 <= percent <= 100 or not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise DepositConfigError('请填写有效的单抵比例并重新读取配置', 400)
    try:
        with transaction.atomic():
            row, created = SystemConfig.objects.get_or_create(key=DEPOSIT_PERCENT_KEY,
                defaults={'value': '60', 'remark': '单抵押金占订单净收益比例(%)'})
            row = SystemConfig.objects.select_for_update().get(pk=row.pk)
            if deposit_percent_snapshot(None if created else row) != fingerprint:
                raise DepositConfigError('比例已变化，请重新读取后核对')
            before = {'raw_value': None if created else row.value,
                      'effective_percent': parse_deposit_income_percent(None if created else row.value)}
            row.value = str(percent)
            row.remark = '单抵押金占订单净收益比例(%)'
            row.save(update_fields=['value', 'remark', 'updated_at'])
            audit(row, before, {'deposit_percent': percent})
            return {'deposit_percent': percent}
    except IntegrityError:
        raise DepositConfigError('比例已变化，请重新读取后核对') from None


class DepositRulesInput(serializers.Serializer):
    deposit_percent = serializers.IntegerField(min_value=0, max_value=100)
    expected_deposit_percent = serializers.IntegerField(min_value=0, max_value=100, required=False)


class DepositRulesView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        if not {'deposit_rule:view', 'deposit_rule:edit'}.intersection(get_account_permissions(request.account)):
            return Response({'code': 403, 'msg': '无操作权限'}, status=403)
        return Response({'code': 0, 'data': {**deposit_rules(), 'conditional_write_supported': True}})

    def put(self, request):
        if 'deposit_rule:edit' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无操作权限'}, status=403)
        value = DepositRulesInput(data=request.data)
        if not value.is_valid():
            return Response({'code': 400, 'msg': '抵押金比例须为0至100的整数', 'data': value.errors}, status=400)
        percent = value.validated_data['deposit_percent']
        expected = value.validated_data.get('expected_deposit_percent')
        remark = '单抵押金占订单净收益比例(%)'
        try:
            with transaction.atomic():
                row, created = SystemConfig.objects.get_or_create(key=DEPOSIT_PERCENT_KEY,
                    defaults={'value': '60', 'remark': remark})
                row = SystemConfig.objects.select_for_update().get(pk=row.pk)
                raw = None if created else row.value
                before = parse_deposit_income_percent(raw)
                if expected is not None and before != expected:
                    raise DepositConfigError('比例已被其他操作修改，请重新读取后核对')
                row.value, row.remark = str(percent), remark
                row.save(update_fields=['value', 'remark', 'updated_at'])
                AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
                    operator_name=(request.account.nickname or request.account.username)[:150], method='PUT',
                    path=request.path[:255], resource='deposit-percent', object_id=str(row.pk),
                    request_body={'before': {'raw_value': raw, 'effective_percent': before},
                                  'after': {'deposit_percent': percent}}, status_code=200)
        except (IntegrityError, DepositConfigError):
            return Response({'code': 409, 'msg': '比例已被其他操作修改，请重新读取后核对'}, status=409)
        request._request._business_audit_recorded = True
        return Response({'code': 0, 'data': {**deposit_rules(), 'conditional_write_supported': True}, 'msg': '规则已保存，对之后的收益结算生效'})


class DepositTargetsInput(serializers.Serializer):
    cash_target = serializers.IntegerField(min_value=1, max_value=2147483647)
    earnings_target = serializers.IntegerField(min_value=1, max_value=2147483647)
    expected_version = serializers.IntegerField(min_value=0)


class DepositTargetsView(APIView):
    permission_classes = [IsConsoleUser]

    def put(self, request):
        if 'deposit_rule:edit' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无操作权限'}, status=403)
        serializer = DepositTargetsInput(data=request.data)
        if not serializer.is_valid():
            return Response({'code': 400, 'msg': '请填写有效的押金目标及配置版本', 'data': serializer.errors}, status=400)
        data = serializer.validated_data
        try:
            def audit(row, before, after):
                AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
                    operator_name=(request.account.nickname or request.account.username)[:150], method='PUT',
                    path=request.path[:255], resource='deposit-targets', object_id=str(row.pk),
                    request_body={'before': before, 'after': after}, status_code=200)
            save_deposit_targets(data, audit=audit)
        except DepositConfigError as exc:
            return Response({'code': exc.status, 'msg': str(exc)}, status=exc.status)
        request._request._business_audit_recorded = True
        return Response({'code': 0, 'data': deposit_rules(), 'msg': '门店押金目标已更新'})
