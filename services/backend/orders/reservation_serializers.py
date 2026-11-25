"""预约序列化器：入参校验与出参投影。

时间窗的业务规则（粒度 / 时长 / 提前期）统一由
``orders.reservation_services.validate_reservation_window`` 裁定，
序列化器只负责「字段存不存在、类型对不对」，避免同一条规则两处维护。
"""

from rest_framework import serializers
from identifiers.serializers import EntityNumberModelSerializer, EntityNumberListSerializer

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

    provider_account_id = serializers.IntegerField(required=False, min_value=1)
    provider_account_ids = serializers.ListField(child=serializers.IntegerField(min_value=1),
                                                  required=False, min_length=1, max_length=2)
    service_id = serializers.IntegerField(required=True)
    customer_account_id = serializers.IntegerField(required=False, min_value=1)
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

    def validate_provider_account_ids(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError('不能重复选择同一位陪玩')
        accounts = ClubAccount.objects.filter(id__in=value, account_type=ClubAccount.AccountType.PROVIDER, is_active=True)
        by_id = {account.pk: account for account in accounts}
        if len(by_id) != len(value):
            raise serializers.ValidationError('陪玩不存在或不可预约')
        self.context['provider_accounts'] = [by_id[pk] for pk in value]
        return value

    def validate(self, attrs):
        try:
            duration = validate_reservation_window(
                attrs['start_time'], attrs['end_time'],
            )
        except (ReservationWindowError, ValueError) as exc:
            raise serializers.ValidationError(str(exc)) from exc

        attrs['duration_minutes'] = duration
        service = self.context['service']
        accounts = self.context.get('provider_accounts')
        if accounts is None:
            account = self.context.get('provider_account')
            accounts = [account] if account else []
        elif self.context.get('provider_account') and self.context['provider_account'].pk != accounts[0].pk:
            raise serializers.ValidationError('指定陪玩参数不一致')
        if len(accounts) != service.fulfillment_provider_count:
            raise serializers.ValidationError(f'该服务须选择{service.fulfillment_provider_count}位陪玩')
        request = self.context['request']
        account = request.account
        requested_customer = attrs.get('customer_account_id')
        if account.account_type == ClubAccount.AccountType.STAFF:
            if not requested_customer:
                raise serializers.ValidationError('客服代约请先选择老板')
            customer_account = ClubAccount.objects.filter(
                pk=requested_customer, account_type=ClubAccount.AccountType.BOSS,
            ).first()
            if customer_account is None:
                raise serializers.ValidationError('所选老板不存在')
            from .reservation_services import _legacy_user_for_account
            customer = _legacy_user_for_account(customer_account)
        else:
            if requested_customer is not None and requested_customer != account.pk:
                raise serializers.ValidationError('只能为本人创建预约')
            customer_account, customer = account, request.legacy_user
        attrs['customer_account'] = customer_account
        attrs['customer'] = customer
        attrs['provider_account'] = accounts[0]
        attrs['provider_accounts'] = accounts
        attrs['service'] = self.context['service']
        return attrs


class ReservationListSerializer(EntityNumberListSerializer):
    def to_representation(self, data):
        rows = list(data.all() if hasattr(data, 'all') else data)
        ids = {row.provider_account_id for row in rows if row.provider_account_id}
        for row in rows:
            if row.required_provider_count == 2:
                ids.update(seat.provider_account_id for seat in row.seats.all() if seat.provider_account_id)
            if row.status == Reservation.Status.CONVERTED and row.order_id:
                ids.update(assignment.provider_account_id for assignment in row.order.providers.all()
                           if assignment.provider_account_id and not assignment.released_at)
                if row.order.provider_account_id:
                    ids.add(row.order.provider_account_id)
        from common.catalog_images import bounded_public_image_url
        self.child.context['_reservation_avatar_map'] = {
            pk: bounded_public_image_url(avatar or '') for pk, avatar in
            ClubAccount.objects.filter(pk__in=ids, account_type=ClubAccount.AccountType.PROVIDER)
            .values_list('pk', 'avatar_url')}
        return super().to_representation(rows)


class ReservationSerializer(EntityNumberModelSerializer):
    """预约详情/列表出参。"""

    provider = serializers.SerializerMethodField()
    seats = serializers.SerializerMethodField()
    current_providers = serializers.SerializerMethodField()
    customer = serializers.SerializerMethodField()
    service = serializers.SerializerMethodField()
    order_no = serializers.CharField(
        source='order.order_no', read_only=True, default='',
    )
    allowed_actions = serializers.SerializerMethodField()
    viewer_business_cycle_status = serializers.SerializerMethodField()
    viewer_participation_status = serializers.SerializerMethodField()

    class Meta:
        model = Reservation
        list_serializer_class = ReservationListSerializer
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
            'required_provider_count',
            'seats',
            'current_providers',
            'service',
            'order',
            'order_no',
            'allowed_actions',
            'viewer_business_cycle_status',
            'viewer_participation_status',
            'confirmed_at',
            'confirmation_deadline',
            'confirmation_timeout_minutes',
            'confirmation_config_version',
            'payment_deadline',
            'payment_timeout_minutes',
            'payment_config_version',
            'rejected_at',
            'cancelled_at',
            'converted_at',
            'expired_at',
            'created_at',
            'updated_at',
        ]

    def _provider_avatars(self, account_ids):
        from common.catalog_images import bounded_public_image_url
        cache = self.context.setdefault('_reservation_avatar_map', {})
        missing = {account_id for account_id in account_ids if account_id and account_id not in cache}
        if missing:
            cache.update({account_id: '' for account_id in missing})
            cache.update({pk: bounded_public_image_url(avatar or '') for pk, avatar in
                          ClubAccount.objects.filter(pk__in=missing, account_type=ClubAccount.AccountType.PROVIDER)
                          .values_list('pk', 'avatar_url')})
        return cache

    def get_provider(self, obj: Reservation):
        from stores.display import provider_display_name
        # For a team reservation the first seat is authoritative.  Its
        # invitation can be rejected or expire while the other confirmed
        # seat remains reserved; the booking's non-null primary account is
        # retained until a replacement is selected.
        if obj.required_provider_count == 2:
            seat = next((row for row in obj.seats.all() if row.number == 1), None)
            account_id = seat.provider_account_id if seat else None
            provider_id = seat.provider_id if seat else None
            name = seat.provider_name_snapshot if seat else ''
        else:
            account_id = obj.provider_account_id
            provider_id = obj.provider_id
            name = obj.provider_name_snapshot
        avatars = self._provider_avatars([account_id])
        return {
            'account_id': account_id,
            'legacy_user_id': provider_id,
            'name': name,
            'display_name': provider_display_name(name),
            'avatar_url': avatars.get(account_id, ''),
        }

    def get_seats(self, obj: Reservation):
        from stores.display import provider_display_name
        if obj.required_provider_count != 2:
            return []
        seats = list(obj.seats.all())
        avatars = self._provider_avatars(seat.provider_account_id for seat in seats)
        return [{'number': seat.number, 'status': seat.status, 'account_id': seat.provider_account_id,
                 'name': seat.provider_name_snapshot, 'display_name': provider_display_name(seat.provider_name_snapshot),
                 'avatar_url': avatars.get(seat.provider_account_id, ''),
                 'confirmation_deadline': seat.confirmation_deadline,
                 'confirmed_at': seat.confirmed_at} for seat in seats]

    def get_current_providers(self, obj: Reservation):
        from stores.display import provider_display_name
        if obj.status != Reservation.Status.CONVERTED or not obj.order_id:
            return []
        assignments = list(obj.order.providers.all())
        ids = [row.provider_account_id for row in assignments if row.provider_account_id and not row.released_at]
        if not assignments and obj.order.provider_account_id:
            ids.append(obj.order.provider_account_id)
        avatars = self._provider_avatars(ids)
        current = [{'account_id': row.provider_account_id, 'legacy_user_id': row.provider_id,
                    'name': row.provider_name_snapshot, 'display_name': provider_display_name(row.provider_name_snapshot),
                    'avatar_url': avatars.get(row.provider_account_id, '')}
                   for row in assignments if not row.released_at]
        if not assignments and obj.order.provider_account_id:
            current = [{'account_id': obj.order.provider_account_id, 'legacy_user_id': obj.order.provider_id,
                        'name': obj.order.provider_name_snapshot,
                        'display_name': provider_display_name(obj.order.provider_name_snapshot),
                        'avatar_url': avatars.get(obj.order.provider_account_id, '')}]
        return current

    def get_customer(self, obj: Reservation):
        customer = obj.customer_account if obj.customer_account_id else obj.customer
        return {
            'account_id': obj.customer_account_id,
            'legacy_user_id': obj.customer_id,
            'name': (customer.nickname or customer.username) if customer else '',
        }

    def get_service(self, obj: Reservation):
        return {
            'id': obj.service_id,
            'name': obj.service_name_snapshot,
            'price': obj.service.price if obj.service_id else 0,
        }

    def to_representation(self, obj):
        result = super().to_representation(obj)
        if result['viewer_participation_status'] == 'HISTORICAL':
            # A past invite does not grant access to later participants or
            # the customer's private game/order information.
            allowed = {'id', 'reservation_no', 'status', 'start_time', 'end_time',
                       'duration_minutes', 'game_rounds', 'service', 'created_at',
                       'updated_at', 'viewer_business_cycle_status',
                       'viewer_participation_status', 'allowed_actions'}
            return {key: value for key, value in result.items() if key in allowed}
        return result

    def get_viewer_participation_status(self, obj):
        account = getattr(self.context.get('request'), 'account', None)
        if account is None:
            return 'NOT_APPLICABLE'
        from .reservation_cycle import viewer_participation_status
        return viewer_participation_status(obj, account)

    def get_viewer_business_cycle_status(self, obj):
        account = getattr(self.context.get('request'), 'account', None)
        if account is None:
            return 'UNKNOWN'
        from .reservation_cycle import viewer_cycle_status
        return viewer_cycle_status(obj, account)

    def get_allowed_actions(self, obj: Reservation):
        """当前查看者可执行的动作，前端据此决定按钮显隐。

        只按「状态 + 身份」给建议，真正的权限判定仍在各视图里做 —— 这里
        少给了不会造成越权，多给了也只会在点下去时被拒。
        """
        request = self.context.get('request')
        account = getattr(request, 'account', None)
        if account is None or obj.is_terminal:
            return []
        if self.get_viewer_business_cycle_status(obj) not in ('CURRENT', 'NOT_APPLICABLE'):
            return []
        if self.get_viewer_participation_status(obj) == 'HISTORICAL':
            return []

        actions = []
        is_customer = account.account_type == ClubAccount.AccountType.BOSS and obj.customer_account_id == account.pk
        is_provider = account.account_type == ClubAccount.AccountType.PROVIDER and (
            obj.provider_account_id == account.pk if obj.required_provider_count == 1 else
            any(seat.provider_account_id == account.pk for seat in obj.seats.all()))
        from .reservation_permissions import staff_permissions
        if '_reservation_staff_permissions' not in self.context:
            self.context['_reservation_staff_permissions'] = staff_permissions(account)
        permissions = self.context['_reservation_staff_permissions']
        staff_dispatch = 'order:dispatch' in permissions
        staff_cancel = 'order:cancel' in permissions

        if obj.status == Reservation.Status.PENDING:
            if is_provider and (obj.required_provider_count == 1 or any(
                    seat.provider_account_id == account.pk and seat.status == 'PENDING' for seat in obj.seats.all())):
                actions.extend(['confirm', 'reject'])
            if is_customer or staff_cancel:
                actions.append('cancel')
            if is_customer and obj.required_provider_count == 2 and any(seat.status == 'VACANT' for seat in obj.seats.all()):
                actions.append('replace_provider')
        elif obj.status == Reservation.Status.CONFIRMED:
            if is_customer or is_provider or staff_cancel:
                actions.append('cancel')
            if is_customer or staff_dispatch:
                actions.append('convert')
        return actions


