from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from wallet.club_rules import DEPOSIT_PERCENT_KEY, deposit_rules
from wallet.models import SystemConfig
from .permissions import IsConsoleUser, get_account_permissions


class DepositRulesInput(serializers.Serializer):
    deposit_percent = serializers.IntegerField(min_value=0, max_value=100)


class DepositRulesView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        if not {'deposit_rule:view', 'deposit_rule:edit'}.intersection(get_account_permissions(request.account)):
            return Response({'code': 403, 'msg': '无操作权限'}, status=403)
        return Response({'code': 0, 'data': deposit_rules()})

    def put(self, request):
        if 'deposit_rule:edit' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无操作权限'}, status=403)
        value = DepositRulesInput(data=request.data)
        if not value.is_valid():
            return Response({'code': 400, 'msg': '抵押金比例须为0至100的整数', 'data': value.errors}, status=400)
        SystemConfig.objects.update_or_create(
            key=DEPOSIT_PERCENT_KEY,
            defaults={'value': str(value.validated_data['deposit_percent']), 'remark': '单抵押金占订单净收益比例(%)'},
        )
        return Response({'code': 0, 'data': deposit_rules(), 'msg': '规则已保存，对之后的收益结算生效'})
