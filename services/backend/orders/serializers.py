from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.conf import settings
from django.utils import timezone

from club_accounts.models import ClubAccount
from club_accounts.services import (
    get_or_create_account_for_legacy_user,
    link_legacy_relations,
)
from .models import Evaluation, Order, ServiceFavorite, ServiceItem


class ServiceItemSerializer(serializers.ModelSerializer):
    game_category_name = serializers.CharField(source='game_category.name', read_only=True, default='')
    service_category_name = serializers.CharField(source='service_category.name', read_only=True, default='')
    is_gift = serializers.BooleanField(source='service_category.is_gift', read_only=True, default=False)
    required_level_name = serializers.CharField(source='required_level.name', read_only=True, default='')

    class Meta:
        model = ServiceItem
        fields = ['id', 'name', 'description', 'price', 'game_category', 'game_category_name',
                  'service_category', 'service_category_name', 'is_gift',
                  'required_level', 'required_level_name', 'cover_url']


class ServiceItemDetailSerializer(serializers.ModelSerializer):
    sales_count = serializers.SerializerMethodField()
    is_favorited = serializers.SerializerMethodField()

    class Meta:
        model = ServiceItem
        fields = [
            'id',
            'name',
            'description',
            'price',
            'cover_url',
            'images',
            'highlights',
            'sales_count',
            'is_favorited',
        ]

    def get_sales_count(self, obj):
        # 已完成订单数
        return Order.objects.filter(
            service_id=obj.id,
            status=Order.Status.COMPLETED,
        ).count()

    def get_is_favorited(self, obj):
        request = self.context.get('request')
        if not request or not getattr(request, 'account', None):
            return False
        return ServiceFavorite.objects.filter(
            account=request.account,
            service_id=obj.id,
        ).exists()


