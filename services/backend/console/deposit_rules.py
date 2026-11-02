from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from wallet.club_rules import DEPOSIT_PERCENT_KEY, deposit_rules, parse_deposit_income_percent
from wallet.models import SystemConfig
from .permissions import IsConsoleUser, get_account_permissions


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
        target = str(value.validated_data['deposit_percent'])
        expected = value.validated_data.get('expected_deposit_percent')
        remark = '单抵押金占订单净收益比例(%)'
        if expected is None:
            # Compatibility for already deployed clients, which have no conflict token.
            SystemConfig.objects.update_or_create(key=DEPOSIT_PERCENT_KEY, defaults={'value': target, 'remark': remark})
        else:
            row = SystemConfig.objects.filter(key=DEPOSIT_PERCENT_KEY).first()
            if parse_deposit_income_percent(row.value if row else None) != expected:
                return Response({'code': 409, 'msg': '比例已被其他操作修改，请重新读取后核对'}, status=409)
            if row:
                changed = SystemConfig.objects.filter(pk=row.pk, value=row.value).update(value=target, remark=remark, updated_at=timezone.now())
                if not changed:
                    return Response({'code': 409, 'msg': '比例已被其他操作修改，请重新读取后核对'}, status=409)
            else:
                try:
                    with transaction.atomic():
                        SystemConfig.objects.create(key=DEPOSIT_PERCENT_KEY, value=target, remark=remark)
                except IntegrityError:
                    return Response({'code': 409, 'msg': '比例已被其他操作修改，请重新读取后核对'}, status=409)
        return Response({'code': 0, 'data': {**deposit_rules(), 'conditional_write_supported': True}, 'msg': '规则已保存，对之后的收益结算生效'})
