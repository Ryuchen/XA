from users.qualifications import service_skill
from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from club_accounts.models import ClubAccount
from club_accounts.services import (
    get_or_create_account_for_legacy_user,
    link_legacy_relations,
)
from .models import Evaluation, Order, ServiceFavorite, ServiceItem
from .catalog import visible_service_q


class ServiceItemSerializer(serializers.ModelSerializer):
    # Public prices are the full package price; accounting retains per-person
    # unit prices and applies billing_provider_count exactly once.
    price = serializers.SerializerMethodField()
    unit_price = serializers.IntegerField(source="price", read_only=True)

    def get_price(self, obj):
        return obj.price * obj.billing_provider_count

    gameplay_name = serializers.CharField(source='gameplay.name', read_only=True, default='')
    gameplay_kind = serializers.CharField(source='gameplay.kind', read_only=True, default='')
    gameplay_theme = serializers.CharField(source='gameplay.theme', read_only=True, default='custom')
    gameplay_cover_url = serializers.CharField(source='gameplay.cover_url', read_only=True, default='')
    platform_name = serializers.CharField(source='get_platform_display', read_only=True)
    security_level_name = serializers.CharField(source='get_security_level_display', read_only=True)
    game_category_name = serializers.CharField(source='game_category.name', read_only=True, default='')
    service_category_name = serializers.CharField(source='service_category.name', read_only=True, default='')
    is_gift = serializers.BooleanField(source='service_category.is_gift', read_only=True, default=False)
    gift_commission_rate = serializers.SerializerMethodField()
    required_level_name = serializers.CharField(source='required_level.name', read_only=True, default='')
    provider_requirement = serializers.SerializerMethodField()
    def get_provider_requirement(self,obj):
        from users.qualifications import public_requirement
        return public_requirement(obj)

    class Meta:
        model = ServiceItem
        fields = ['id', 'name', 'description', 'price', 'unit_price', 'billing_provider_count', 'gift_naming_days', 'gift_festival', 'gift_starts_at', 'gift_ends_at', 'gameplay', 'gameplay_name', 'gameplay_kind', 'gameplay_theme', 'gameplay_cover_url', 'platform', 'platform_name', 'security_level', 'security_level_name', 'game_category', 'game_category_name',
                  'service_category', 'service_category_name', 'is_gift', 'gift_commission_rate',
                  'required_level', 'required_level_name', 'provider_kind', 'provider_grade', 'provider_requirement', 'cover_url',
                  'price_unit', 'purchase_mode', 'purchase_note', 'is_addon', 'catalog_family', 'catalog_variant', 'experience_kind', 'service_type', 'allows_discount_coupons']

    def to_representation(self, instance):
        from common.catalog_images import catalog_display_url
        data = super().to_representation(instance)
        for field in ('cover_url', 'gameplay_cover_url'):
            if field in data:
                data[field] = catalog_display_url(data[field])
        for field in ('images', 'rules_images', 'introduction_images'):
            if field in data:
                data[field] = list(dict.fromkeys(url for value in (data[field] or [])
                    if (url := catalog_display_url(value))))
        return data

    def get_gift_commission_rate(self, obj):
        from .gifts import GIFT_COMMISSION_RATE
        if not (obj.service_category and obj.service_category.is_gift):
            return None
        return GIFT_COMMISSION_RATE if obj.commission_rate is None else obj.commission_rate