class OrderSerializer(serializers.ModelSerializer):
    service = ServiceItemSerializer(read_only=True)
    customer = serializers.SerializerMethodField()
    provider = serializers.SerializerMethodField()
    is_evaluated = serializers.SerializerMethodField()
    can_operate = serializers.SerializerMethodField()
    can_reject = serializers.SerializerMethodField()
    allowed_actions = serializers.SerializerMethodField()
    expected_income = serializers.SerializerMethodField()
    evaluation = serializers.SerializerMethodField()
    support_contact = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'id',
            'order_no',
            'status',
            'payment_status',
            'amount',
            'original_amount',
            'boss_discount',
            'promo_discount',
            'coupon_discount',
            'expected_income',
            'commission_rate',
            'game_rounds',
            'game_region',
            'game_nickname',
            'game_uid',
            'remark',
            'support_contact',
            'source_order',
            'service',
            'customer',
            'provider',
            'is_evaluated',
            'evaluation',
            'can_operate',
            'can_reject',
            'allowed_actions',
            'grabbed_at',
            'in_service_at',
            'completed_at',
            'cancelled_at',
            'refunded_at',
            'cancel_reason',
            'auto_cancel_at',
            'reject_count',
            'created_at',
            'updated_at',
        ]

    def get_is_evaluated(self, obj):
        return hasattr(obj, 'evaluation')

    def get_evaluation(self, obj):
        """订单列表内返回评价摘要，让老板能直接看到陪玩回复。"""
        evaluation = getattr(obj, 'evaluation', None)
        if evaluation is None:
            return None
        return {
            'id': evaluation.id,
            'score': evaluation.score,
            'skill_score': evaluation.skill_score,
            'attitude_score': evaluation.attitude_score,
            'communication_score': evaluation.communication_score,
            'content': evaluation.content,
            'reply_content': evaluation.reply_content,
            'replied_at': evaluation.replied_at,
            'created_at': evaluation.created_at,
        }

    def get_can_operate(self, obj):
        request = self.context.get('request')
        return bool(
            request and getattr(request, 'account', None)
            and obj.provider_account_id == request.account.id
            and obj.status in (Order.Status.GRABBED, Order.Status.IN_SERVICE)
        )

    def get_can_reject(self, obj):
        return (
            self.get_can_operate(obj)
            and obj.status == Order.Status.GRABBED
            and obj.escort_mode == Order.EscortMode.SINGLE
        )

    def get_allowed_actions(self, obj):
        """后端给出当前登录人可执行动作，三端只负责按此渲染按钮。"""
        request = self.context.get('request')
        if not request or not getattr(request, 'account', None):
            return []
        account = request.account
        user = request.legacy_user
        actions = []

        if (
            account.account_type == ClubAccount.AccountType.BOSS
            and obj.customer_account_id == account.id
        ):
            if obj.status == Order.Status.PENDING and obj.payment_status == Order.PaymentStatus.PAID:
                actions.append('CANCEL')
            is_gift = bool(obj.service.service_category and obj.service.service_category.is_gift)
            if obj.status == Order.Status.COMPLETED and not is_gift and not hasattr(obj, 'evaluation'):
                actions.append('EVALUATE')
            if obj.status == Order.Status.COMPLETED and not is_gift and obj.provider_id:
                actions.append('TIP')
            return actions

        if account.account_type == ClubAccount.AccountType.PROVIDER:
            if obj.status == Order.Status.PENDING and obj.provider_account_id is None:
                profile = getattr(account, 'escort_profile', None)
                if (
                    profile
                    and profile.status == profile.Status.AVAILABLE
                    and (obj.auto_cancel_at is None or obj.auto_cancel_at > timezone.now())
                ):
                    from users.models import EscortSchedule
                    if EscortSchedule.provider_is_scheduled_now(user):
                        actions.append('GRAB')
            if obj.provider_account_id == account.id and obj.status == Order.Status.GRABBED:
                actions.append('START')
                if obj.escort_mode == Order.EscortMode.SINGLE:
                    actions.append('REJECT')
            if obj.provider_account_id == account.id and obj.status == Order.Status.IN_SERVICE:
                actions.append('COMPLETE')
            return actions

        if account.account_type == ClubAccount.AccountType.STAFF:
            if obj.status == Order.Status.PENDING:
                actions.extend(['ASSIGN', 'CANCEL'])
            elif obj.status == Order.Status.GRABBED:
                actions.extend(['START', 'REFUND'])
            elif obj.status == Order.Status.IN_SERVICE:
                actions.extend(['COMPLETE', 'REFUND'])
        return actions

    def get_expected_income(self, obj):
        """当前陪玩预计到手金额；双陪订单优先取本人结算明细。"""
        request = self.context.get('request')
        if request and getattr(request, 'account', None):
            assignment = obj.providers.filter(provider_account=request.account).first()
            if assignment:
                return assignment.provider_income
        return obj.provider_income

    def get_customer(self, obj):
        boss_type = obj.customer.boss_type
        boss_type_data = None
        if boss_type and boss_type.is_active:
            boss_type_data = {
                'name': boss_type.name,
                'color': boss_type.color,
                'discount_rate': boss_type.discount_rate,
            }
        return {
            'id': obj.customer_id,
            'nickname': obj.customer.nickname or obj.customer.username,
            'phone': obj.customer.phone or '',
            'boss_type': boss_type_data,
        }

    def get_provider(self, obj):
        if not obj.provider_id:
            return None
        return {
            'id': obj.provider_id,
            'nickname': obj.provider.nickname or obj.provider.username,
        }

    def get_support_contact(self, obj):
        if not obj.support_contact_id:
            return None
        return {
            'id': obj.support_contact_id,
            'name': obj.support_contact_name_snapshot or obj.support_contact.name,
            'avatar_url': obj.support_contact.avatar_url,
        }


