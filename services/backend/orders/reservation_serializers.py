"""预约序列化器：入参校验与出参投影。

时间窗的业务规则（粒度 / 时长 / 提前期）统一由
``orders.reservation_services.validate_reservation_window`` 裁定，
序列化器只负责「字段存不存在、类型对不对」，避免同一条规则两处维护。
"""

from rest_framework import serializers

from club_accounts.models import ClubAccount

from .models import Reservation, ServiceItem
from .reservation_services import (
    ReservationWindowError,
    validate_reservation_window,
)


class ReservationCreateSerializer(serializers.Serializer):
    """发起预约（R1）的入参。

    ``provider_account_id`` 刻意用 account 维度命名：预约是新建能力，
    没有历史包袱，直接对齐 ``ClubAccount`` 这一主维度，不再引入
    「provider_id 到底是 legacy 用户还是业务账户」的双关。
    """

    provider_account_id = serializers.IntegerField(required=True)
    service_id = serializers.IntegerField(required=True)
    start_time = serializers.DateTimeField(required=True)
    end_time = serializers.DateTimeField(required=True)
    game_rounds = serializers.IntegerField(required=False, min_value=1, default=1)
    game_region = serializers.CharField(
        required=False, allow_blank=True, max_length=50, default='',
    )
    game_nickname = serializers.CharField(
        required=False, allow_blank=True, max_length=50, default='',
    )
    game_uid = serializers.CharField(
        required=False, allow_blank=True, max_length=50, default='',
    )
    remark = serializers.CharField(
        required=False, allow_blank=True, max_length=255, default='',
    )

    def validate_provider_account_id(self, value: int) -> int:
        account = ClubAccount.objects.filter(
            id=value,
            account_type=ClubAccount.AccountType.PROVIDER,
        ).first()
        if account is None:
            raise serializers.ValidationError('指定的大神不存在')
        if not account.is_active:
            raise serializers.ValidationError('指定的大神当前不可预约')
        self.context['provider_account'] = account
        return value

    def validate_service_id(self, value: int) -> int:
        service = ServiceItem.objects.filter(id=value, is_active=True).first()
        if service is None:
            raise serializers.ValidationError('服务不存在或不可用')
        self.context['service'] = service
        return value

    def validate(self, attrs):
        try:
            duration = validate_reservation_window(
                attrs['start_time'], attrs['end_time'],
            )
        except ReservationWindowError as exc:
            raise serializers.ValidationError(str(exc)) from exc

        attrs['duration_minutes'] = duration
        attrs['provider_account'] = self.context['provider_account']
        attrs['service'] = self.context['service']
        return attrs


class ReservationSerializer(serializers.ModelSerializer):
    """预约详情/列表出参。"""

    provider = serializers.SerializerMethodField()
    customer = serializers.SerializerMethodField()
    service = serializers.SerializerMethodField()
    order_no = serializers.CharField(
        source='order.order_no', read_only=True, default='',
    )
    allowed_actions = serializers.SerializerMethodField()

    class Meta:
        model = Reservation
        fields = [
            'id',
            'reservation_no',
            'status',
            'start_time',
            'end_time',
            'duration_minutes',
            'game_rounds',
            'estimated_amount',
            'game_region',
            'game_nickname',
            'game_uid',
            'remark',
            'cancel_reason',
            'reject_reason',
            'customer',
            'provider',
            'service',
            'order',
            'order_no',
            'allowed_actions',
            'confirmed_at',
            'rejected_at',
            'cancelled_at',
            'converted_at',
            'expired_at',
            'created_at',
            'updated_at',
        ]

    def get_provider(self, obj: Reservation):
        return {
            'account_id': obj.provider_account_id,
            'legacy_user_id': obj.provider_id,
            'name': obj.provider_name_snapshot,
        }

    def get_customer(self, obj: Reservation):
        return {
            'account_id': obj.customer_account_id,
            'legacy_user_id': obj.customer_id,
            'name': (
                obj.customer.nickname or obj.customer.username
                if obj.customer_id else ''
            ),
        }

    def get_service(self, obj: Reservation):
        return {
            'id': obj.service_id,
            'name': obj.service_name_snapshot,
            'price': obj.service.price if obj.service_id else 0,
        }

    def get_allowed_actions(self, obj: Reservation):
        """当前查看者可执行的动作，前端据此决定按钮显隐。

        只按「状态 + 身份」给建议，真正的权限判定仍在各视图里做 —— 这里
        少给了不会造成越权，多给了也只会在点下去时被拒。
        """
        request = self.context.get('request')
        account = getattr(request, 'account', None)
        if account is None or obj.is_terminal:
            return []

        actions = []
        is_customer = obj.customer_account_id == account.pk
        is_provider = obj.provider_account_id == account.pk
        is_staff = account.account_type == ClubAccount.AccountType.STAFF

        if obj.status == Reservation.Status.PENDING:
            if is_provider or is_staff:
                actions.extend(['confirm', 'reject'])
            if is_customer or is_staff:
                actions.append('cancel')
        elif obj.status == Reservation.Status.CONFIRMED:
            if is_customer or is_provider or is_staff:
                actions.append('cancel')
            if is_customer or is_staff:
                actions.append('convert')
        return actions


class ReservationCancelSerializer(serializers.Serializer):
    """取消 / 拒绝预约的入参：理由可空，但一旦填写就要落库存档。"""

    reason = serializers.CharField(
        required=False, allow_blank=True, max_length=255, default='',
    )
