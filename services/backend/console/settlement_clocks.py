import json

from django.db import IntegrityError
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from stores.db import transaction

from wallet.models import SystemConfig
from wallet.settlement_clocks import CONFIG_KEY, DEFAULT_RULES, MAX_HOURS, current_rules, parse_rules
from .permissions import IsConsoleUser, get_account_permissions


class ClockInput(serializers.Serializer):
    aftersale_hours = serializers.IntegerField(min_value=0, max_value=MAX_HOURS)
    income_freeze_hours = serializers.IntegerField(min_value=0, max_value=MAX_HOURS)
    expected_version = serializers.IntegerField(min_value=0)


class ClockConfigError(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def save_clocks(value, audit=None):
    """Serialize store edits and optional auditing on the actual configuration row."""
    if (any(type(value.get(k)) is not int or not 0 <= value[k] <= MAX_HOURS
            for k in ('aftersale_hours', 'income_freeze_hours'))
            or type(value.get('expected_version')) is not int or value['expected_version'] < 0):
        raise ClockConfigError('请填写有效的整小时时限及配置版本', 400)
    try:
        with transaction.atomic():
            row, _ = SystemConfig.objects.get_or_create(key=CONFIG_KEY,
                defaults={'value': json.dumps(DEFAULT_RULES), 'remark': '门店售后与收益冻结时限'})
            row = SystemConfig.objects.select_for_update().get(pk=row.pk)
            current = parse_rules(row.value)
            if current['version'] != value['expected_version']:
                raise ClockConfigError('时限已变化，请重新读取并核对')
            new = {'aftersale_hours': value['aftersale_hours'], 'income_freeze_hours': value['income_freeze_hours'],
                   'version': current['version'] + 1}
            row.value = json.dumps(new, separators=(',', ':'))
            row.save(update_fields=['value', 'updated_at'])
            if audit:
                audit(row, current, new)
            return new
    except IntegrityError:
        raise ClockConfigError('时限已变化，请重新读取并核对') from None


class SettlementClocksView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        if not {'deposit_rule:view', 'deposit_rule:edit'}.intersection(get_account_permissions(request.account)):
            return Response({'code':403, 'msg':'无操作权限'}, status=403)
        return Response({'code':0, 'data':current_rules()})

    def put(self, request):
        if 'deposit_rule:edit' not in get_account_permissions(request.account):
            return Response({'code':403, 'msg':'无操作权限'}, status=403)
        serializer = ClockInput(data=request.data)
        if not serializer.is_valid():
            return Response({'code':400, 'msg':'请填写有效的整小时时限及配置版本', 'data':serializer.errors}, status=400)
        value = serializer.validated_data
        try:
            new = save_clocks(value)
        except ClockConfigError as exc:
            return Response({'code':exc.status, 'msg':str(exc)}, status=exc.status)
        return Response({'code':0, 'data':new, 'msg':'时限已保存，新订单按适用规则保存快照'})