class CreateOrderSerializer(serializers.Serializer):
    service_id = serializers.IntegerField(required=False, allow_null=True)
    product_id = serializers.IntegerField(required=False, allow_null=True)
    provider_id = serializers.IntegerField(required=False, allow_null=True)
    support_contact_id = serializers.IntegerField(required=False, allow_null=True)
    user_coupon_id = serializers.IntegerField(required=False, allow_null=True)
    game_rounds = serializers.IntegerField(required=False, min_value=1, default=1)
    game_region = serializers.CharField(required=False, allow_blank=True, max_length=50, default='')
    game_nickname = serializers.CharField(required=False, allow_blank=True, max_length=50, default='')
    game_uid = serializers.CharField(required=False, allow_blank=True, max_length=50, default='')
    remark = serializers.CharField(required=False, allow_blank=True, max_length=255, default='')

    def validate(self, attrs):
        request = self.context['request']
        account = request.account
        user = request.legacy_user
        if account.account_type not in (
            ClubAccount.AccountType.BOSS,
            ClubAccount.AccountType.STAFF,
        ):
            raise serializers.ValidationError('仅玩家或客服可下单')

        service_id = attrs.get('service_id') or attrs.get('product_id')
        if not service_id:
            raise serializers.ValidationError('请选择服务')

        try:
            service = ServiceItem.objects.get(id=service_id, is_active=True)
        except ServiceItem.DoesNotExist as exc:
            raise serializers.ValidationError('服务不存在或不可用') from exc

        attrs['service'] = service
        original_amount = service.price * attrs.get('game_rounds', 1)

        # 老板分级折扣：discount_rate=100 表示原价
        boss_type = account.boss_type
        if boss_type and boss_type.is_active:
            boss_rate = boss_type.discount_rate
        else:
            boss_rate = 100

        # 指定陪玩下单（可选）：需在解析抽成率前确定，等级抽成依赖陪玩
        provider = None
        provider_id = attrs.get('provider_id')
        if provider_id:
            try:
                provider_account = None
                if getattr(settings, 'LEGACY_FORCE_AUTH_COMPAT', False):
                    legacy_provider = get_user_model().objects.filter(
                        id=provider_id,
                        role='PROVIDER',
                    ).first()
                    if legacy_provider is not None:
                        provider_account = get_or_create_account_for_legacy_user(
                            legacy_provider,
                        )
                        link_legacy_relations(legacy_provider, provider_account)
                if provider_account is None:
                    provider_account = ClubAccount.objects.select_related(
                        'escort_profile__level',
                        'legacy_mapping',
                    ).filter(
                        id=provider_id,
                        account_type=ClubAccount.AccountType.PROVIDER,
                    ).first()
                if provider_account is None:
                    raise ClubAccount.DoesNotExist
                provider = get_user_model().objects.get(
                    id=provider_account.legacy_mapping.legacy_user_id,
                )
            except (ClubAccount.DoesNotExist, get_user_model().DoesNotExist) as exc:
                raise serializers.ValidationError('指定的大神不存在') from exc
            profile = getattr(provider_account, 'escort_profile', None)
            if profile is None or profile.status != profile.Status.AVAILABLE:
                raise serializers.ValidationError('指定的大神当前不可接单')
            from users.models import EscortSchedule
            if not EscortSchedule.provider_is_scheduled_now(provider):
                raise serializers.ValidationError('指定的大神当前不在接单档期')
            if not profile.can_take_service(service):
                raise serializers.ValidationError('该服务要求更高的陪玩档位，所选大神不符合')
        attrs['provider'] = provider

        support_contact = None
        support_contact_id = attrs.get('support_contact_id')
        if support_contact_id:
            from support.models import SupportContactCard
            try:
                support_contact = SupportContactCard.objects.get(id=support_contact_id, is_active=True)
            except SupportContactCard.DoesNotExist as exc:
                raise serializers.ValidationError('所选客服不存在或已停用') from exc
        attrs['support_contact'] = support_contact

        # 命中的促销活动（时间窗+范围+优先级取一条）
        from .pricing import (
            PricingError,
            compute_order_amount,
            resolve_active_promotion,
            resolve_commission_rate,
        )
        promotion = resolve_active_promotion(service)
        attrs['promotion'] = promotion

        # 优惠券（可选）：先校验有效性，门槛/与活动互斥交由计价模块裁定
        user_coupon = None
        coupon_amount = None
        coupon_threshold = 0
        user_coupon_id = attrs.get('user_coupon_id')
        if user_coupon_id:
            from django.utils import timezone

            from coupons.models import UserCoupon
            try:
                user_coupon = UserCoupon.objects.select_related('coupon').get(
                    id=user_coupon_id,
                    account=account,
                )
            except UserCoupon.DoesNotExist as exc:
                raise serializers.ValidationError('优惠券不存在') from exc

            if user_coupon.status != UserCoupon.Status.UNUSED:
                raise serializers.ValidationError('该优惠券已使用或已失效')
            if user_coupon.coupon.valid_to <= timezone.now():
                raise serializers.ValidationError('优惠券已过期')
            coupon_amount = user_coupon.coupon.amount
            coupon_threshold = user_coupon.coupon.threshold

        try:
            result = compute_order_amount(
                original_amount=original_amount,
                boss_rate=boss_rate,
                promotion=promotion,
                coupon_amount=coupon_amount,
                coupon_threshold=coupon_threshold,
            )
        except PricingError as exc:
            raise serializers.ValidationError(str(exc)) from exc

        attrs['original_amount'] = result.original_amount
        attrs['boss_discount'] = result.boss_discount
        attrs['promo_discount'] = result.promo_discount
        attrs['coupon_discount'] = result.coupon_discount
        attrs['amount'] = result.amount
        attrs['user_coupon'] = user_coupon

        # 拆账参数：抽成率 活动>陪玩等级>店铺；推荐人分佣按下单老板配置计提
        escort_profile = getattr(provider, 'escort_profile', None) if provider else None
        attrs['commission_rate'] = resolve_commission_rate(service, escort_profile, promotion)
        attrs['inviter'] = getattr(user, 'inviter', None)
        attrs['inviter_commission_rate'] = (
            user.inviter_commission_rate if attrs['inviter'] else 0
        )

        return attrs


