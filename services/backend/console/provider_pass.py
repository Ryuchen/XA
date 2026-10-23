"""通行证运营配置与购买记录；金额沿用整数账务单位。"""
from django.db import transaction
from django.db.models import Q
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ReadOnlyModelViewSet

from users.models import EscortProfile, ProviderPassPurchase
from wallet.models import (
    SystemConfig, PASS_DAILY_PRICE_KEY_PREFIX, PASS_DELAY_SECONDS_KEY_PREFIX,
    get_pass_daily_price, get_pass_delay_seconds,
)
from .mixins import EnvelopeViewSetMixin
from .permissions import IsConsoleUser, get_account_permissions


class PassTierInput(serializers.Serializer):
    tier = serializers.ChoiceField(choices=EscortProfile.PassTier.choices)
    daily_price = serializers.IntegerField(min_value=1, max_value=2147483647)
    delay_seconds = serializers.IntegerField(min_value=0, max_value=86400)


class PassConfigInput(serializers.Serializer):
    products = PassTierInput(many=True)
    no_pass_delay_seconds = serializers.IntegerField(min_value=0, max_value=86400)

    def validate(self, attrs):
        tiers = [tier for tier, _ in EscortProfile.PassTier.choices]
        products = attrs['products']
        if len(products) != len(tiers) or {item['tier'] for item in products} != set(tiers):
            raise serializers.ValidationError('请完整配置四种通行证，每种只能出现一次')
        values = {item['tier']: item for item in products}
        delays = [values[tier]['delay_seconds'] for tier in tiers] + [attrs['no_pass_delay_seconds']]
        if delays != sorted(delays):
            raise serializers.ValidationError('可见延迟应按黑卡、金卡、银卡、铜卡、无卡依次递增或相同')
        return attrs


def pass_config_data():
    return {
        'products': [
            {'tier': tier, 'name': name, 'daily_price': get_pass_daily_price(tier),
             'delay_seconds': get_pass_delay_seconds(tier)}
            for tier, name in EscortProfile.PassTier.choices
        ],
        'no_pass_delay_seconds': get_pass_delay_seconds(''),
    }


class ProviderPassConfigView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        if not get_account_permissions(request.account).intersection({'escort:view', 'escort:edit'}):
            return Response({'code': 403, 'msg': '无通行证查看权限'}, status=403)
        return Response({'code': 0, 'data': pass_config_data()})

    def put(self, request):
        if 'escort:edit' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无通行证配置权限'}, status=403)
        serializer = PassConfigInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            for product in data['products']:
                for prefix, field in [(PASS_DAILY_PRICE_KEY_PREFIX, 'daily_price'), (PASS_DELAY_SECONDS_KEY_PREFIX, 'delay_seconds')]:
                    SystemConfig.objects.update_or_create(
                        key=f"{prefix}{product['tier']}", defaults={'value': str(product[field])},
                    )
            SystemConfig.objects.update_or_create(
                key=f'{PASS_DELAY_SECONDS_KEY_PREFIX}NONE',
                defaults={'value': str(data['no_pass_delay_seconds'])},
            )
        return Response({'code': 0, 'data': pass_config_data(), 'msg': '通行证规则已更新'})


class PassPurchaseSerializer(serializers.ModelSerializer):
    account_no = serializers.CharField(source='account.account_no', default='', read_only=True)
    nickname = serializers.CharField(source='provider.nickname', read_only=True)
    username = serializers.CharField(source='provider.username', read_only=True)
    tier_display = serializers.CharField(source='get_tier_display', read_only=True)
    tx_no = serializers.CharField(source='transaction.tx_no', read_only=True)

    class Meta:
        model = ProviderPassPurchase
        fields = ['id', 'account_no', 'nickname', 'username', 'tier', 'tier_display',
                  'days', 'paid_amount', 'starts_at', 'expires_at', 'created_at', 'tx_no']


class ProviderPassPurchaseViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    default_perm = 'escort:view'
    serializer_class = PassPurchaseSerializer

    def get_queryset(self):
        qs = ProviderPassPurchase.objects.select_related('account', 'provider', 'transaction').order_by('-created_at', '-id')
        keyword = self.request.query_params.get('keyword', '').strip()
        if keyword:
            qs = qs.filter(Q(account__account_no__icontains=keyword) | Q(provider__nickname__icontains=keyword)
                           | Q(provider__username__icontains=keyword) | Q(transaction__tx_no__icontains=keyword))
        tier = self.request.query_params.get('tier')
        if tier:
            qs = qs.filter(tier=tier)
        return qs
