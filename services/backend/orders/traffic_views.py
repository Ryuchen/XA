from django.core.cache import cache
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.permissions import IsClubAccountAuthenticated
from console.permissions import IsConsoleUser, get_account_permissions
from console.kook_dispatch import ConfigConflict
from .traffic import CONFIG_KEY, FIELDS, config_data, current_mode, in_hours, metric, metrics
from .traffic_models import OrderTrafficConfig


class LimitsInput(serializers.Serializer):
    account_per_minute = serializers.IntegerField(min_value=1, max_value=10000)
    global_per_minute = serializers.IntegerField(min_value=1, max_value=1000000)
    global_per_second = serializers.IntegerField(min_value=1, max_value=100000)


class TrafficConfigInput(serializers.Serializer):
    revision = serializers.IntegerField(min_value=0)
    enabled = serializers.BooleanField()
    self_order_enabled = serializers.BooleanField()
    peak_start = serializers.IntegerField(min_value=0, max_value=23)
    peak_end = serializers.IntegerField(min_value=0, max_value=24)
    valley_start = serializers.IntegerField(min_value=0, max_value=23)
    valley_end = serializers.IntegerField(min_value=0, max_value=24)
    limits = serializers.DictField()

    def validate(self, attrs):
        for prefix in ('peak', 'valley'):
            if attrs[prefix + '_start'] == attrs[prefix + '_end']:
                raise serializers.ValidationError('时段起止不能相同')
        if any(in_hours(h, attrs['peak_start'], attrs['peak_end']) and in_hours(h, attrs['valley_start'], attrs['valley_end']) for h in range(24)):
            raise serializers.ValidationError('波峰与波谷时段不能重叠')
        normalized = {}
        if set(attrs['limits']) != {'peak', 'valley', 'normal'}:
            raise serializers.ValidationError('请设置波峰、波谷和常规三套配额')
        for mode, limits in attrs['limits'].items():
            if not isinstance(limits, dict) or set(limits) != {'create', 'grab', 'pool', 'quote'}:
                raise serializers.ValidationError('每个时段必须包含下单、抢单、刷新和报价配额')
            normalized[mode] = {}
            for action, values in limits.items():
                serializer = LimitsInput(data=values)
                serializer.is_valid(raise_exception=True)
                normalized[mode][action] = serializer.validated_data
        attrs['limits'] = normalized
        return attrs


class TrafficConfigView(APIView):
    permission_classes = [IsConsoleUser]

    def require(self, request, permission):
        if permission not in get_account_permissions(request.account):
            raise PermissionDenied('无权操作订单流量配置')

    def get(self, request):
        self.require(request, 'order:view')
        config = config_data()
        return Response({'code': 0, 'data': {**config, 'mode': current_mode(config), 'metrics': metrics()}})

    def put(self, request):
        self.require(request, 'order:traffic')
        serializer = TrafficConfigInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        with transaction.atomic():
            OrderTrafficConfig.objects.get_or_create(pk=1)
            row = OrderTrafficConfig.objects.select_for_update().get(pk=1)
            if row.revision != data.pop('revision'):
                raise ConfigConflict()
            for field, value in data.items():
                setattr(row, field, value)
            row.revision += 1
            row.save()
            # 窗口计数不随保存清空，避免配置修改期间绕过已有配额。
            transaction.on_commit(lambda: cache.delete(CONFIG_KEY))
        return Response({'code': 0, 'data': {field: getattr(row, field) for field in FIELDS}})


class TrafficStatusView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        config = config_data()
        return Response({'code': 0, 'data': {'mode': current_mode(config), 'self_order_enabled': config['self_order_enabled']}})


class NetworkInput(serializers.Serializer):
    samples = serializers.IntegerField(min_value=1, max_value=60)
    failures = serializers.IntegerField(min_value=0, max_value=60)
    average_ms = serializers.IntegerField(min_value=0, max_value=15000)
    jitter_ms = serializers.IntegerField(min_value=0, max_value=15000)

    def validate(self, data):
        if data['failures'] > data['samples']:
            raise serializers.ValidationError('失败数不能超过样本数')
        return data


class NetworkReportView(APIView):
    permission_classes = [IsClubAccountAuthenticated]
    throttle_scope = 'network_report'

    def post(self, request):
        if request.account.account_type != 'PROVIDER':
            raise PermissionDenied('仅陪玩可上报抢单网络状态')
        serializer = NetworkInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        metric('network_reports')
        metric('latency_sum', data['average_ms'])
        metric('jitter_sum', data['jitter_ms'])
        metric('network_samples', data['samples'])
        metric('network_failures', data['failures'])
        return Response({'code': 0, 'data': {'received': True}})
