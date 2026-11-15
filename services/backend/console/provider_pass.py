"""通行证运营配置与购买记录；金额沿用整数账务单位。"""
from stores.db import transaction
from django.db.models import Q
from rest_framework import serializers
from identifiers.serializers import EntityNumberModelSerializer, EntityNumberListSerializer
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
            from users.pass_rules import lock_pass_config
            lock_pass_config()
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


class NoPassDelayView(APIView):
    """Configure the dispatch delay that applies even when pass sales stop."""
    permission_classes = [IsConsoleUser]

    def get(self, request):
        if not get_account_permissions(request.account).intersection({'escort:view', 'escort:edit'}):
            return Response({'code': 403, 'msg': '无通行证配置查看权限'}, status=403)
        key = f'{PASS_DELAY_SECONDS_KEY_PREFIX}NONE'
        return Response({'code': 0, 'data': {'delay_seconds': get_pass_delay_seconds(''),
            'configured': SystemConfig.objects.filter(key=key).exists()}})

    def put(self, request):
        if 'escort:edit' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无通行证配置权限'}, status=403)
        if (not isinstance(request.data, dict) or set(request.data) != {'delay_seconds'}
                or type(request.data['delay_seconds']) is not int
                or not 0 <= request.data['delay_seconds'] <= 86400):
            return Response({'code': 400, 'msg': '无通行证可见延迟须为 0 至 86400 秒整数'}, status=400)
        from users.pass_rules import lock_pass_config
        from .models import AdminAuditLog
        with transaction.atomic():
            lock_pass_config()
            if any(request.data['delay_seconds'] < get_pass_delay_seconds(tier)
                   for tier, _ in EscortProfile.PassTier.choices):
                return Response({'code': 400, 'msg': '无通行证延迟不能短于通行证档位延迟'}, status=400)
            before = get_pass_delay_seconds('')
            key = f'{PASS_DELAY_SECONDS_KEY_PREFIX}NONE'
            SystemConfig.objects.update_or_create(key=key,
                defaults={'value': str(request.data['delay_seconds']), 'remark': '无通行证订单可见延迟（秒）'})
            AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
                operator_name=(request.account.nickname or request.account.username)[:150], method='PUT',
                path=request.path[:255], resource='no-pass-delay', object_id=key,
                request_body={'before': before, 'after': request.data['delay_seconds']}, status_code=200)
        request._request._business_audit_recorded = True
        return Response({'code': 0, 'data': {'delay_seconds': request.data['delay_seconds'], 'configured': True}})


class ProviderPassAvailabilityView(APIView):
    """Control new sales only; existing paid passes keep their entitlement."""
    permission_classes = [IsConsoleUser]

    def get(self, request):
        if not get_account_permissions(request.account).intersection({'escort:view', 'escort:edit'}):
            return Response({'code': 403, 'msg': '无通行证查看权限'}, status=403)
        from users.pass_rules import pass_sales_enabled
        try:
            return Response({'code': 0, 'data': {'enabled': pass_sales_enabled()}})
        except ValueError as exc:
            return Response({'code': 409, 'msg': str(exc)}, status=409)

    def put(self, request):
        if 'escort:edit' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无通行证配置权限'}, status=403)
        if not isinstance(request.data, dict) or set(request.data) != {'enabled'} or type(request.data['enabled']) is not bool:
            return Response({'code': 400, 'msg': '请明确选择是否销售通行证'}, status=400)
        from users.pass_rules import PASS_SALES_ENABLED_KEY, lock_pass_config, pass_sales_enabled
        from .config_readiness import _last_confirmation, _snapshot
        from .models import AdminAuditLog
        with transaction.atomic():
            lock_pass_config()
            try:
                before = pass_sales_enabled()
                if request.data['enabled']:
                    snapshot = _snapshot('provider_pass', lock=True)
                    previous = _last_confirmation('provider_pass')
                    if (not previous or not isinstance(previous.request_body, dict)
                            or previous.request_body.get('fingerprint') != snapshot['fingerprint']):
                        raise ValueError('请先核对并确认当前通行证价格及可见延迟')
            except ValueError as exc:
                return Response({'code': 409, 'msg': str(exc)}, status=409)
            SystemConfig.objects.update_or_create(key=PASS_SALES_ENABLED_KEY,
                defaults={'value': '1' if request.data['enabled'] else '0', 'remark': '本店通行证销售开关'})
            AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
                operator_name=(request.account.nickname or request.account.username)[:150], method='PUT',
                path=request.path[:255], resource='provider-pass-sales', object_id=PASS_SALES_ENABLED_KEY,
                request_body={'before': before, 'after': request.data['enabled']}, status_code=200)
        request._request._business_audit_recorded = True
        return Response({'code': 0, 'data': {'enabled': request.data['enabled']}})


class PassPurchaseSerializer(EntityNumberModelSerializer):
    account_no = serializers.CharField(source='account.account_no', default='', read_only=True)
    nickname = serializers.CharField(source='provider.nickname', read_only=True)
    username = serializers.CharField(source='provider.username', read_only=True)
    tier_display = serializers.CharField(source='get_tier_display', read_only=True)
    tx_no = serializers.CharField(source='transaction.tx_no', read_only=True)
    refunded_at = serializers.DateTimeField(source='refund.created_at', default=None, read_only=True)
    refund_amount = serializers.IntegerField(source='refund.amount', default=None, read_only=True)
    refund_shortfall = serializers.IntegerField(source='refund.shortfall_amount', default=None, read_only=True)
    points_hundredths = serializers.IntegerField(source='point_redemption.points_hundredths', default=0, read_only=True)
    points_returned = serializers.IntegerField(source='refund.points_returned', default=0, read_only=True)

    class Meta:
        model = ProviderPassPurchase
        fields = ['id', 'account_no', 'nickname', 'username', 'tier', 'tier_display',
                  'days', 'original_amount', 'paid_amount', 'point_discount_amount', 'points_hundredths',
                  'starts_at', 'expires_at', 'created_at', 'tx_no',
                  'refunded_at', 'refund_amount', 'refund_shortfall', 'points_returned']


class ProviderPassPurchaseViewSet(EnvelopeViewSetMixin, ReadOnlyModelViewSet):
    default_perm = 'escort:view'
    serializer_class = PassPurchaseSerializer

    def get_queryset(self):
        qs = ProviderPassPurchase.objects.select_related('account', 'provider', 'transaction', 'refund', 'point_redemption').order_by('-created_at', '-id')
        keyword = self.request.query_params.get('keyword', '').strip()
        if keyword:
            qs = qs.filter(Q(account__account_no__icontains=keyword) | Q(provider__nickname__icontains=keyword)
                           | Q(provider__username__icontains=keyword) | Q(transaction__tx_no__icontains=keyword))
        tier = self.request.query_params.get('tier')
        if tier:
            qs = qs.filter(tier=tier)
        return qs