class ServiceItemDetailSerializer(ServiceItemSerializer):
    details_html = serializers.SerializerMethodField()
    details_images = serializers.SerializerMethodField()

    def _content(self, obj):
        from common.rich_text import catalog_content
        cache = getattr(self, '_catalog_content_cache', None)
        if cache is None:
            cache = self._catalog_content_cache = {}
        if obj.pk not in cache:
            cache[obj.pk] = catalog_content(obj)
        return cache[obj.pk]

    def get_details_html(self, obj):
        return self._content(obj)['html']

    def get_details_images(self, obj):
        return self._content(obj)['images']

    variants = serializers.SerializerMethodField()
    roulette_pool = serializers.SerializerMethodField()
    rules = serializers.SerializerMethodField()

    def get_variants(self, obj):
        if not obj.catalog_family or (obj.service_category and obj.service_category.is_gift):
            return []
        from .purchase_benefits import account_service_q
        variants = ServiceItem.objects.filter(
            visible_service_q(), account_service_q(getattr(self.context.get('request'), 'account', None)),
            catalog_family=obj.catalog_family, gameplay_id=obj.gameplay_id,
            game_category_id=obj.game_category_id, is_active=True,
        ).exclude(service_category__is_gift=True).select_related('gameplay', 'game_category', 'service_category', 'required_level').order_by('sort_order', 'id')
        return ServiceItemSerializer(variants, many=True, context=self.context).data

    def get_rules(self, obj):
        return obj.rules or (obj.gameplay.rules if obj.gameplay_id else '')

    def get_roulette_pool(self, obj):
        from .gameplay import public_pool
        return public_pool(obj.roulette_pool) if obj.roulette_pool else []
    sales_count = serializers.SerializerMethodField()
    is_favorited = serializers.SerializerMethodField()

    class Meta:
        model = ServiceItem
        fields = ServiceItemSerializer.Meta.fields + [
            'rules', 'details_html', 'details_images', 'rules_images', 'introduction_images', 'roulette_pool', 'variants',
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


class OrderListSerializer(serializers.ListSerializer):
    def to_representation(self, data):
        from django.db.models import prefetch_related_objects
        rows = list(data.all() if hasattr(data, 'all') else data)
        prefetch_related_objects(rows, 'aftersales')
        scoped=[row for row in rows if row.provider_requirement_snapshot]
        if scoped:prefetch_related_objects(scoped,'applications__applicant__escort_profile__qualifications__game_category','applications__partner__escort_profile__qualifications__game_category')
        return super().to_representation(rows)


class OrderSerializer(serializers.ModelSerializer):
    settled_at = serializers.DateTimeField(read_only=True)
    can_aftersale = serializers.SerializerMethodField()
    refund_summary = serializers.SerializerMethodField()
    def get_can_aftersale(self,obj):
        from .refunds import can_request_aftersale
        return can_request_aftersale(obj)
    def get_refund_summary(self,obj):
        from .refunds import refund_summary, authorized_refund
        request = self.context.get('request')
        account = getattr(request,'account',None)
        return refund_summary(obj,detailed=authorized_refund(account),account=account)
    dispatch_notification_enabled = serializers.SerializerMethodField()
    def get_dispatch_notification_enabled(self,obj):
        request=self.context.get('request');account=getattr(request,'account',None)
        if not account or account.account_type!='PROVIDER':return False
        from users.qualifications import dispatch_notification_enabled
        if '_provider_notification_profile' not in self.context:
            from users.models import EscortProfile
            state=self.context.get('_provider_read_state')
            self.context['_provider_notification_profile']=state[0] if state else EscortProfile.objects.filter(account=account).prefetch_related('qualifications__game_category').first()
        return dispatch_notification_enabled(self.context['_provider_notification_profile'],obj)

    provider_requirement = serializers.SerializerMethodField()
    def get_provider_requirement(self,obj):
        from users.qualifications import public_requirement
        return public_requirement(obj)
    aftersale_deadline = serializers.SerializerMethodField()
    aftersale_status = serializers.SerializerMethodField()
    def get_aftersale_deadline(self, obj):
        from engagement.confirmation import aftersale_deadline
        return aftersale_deadline(obj)
    def get_aftersale_status(self, obj):
        return next((row.status for row in obj.aftersales.all()), '')

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
    fulfillment = serializers.SerializerMethodField()
    applications = serializers.SerializerMethodField()
    my_applications = serializers.SerializerMethodField()
    participants = serializers.SerializerMethodField()
    required_team_size = serializers.SerializerMethodField()
    can_team_apply = serializers.SerializerMethodField()
    quantity_unit = serializers.SerializerMethodField()

    def get_quantity_unit(self, obj):
        snapshot = obj.gameplay_snapshot
        unit = snapshot.get('price_unit') if isinstance(snapshot, dict) else None
        # Historical orders without a saved unit must not inherit catalog edits.
        return unit.strip() if isinstance(unit, str) and unit.strip() else '份'

    def get_required_team_size(self, obj):
        from .team_rules import required_team_size
        return required_team_size(obj)

    def get_can_team_apply(self, obj):
        return self.get_required_team_size(obj) == 2

    def get_applications(self, obj):
        from .applications import applications_data
        return applications_data(obj, self.context.get('request'))

    def get_my_applications(self, obj):
        from .applications import my_applications_data
        return my_applications_data(obj, self.context.get('request'))

    def get_participants(self, obj):
        return [{'id': row.provider_account_id, 'nickname': row.provider_name_snapshot} for row in (obj.active_participants if hasattr(obj, 'active_participants') else obj.providers.all()) if row.released_at is None]


    def get_fulfillment(self, obj):
        from .gameplay import fulfillment_text
        return fulfillment_text(obj)

    class Meta:
        model = Order
        list_serializer_class = OrderListSerializer
        fields = [
            'dispatch_notification_enabled','provider_requirement',
            'id',
            'requires_boss_selection', 'escort_mode', 'applications', 'my_applications', 'participants',
            'required_provider_count', 'required_team_size', 'can_team_apply',
            'order_no', 'gameplay_snapshot', 'roulette_result', 'fulfillment',
            'service_name_snapshot', 'unit_price_snapshot', 'provider_name_snapshot',
            'status',
            'payment_status',
            'amount',
            'original_amount',
            'boss_discount',
            'promo_discount',
            'coupon_discount',
            'expected_income',
            'commission_type', 'commission_fixed', 'commission_override', 'commission_source', 'commission_rate',
            'game_rounds',
            'quantity_unit',
            'game_region',
            'game_nickname',
            'game_uid',
            'provider_gender',
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
            'completed_at', 'settled_at', 'business_cycle', 'experience_kind', 'report_ready_at', 'consumption_ready_at', 'consumption_confirmed_at', 'aftersale_deadline', 'aftersale_status', 'can_aftersale', 'refund_summary',
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
                if obj.requires_boss_selection:
                    actions.append('SELECT_PROVIDER')
            is_gift = bool(obj.service.service_category and obj.service.service_category.is_gift)
            if obj.status == Order.Status.COMPLETED and not is_gift and not hasattr(obj, 'evaluation'):
                actions.append('EVALUATE')
            if obj.status == Order.Status.COMPLETED and obj.payment_status in (Order.PaymentStatus.PAID,Order.PaymentStatus.PARTIALLY_REFUNDED) and not is_gift and (obj.provider_id or obj.escort_mode == Order.EscortMode.DOUBLE):
                actions.append('TIP')
            return actions

        if account.account_type == ClubAccount.AccountType.PROVIDER:
            if obj.status == Order.Status.PENDING and obj.provider_account_id is None:
                # The list serializer shares this request-local context across
                # rows. Actual grab/dispatch still rechecks under its row lock.
                if '_provider_read_state' not in self.context:
                    from .services import count_active_orders
                    profile = getattr(account, 'escort_profile', None)
                    self.context['_provider_read_state'] = (
                        profile,
                        bool(profile and not profile.order_acceptance_error()),
                        count_active_orders(account),
                        profile.order_visibility_delay_seconds if profile else 0,
                    )
                profile, available, active_count, delay = self.context['_provider_read_state']
                if (
                    available
                    and profile.can_take_service(obj)
                    and service_skill(profile,obj)
                    and (obj.provider_gender == "ANY" or obj.provider_gender == profile.gender)
                    and obj.payment_status == Order.PaymentStatus.PAID
                    and obj.provider_id is None
                    and obj.escort_mode == Order.EscortMode.SINGLE
                    and not obj.source_order_id
                    and not (obj.service.service_category and obj.service.service_category.is_gift)
                    and (obj.auto_cancel_at is None or obj.auto_cancel_at > timezone.now())
                ):
                    from datetime import timedelta
                    if (active_count < Order.MAX_CONCURRENT_ORDERS
                            and timezone.now() >= obj.created_at + timedelta(seconds=delay)):
                        actions.append('GRAB')
            participant = obj.provider_account_id == account.id or (any(row.provider_account_id == account.pk for row in obj.active_participants) if hasattr(obj,'active_participants') else obj.providers.filter(provider_account=account, released_at__isnull=True).exists())
            if participant and obj.status == Order.Status.GRABBED:
                from .team_fulfillment import team_evidence_submitted
                if obj.escort_mode != 'DOUBLE' or not team_evidence_submitted(obj,account,'entry_image'):
                    actions.append('START')
                if obj.escort_mode == Order.EscortMode.SINGLE:
                    actions.append('REJECT')
            if participant and obj.status == Order.Status.IN_SERVICE:
                from .team_fulfillment import team_evidence_submitted
                if obj.escort_mode != 'DOUBLE' or not team_evidence_submitted(obj,account,'completion_image'):
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
            if hasattr(obj, 'requester_assignments'):
                assignment = next(iter(obj.requester_assignments), None)
            else:
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
    dispatch_mode = serializers.ChoiceField(choices=['BOSS_SELECT', 'AUTO_GRAB'], default='BOSS_SELECT')
    service_id = serializers.IntegerField(required=False, allow_null=True)
    product_id = serializers.IntegerField(required=False, allow_null=True)
    provider_id = serializers.IntegerField(required=False, allow_null=True)
    provider_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, max_length=2,
        allow_empty=True,
    )
    support_contact_id = serializers.IntegerField(required=False, allow_null=True)
    user_coupon_id = serializers.IntegerField(required=False, allow_null=True)
    game_rounds = serializers.IntegerField(required=False, min_value=1, default=1)
    expected_amount = serializers.IntegerField(required=False, min_value=0)
    game_region = serializers.CharField(required=False, allow_blank=True, max_length=50, default='')
    game_nickname = serializers.CharField(required=False, allow_blank=True, max_length=50, default='')
    game_uid = serializers.CharField(required=False, allow_blank=True, max_length=50, default='')
    provider_gender = serializers.ChoiceField(choices=Order.ProviderGender.choices, default=Order.ProviderGender.ANY)
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

        if service.service_category and service.service_category.is_gift:
            raise serializers.ValidationError('礼物请从赠送礼物入口购买')

        from .gameplay import validate_service_gameplay
        # A purchased double package can enter the existing two-person
        # application flow, or be assigned to a complete selected team.
        validate_service_gameplay(service, attrs.get('game_rounds', 1),
                                  companion_assignment=service.billing_provider_count == 2)
        attrs['service'] = service
        attrs['required_provider_count'] = service.billing_provider_count
        if attrs['provider_gender'] != 'ANY' and service.service_type != 'COMPANION':
            raise serializers.ValidationError('仅陪玩服务可选择男陪或女陪')
        original_amount = service.price * attrs.get('game_rounds', 1) * service.billing_provider_count

        # 老板分级折扣：discount_rate=100 表示原价
        boss_type = account.boss_type
        if boss_type and boss_type.is_active:
            boss_rate = boss_type.discount_rate
        else:
            boss_rate = 100
        from coupons.benefits import effective_boss_rate
        boss_rate = effective_boss_rate(account)

        if attrs['dispatch_mode'] == 'AUTO_GRAB' and service.billing_provider_count != 1:
            raise serializers.ValidationError('多人服务请使用老板选人方式下单')

        # 指定陪玩下单（可选）：需在解析抽成率前确定，等级抽成依赖陪玩
        provider_id = attrs.get('provider_id')
        selected_ids = attrs.get('provider_ids')
        if selected_ids is not None and provider_id:
            if selected_ids != [provider_id]:
                raise serializers.ValidationError('指定陪玩参数不一致，请重新选择')
        selected_ids = selected_ids if selected_ids is not None else ([provider_id] if provider_id else [])
        if len(set(selected_ids)) != len(selected_ids):
            raise serializers.ValidationError('不能重复选择同一位陪玩')
        if selected_ids and len(selected_ids) != service.billing_provider_count:
            raise serializers.ValidationError(f'该套餐须选择{service.billing_provider_count}位陪玩')
        providers, provider_accounts, provider_profiles = [], [], []
        for selected_id in selected_ids:
            try:
                provider_account = None
                # The array contract always addresses business accounts. The
                # historical scalar retains legacy-ID fixture compatibility.
                if 'provider_ids' not in attrs and getattr(settings, 'LEGACY_FORCE_AUTH_COMPAT', False):
                    legacy_provider = get_user_model().objects.filter(
                        id=selected_id,
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
                        id=selected_id,
                        account_type=ClubAccount.AccountType.PROVIDER,
                    ).first()
                if provider_account is None:
                    raise ClubAccount.DoesNotExist
                provider = get_user_model().objects.get(
                    id=provider_account.legacy_mapping.legacy_user_id,
                )
            except ObjectDoesNotExist as exc:
                raise serializers.ValidationError('指定的大神不存在') from exc
            profile = getattr(provider_account, 'escort_profile', None)
            if profile is None or profile.status != profile.Status.AVAILABLE:
                raise serializers.ValidationError('指定的大神当前不可接单')
            reason = profile.order_acceptance_error(service, provider_gender=attrs["provider_gender"])
            if reason:
                raise serializers.ValidationError(reason)
            from .services import count_active_orders
            if count_active_orders(provider_account) >= Order.MAX_CONCURRENT_ORDERS:
                raise serializers.ValidationError('该大神接单名额已满，请重新选择')
            providers.append(provider)
            provider_accounts.append(provider_account)
            provider_profiles.append(profile)
        if len({account.pk for account in provider_accounts}) != len(provider_accounts):
            raise serializers.ValidationError('不能重复选择同一位陪玩')
        attrs['providers'] = providers
        attrs['provider_accounts'] = provider_accounts
        attrs['provider_profiles'] = provider_profiles
        attrs['provider'] = providers[0] if providers else None

        support_contact = None
        support_contact_id = attrs.get('support_contact_id')
        if support_contact_id:
            from support.models import SupportContactCard
            try:
                support_contact = SupportContactCard.objects.get(id=support_contact_id, is_active=True)
            except SupportContactCard.DoesNotExist as exc:
                raise serializers.ValidationError('所选客服不存在或已停用') from exc
        attrs['support_contact'] = support_contact

        from .purchase_benefits import prepare_purchase
        from .pricing import resolve_commission_rate
        result, promotion, user_coupon = prepare_purchase(account, service,
            attrs.get('game_rounds', 1), attrs.get('user_coupon_id'))
        attrs['promotion'] = promotion
        attrs['original_amount'] = result.original_amount
        attrs['boss_discount'] = result.boss_discount
        attrs['promo_discount'] = result.promo_discount
        attrs['coupon_discount'] = result.coupon_discount
        attrs['amount'] = result.amount
        attrs['user_coupon'] = user_coupon
        attrs['pricing'] = result

        # 拆账参数：抽成率 活动>陪玩等级>店铺；推荐人分佣按下单老板配置计提
        escort_profile = provider_profiles[0] if provider_profiles else None
        from .commission import resolve, snapshot
        plan = resolve(service, result.amount, profile=escort_profile, promotion=promotion)
        attrs['commission_snapshot'] = snapshot(plan)
        attrs['commission_rate'] = plan['commission_rate']
        from .pricing import club_provider_specs
        specs = club_provider_specs(service, [escort_profile], result.amount)
        attrs['commission_spec'] = ({'commission_type':'FIXED','commission_fixed':plan['commission_fixed'],'commission_rate':0}
            if plan['commission_type'] == 'FIXED' else specs[0] if specs else {'commission_rate': attrs['commission_rate']})
        if len(provider_profiles) == 2:
            from .commission import fixed_specs
            if plan['commission_type'] == 'FIXED':
                selected_specs = fixed_specs(result.amount, 2, plan['commission_fixed'])
            else:
                selected_specs = club_provider_specs(service, provider_profiles, result.amount)
                if selected_specs is None:
                    selected_specs = [
                        {'commission_rate': resolve(service, result.amount, profile=profile,
                                                    promotion=promotion)['commission_rate']}
                        for profile in provider_profiles
                    ]
            attrs['provider_specs'] = selected_specs
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