class ReservationCancelSerializer(serializers.Serializer):
    """取消 / 拒绝预约的入参：理由可空，但一旦填写就要落库存档。"""

    reason = serializers.CharField(
        required=False, allow_blank=True, max_length=255, default='',
    )


class ReservationReplacementOriginalProviderSerializer(serializers.Serializer):
    account_id = serializers.IntegerField(min_value=1)
    display_name = serializers.CharField(allow_blank=True)


class ReservationReplacementCandidateSerializer(serializers.Serializer):
    """Snake-case card projection compatible with the existing boss picker."""

    id = serializers.IntegerField(min_value=1)
    account_id = serializers.IntegerField(min_value=1)
    nickname = serializers.CharField(allow_blank=True)
    display_name = serializers.CharField(allow_blank=True)
    store_prefix = serializers.CharField(allow_blank=True)
    gender = serializers.CharField(allow_blank=True)
    avatar = serializers.CharField(allow_blank=True)
    avatar_url = serializers.CharField(allow_blank=True)
    rank = serializers.CharField(allow_blank=True)
    level_name = serializers.CharField(allow_blank=True)
    rating = serializers.FloatField()
    rating_count = serializers.IntegerField()
    favorable_rate = serializers.FloatField()
    order_count = serializers.IntegerField()
    win_rate = serializers.FloatField()
    price_per_hour = serializers.IntegerField()
    qualifications = serializers.ListField(child=serializers.DictField())
    has_reference_hourly_price = serializers.BooleanField()
    bio = serializers.CharField(allow_blank=True)
    city = serializers.CharField(allow_blank=True)
    status = serializers.CharField()
    can_order = serializers.BooleanField()
    is_verified = serializers.BooleanField()
    voice_card_url = serializers.CharField(allow_blank=True, allow_null=True)
    game_category_ids = serializers.ListField(child=serializers.IntegerField())
    game_category_names = serializers.ListField(child=serializers.CharField())
    service_item_ids = serializers.ListField(child=serializers.IntegerField())


class ReservationReplacementCandidatesSerializer(serializers.Serializer):
    seat_number = serializers.IntegerField(min_value=1, max_value=2)
    service_id = serializers.IntegerField(min_value=1)
    start_time = serializers.DateTimeField()
    end_time = serializers.DateTimeField()
    original_provider = ReservationReplacementOriginalProviderSerializer(allow_null=True)
    list = ReservationReplacementCandidateSerializer(many=True)