class EvaluationSerializer(serializers.ModelSerializer):
    customer_name = serializers.SerializerMethodField()
    customer_avatar = serializers.SerializerMethodField()
    provider_name = serializers.SerializerMethodField()
    service_name = serializers.SerializerMethodField()
    order_no = serializers.CharField(source='order.order_no', read_only=True)
    avg_score = serializers.FloatField(read_only=True)

    class Meta:
        model = Evaluation
        fields = [
            'id', 'order', 'order_no', 'score',
            'skill_score', 'attitude_score', 'communication_score', 'avg_score',
            'content', 'is_anonymous',
            'customer_name', 'customer_avatar', 'provider_name', 'service_name',
            'reply_content', 'replied_at', 'created_at',
        ]

    def get_customer_name(self, obj):
        if obj.is_anonymous:
            return '匿名用户'
        return obj.customer.nickname or obj.customer.username

    def get_customer_avatar(self, obj):
        if obj.is_anonymous:
            return ''
        return obj.customer.avatar_url or ''

    def get_provider_name(self, obj):
        if not obj.provider_id:
            return ''
        return obj.provider.nickname or obj.provider.username

    def get_service_name(self, obj):
        return obj.order.service_name_snapshot


class CreateEvaluationSerializer(serializers.Serializer):
    order_id = serializers.IntegerField(required=True)
    score = serializers.IntegerField(min_value=1, max_value=5)
    skill_score = serializers.IntegerField(min_value=1, max_value=5, required=False)
    attitude_score = serializers.IntegerField(min_value=1, max_value=5, required=False)
    communication_score = serializers.IntegerField(min_value=1, max_value=5, required=False)
    content = serializers.CharField(required=False, allow_blank=True, max_length=500, default='')
    is_anonymous = serializers.BooleanField(default=False)

    def validate(self, attrs):
        # 三维分缺省时回落综合分，保证旧 C 端只传 score 时仍可用
        score = attrs['score']
        attrs.setdefault('skill_score', score)
        attrs.setdefault('attitude_score', score)
        attrs.setdefault('communication_score', score)
        return attrs

    def validate_order_id(self, value):
        request = self.context['request']
        try:
            order = Order.objects.get(id=value)
        except Order.DoesNotExist as exc:
            raise serializers.ValidationError('订单不存在') from exc

        if order.status != Order.Status.COMPLETED:
            raise serializers.ValidationError('仅已完成订单可评价')
        if order.customer_id != request.legacy_user.id:
            raise serializers.ValidationError('仅订单所属玩家可评价')
        if hasattr(order, 'evaluation'):
            raise serializers.ValidationError('该订单已评价')

        self.context['order'] = order
        return value
