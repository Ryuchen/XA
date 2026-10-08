import re
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.core.validators import URLValidator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from announcements.models import Announcement
from audition.models import AuditionLink, AuditionSignup
from banners.models import (
    BANNER_IMAGE_HEIGHT,
    BANNER_IMAGE_WIDTH,
    Banner,
)
from chat.models import ChatMessage, ChatSession
from coupons.models import Coupon, UserCoupon
from orders.models import (
    Evaluation,
    GameCategory,
    Order,
    OrderProvider,
    OrderStatusLog,
    ServiceCategory,
    ServiceItem,
)
from orders.catalog import skill_service_q


from promotions.models import Promotion
from site_messages.models import Message
from support.models import SupportContactCard
from club_accounts.models import ClubAccount
from users.models import (
    Achievement, BossType, CheckinGift, CheckinMonthProgress, CheckinRuleConfig,
    EscortLevel, EscortProfile,
)
from wallet.models import (
    DisposeRecord,
    ProviderReport,
    RechargeRecord,
    Transaction,
    Wallet,
    WithdrawRequest,
    get_commission_rate,
)
from common.media import build_media_url

from .models import AccountBan, AdminAuditLog, AdminMembership, AdminRole

User = get_user_model()


def normalize_escort_skills(attrs, instance=None):
    """Validate new links, retain retired links, and include each skill's game."""
    if not {'service_items', 'game_categories'}.intersection(attrs):
        return attrs
    items = attrs.get('service_items', list(instance.service_items.all()) if instance else [])
    previous = set(instance.service_items.values_list('id', flat=True)) if instance else set()
    new_ids = {item.pk for item in items} - previous
    if ServiceItem.objects.filter(skill_service_q(), pk__in=new_ids).count() != len(new_ids):
        raise serializers.ValidationError({'service_items': '请选择已启用游戏下的服务，礼物与附加费用不能作为技能'})
    games = attrs.get('game_categories', list(instance.game_categories.all()) if instance else [])
    old_games = set(instance.game_categories.values_list('id', flat=True)) if instance else set()
    if any(not game.is_active and game.pk not in old_games for game in games):
        raise serializers.ValidationError({'game_categories': '不能新增已停用的游戏类目'})
    game_ids = {game.pk for game in games} | {item.game_category_id for item in items if item.game_category_id}
    attrs['game_categories'] = list(GameCategory.objects.filter(pk__in=game_ids))
    return attrs

# ---------------- 用户 ----------------
class AdminUserSerializer(serializers.ModelSerializer):
    role = serializers.CharField(source='account_type', read_only=True)
    role_display = serializers.CharField(source='get_account_type_display', read_only=True)
    boss_no = serializers.CharField(source='account_no', required=False)
    date_joined = serializers.DateTimeField(source='created_at', read_only=True)
    inviter_name = serializers.SerializerMethodField()
    boss_type_name = serializers.CharField(source='boss_type.name', read_only=True)
    wallet_balance = serializers.SerializerMethodField()

    class Meta:
        model = ClubAccount
        fields = [
            'id', 'username', 'nickname', 'real_name', 'role', 'role_display',
            'phone', 'avatar_url', 'openid', 'is_phone_verified', 'is_openid_bound',
            'is_active', 'inviter', 'inviter_name', 'inviter_commission_rate',
            'boss_type', 'boss_type_name', 'boss_no', 'can_login', 'can_view',
            'wallet_balance',
            'last_active_at', 'date_joined', 'created_at',
        ]
        read_only_fields = ['username', 'openid', 'date_joined', 'created_at']

    def get_inviter_name(self, obj):
        if not obj.inviter_id:
            return ''
        return obj.inviter.nickname or obj.inviter.username

    def get_wallet_balance(self, obj):
        wallet = getattr(obj, 'wallet', None)
        return wallet.balance if wallet else 0

    # 封禁流程真正翻转的开关，与 console.ban_utils.BAN_LOCKED_FIELDS 同源。
    # 两处必须一致：一边锁 A 一边拦 B，报错文案就会开始说谎。
    BAN_MANAGED_FIELDS = ('is_active', 'can_login')

    # 拦截但**不由封禁流程管理**的开关。can_view 目前全仓没有鉴权消费点（死开关），
    # 封禁刻意不动它；但也不能从「编辑用户」放开——一旦将来它被接成访问闸门，
    # 裸 PATCH 就是一条现成的无审计后门。拦住成本为零，放开的代价要等出事才知道。
    LOCKED_SWITCH_REASONS = {
        'is_active': '该开关由封禁流程管理，请使用封禁/解封接口',
        'can_login': '该开关由封禁流程管理，请使用封禁/解封接口',
        'can_view': '该开关不开放在用户编辑中修改，如需调整请提工单',
    }

    def validate(self, attrs):
        """拦截绕过封禁流程的裸 PATCH。

        这几个开关一旦能被普通「编辑用户」接口改写，封禁就有了一条没有原因、
        没有操作人、没有到期时间、不落 AccountBan 的后门通道——审计表会显示
        「该账户从未被封禁」，而人确实进不来。

        只在**值真的发生变化**时拒绝：后台表单是整体提交的，每次保存都会带上
        这些字段的当前值，若一律拒绝会导致改个昵称都保存失败。
        """
        instance = self.instance
        if instance is None:
            return attrs

        if 'boss_type' in attrs and attrs['boss_type'] != instance.boss_type:
            from engagement.models import VipTier
            if VipTier.objects.filter(boss_type_id__in=[instance.boss_type_id, getattr(attrs['boss_type'], 'pk', None)]).exists():
                raise serializers.ValidationError({'boss_type':'VIP由确认实销自动升级；重置请使用退店清空'})
        attempted = [
            field for field in self.LOCKED_SWITCH_REASONS
            if field in attrs and bool(attrs[field]) != bool(getattr(instance, field))
        ]
        if attempted:
            raise serializers.ValidationError({
                field: self.LOCKED_SWITCH_REASONS[field] for field in attempted
            })
        return attrs

    @transaction.atomic
    def update(self, instance, validated_data):
        instance = super().update(instance, validated_data)
        mapping = getattr(instance, 'legacy_mapping', None)
        if mapping is not None:
            legacy_user = User.objects.filter(pk=mapping.legacy_user_id).first()
            if legacy_user is not None:
                legacy_user.nickname = instance.nickname
                legacy_user.real_name = instance.real_name
                legacy_user.phone = instance.phone
                legacy_user.inviter_commission_rate = instance.inviter_commission_rate
                legacy_user.boss_type_id = instance.boss_type_id
                legacy_user.boss_no = instance.account_no
                legacy_user.can_login = instance.can_login
                legacy_user.can_view = instance.can_view
                if instance.inviter_id:
                    inviter_mapping = getattr(instance.inviter, 'legacy_mapping', None)
                    legacy_user.inviter_id = (
                        inviter_mapping.legacy_user_id if inviter_mapping else None
                    )
                else:
                    legacy_user.inviter_id = None
                legacy_user.save(update_fields=[
                    'nickname', 'real_name', 'phone',
                    'inviter', 'inviter_commission_rate', 'boss_type',
                    'boss_no', 'can_login', 'can_view',
                ])
        return instance


# ---------------- 老板分级 ----------------
class AdminBossTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = BossType
        fields = ['id', 'name', 'discount_rate', 'color', 'remark', 'sort_order',
                  'is_active', 'created_at']
        read_only_fields = ['created_at']


# ---------------- 陪玩等级 ----------------
class AdminEscortLevelSerializer(serializers.ModelSerializer):
    class Meta:
        model = EscortLevel
        fields = ['id', 'name', 'commission_rate', 'remark', 'sort_order',
                  'is_active', 'created_at']
        read_only_fields = ['created_at']


# ---------------- 陪玩 ----------------
class EscortRelationsInputMixin:
    def to_internal_value(self, data):
        if hasattr(data, 'getlist'):
            data = data.copy()
            for field in ('game_categories', 'service_items'):
                if data.getlist(field) == ['[]']:
                    data.setlist(field, [])
        return super().to_internal_value(data)


from users.qualification_serializers import QualificationsField, save_qualifications

class AdminEscortSerializer(EscortRelationsInputMixin, serializers.ModelSerializer):
    qualifications = QualificationsField(required=False)
    user = serializers.PrimaryKeyRelatedField(source='account', read_only=True)
    username = serializers.CharField(source='account.username', read_only=True)
    phone = serializers.CharField(source='account.phone', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    gender_display = serializers.CharField(source='get_gender_display', read_only=True)
    level_name = serializers.CharField(source='level.name', read_only=True)
    avatar = serializers.ImageField(write_only=True, required=False, allow_null=True)
    intro_video = serializers.FileField(write_only=True, required=False, allow_null=True)
    cheat_proof = serializers.ImageField(write_only=True, required=False, allow_null=True)
    avatar_url = serializers.SerializerMethodField()
    intro_video_url = serializers.SerializerMethodField()
    cheat_proof_url = serializers.SerializerMethodField()
    active_pass_tier = serializers.CharField(read_only=True)
    pass_tier_display = serializers.SerializerMethodField()
    game_category_names = serializers.SerializerMethodField()
    service_item_names = serializers.SerializerMethodField()

    class Meta:
        model = EscortProfile
        fields = [
            'id', 'user', 'username', 'phone',
            'avatar', 'intro_video', 'cheat_proof',
            'avatar_url', 'intro_video_url', 'cheat_proof_url',
            'display_name', 'bio',
            'gender', 'gender_display', 'recommendation_slot',
            'city', 'service_area', 'price_per_hour', 'rank_tier', 'win_rate',
            'level', 'level_name', 'qualifications',
            'game_categories', 'game_category_names',
            'service_items', 'service_item_names',
            'status', 'status_display', 'is_verified', 'escort_no',
            'deposit_required', 'deposit_paid', 'deposit_mode', 'effective_deposit_required', 'total_reward', 'total_penalty',
            'rating_avg', 'rating_count',
            'completed_order_count', 'created_at',
            'active_pass_tier', 'pass_tier_display', 'pass_expires_at',
        ]
        read_only_fields = ['user', 'total_reward', 'total_penalty',
                            'rating_avg', 'rating_count', 'completed_order_count', 'created_at']

    # 只能由资金流程写入的账务字段 -> 拒绝理由。
    #
    # 为什么不直接塞进 read_only_fields：前端「编辑陪玩」表单是整体提交的
    # （apps/admin-web/src/views/escort/index.vue 每次保存都无条件回传
    # deposit_paid 的当前值）。设成 read_only 后 DRF 会**静默丢弃**这个键，
    # 客服改完押金按保存、页面提示成功、值却没变——现场只会归因成「系统抽风」，
    # 排查成本远高于直接报错。因此这里走 validate() 显式拒绝，让失败可见。
    FUND_MANAGED_FIELDS = {
        'deposit_paid': '已缴押金不可直接编辑，请走缴纳/退还流程',
    }

    def validate(self, attrs):
        """拦截绕过押金流程直改 ``deposit_paid`` 的后台 PATCH/PUT。

        ``deposit_paid`` 是资金字段：唯一合法写入点是 ``wallet.views.DepositView``
        （扣陪玩钱包 + 累加已缴 + 落 DEPOSIT 流水，三件事在同一事务里）。
        从「编辑陪玩」表单直接改它，会造成「已缴押金变了，但没有任何一笔流水
        对得上」——对账时这笔差额无法归因，也无法追责到操作人。

        与 :class:`AdminUserSerializer` 同款：**只在值真的发生变化时拒绝**。
        表单整体提交会带上当前值，一律拒绝会导致改个昵称都保存失败。

        ``deposit_required``（应缴）保持可改：它是运营策略参数不是资金余额，
        调高调低都不动钱，改完由 DepositView 按新差额收款。
        """
        attrs = normalize_escort_skills(attrs, self.instance)
        instance = self.instance
        if instance is None:
            # 创建走 AdminCreateEscortSerializer（EscortViewSet.create 已覆写），
            # 那个序列化器压根没有 deposit_paid 字段，到不了这里。
            return attrs

        attempted = [
            field for field in self.FUND_MANAGED_FIELDS
            if field in attrs and attrs[field] != getattr(instance, field)
        ]
        if attempted:
            raise serializers.ValidationError({
                field: self.FUND_MANAGED_FIELDS[field] for field in attempted
            })
        from wallet.club_rules import club_income_rules_enabled, deposit_target
        mode = attrs.get('deposit_mode', instance.deposit_mode)
        if mode != instance.deposit_mode and instance.deposit_paid:
            raise serializers.ValidationError({'deposit_mode': '已有押金时请先走退还流程，再切换缴纳模式'})
        if club_income_rules_enabled():
            attrs['deposit_required'] = deposit_target(mode)
        return attrs

    def _abs_url(self, field):
        return build_media_url(self.context.get('request'), field)

    def get_game_category_names(self, obj):
        return [c.name for c in obj.game_categories.all()]

    def get_service_item_names(self, obj):
        return [s.name for s in obj.service_items.all()]

    def get_avatar_url(self, obj):
        return self._abs_url(obj.avatar)

    def get_intro_video_url(self, obj):
        return self._abs_url(obj.intro_video)

    def get_cheat_proof_url(self, obj):
        return self._abs_url(obj.cheat_proof)

    def get_pass_tier_display(self, obj):
        return dict(EscortProfile.PassTier.choices).get(obj.active_pass_tier, '无通行证')

    @transaction.atomic
    def update(self, instance, validated_data):
        instance = EscortProfile.objects.select_for_update().get(pk=instance.pk)
        if validated_data.get('deposit_mode', instance.deposit_mode) != instance.deposit_mode and instance.deposit_paid:
            raise serializers.ValidationError({'deposit_mode': '已有押金时请先走退还流程，再切换缴纳模式'})
        avatar = validated_data.get('avatar')
        qualifications = validated_data.pop('qualifications', None)
        instance = super().update(instance, validated_data)
        if qualifications is not None: save_qualifications(instance,qualifications)
        # 迁移期同步新旧账户头像，避免未切换的订单展示链路丢失头像。
        if avatar is not None and instance.avatar:
            avatar_url = build_media_url(self.context.get('request'), instance.avatar)
            if instance.account_id:
                instance.account.avatar_url = avatar_url
                instance.account.save(update_fields=['avatar_url'])
            instance.user.avatar_url = avatar_url
            instance.user.save(update_fields=['avatar_url'])
        return instance


class AdminCreateEscortSerializer(EscortRelationsInputMixin, serializers.Serializer):
    qualifications = QualificationsField(required=False)
    """客服后台开户：创建陪玩登录账号 + 陪玩档案。

    账号密码由此写入 CustomUser(role=PROVIDER)，陪玩用它在陪玩端登录；
    档案字段落 EscortProfile。钱包由 post_save 信号自动创建。
    """

    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=128)
    display_name = serializers.CharField(max_length=50)
    nickname = serializers.CharField(max_length=50, required=False, allow_blank=True, default='')
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default='')
    gender = serializers.ChoiceField(
        choices=EscortProfile.Gender.choices,
        required=False,
        default=EscortProfile.Gender.UNKNOWN,
    )
    city = serializers.CharField(max_length=50, required=False, allow_blank=True, default='')
    escort_no = serializers.CharField(max_length=32, required=False, allow_blank=True, default='')
    level = serializers.PrimaryKeyRelatedField(
        queryset=EscortLevel.objects.all(), required=False, allow_null=True,
    )
    game_categories = serializers.PrimaryKeyRelatedField(
        queryset=GameCategory.objects.all(), many=True, required=False,
    )
    service_items = serializers.PrimaryKeyRelatedField(
        queryset=ServiceItem.objects.all(), many=True, required=False,
    )
    deposit_required = serializers.IntegerField(min_value=0, required=False, default=0)
    deposit_mode = serializers.ChoiceField(choices=['CASH', 'EARNINGS'], required=False, default='CASH')
    avatar = serializers.ImageField(required=False, allow_null=True)
    intro_video = serializers.FileField(required=False, allow_null=True)
    cheat_proof = serializers.ImageField(required=False, allow_null=True)

    def validate(self, attrs):
        from wallet.club_rules import club_income_rules_enabled, deposit_target
        if club_income_rules_enabled():
            attrs['deposit_required'] = deposit_target(attrs.get('deposit_mode', 'CASH'))
        return normalize_escort_skills(attrs)

    def validate_username(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('请输入登录账号')
        if (
            ClubAccount.objects.filter(username=value).exists()
            or User.objects.filter(username=value).exists()
        ):
            raise serializers.ValidationError('登录账号已存在')
        return value


# ---------------- 订单 ----------------
class AdminOrderProviderSerializer(serializers.ModelSerializer):
    provider = serializers.PrimaryKeyRelatedField(
        source='provider_account',
        read_only=True,
    )
    provider_name = serializers.SerializerMethodField()
    commission_type_display = serializers.CharField(
        source='get_commission_type_display', read_only=True
    )

    class Meta:
        model = OrderProvider
        fields = [
            'id', 'provider', 'provider_name', 'provider_name_snapshot',
            'commission_type', 'commission_type_display',
            'commission_rate', 'commission_fixed',
            'settlement_base', 'provider_income', 'settled_at', 'released_at', 'created_at',
        ]

    def get_provider_name(self, obj):
        if obj.provider_account_id:
            return obj.provider_account.nickname or obj.provider_account.username
        return obj.provider_name_snapshot or ''


class AdminOrderSerializer(serializers.ModelSerializer):
    provider_requirement=serializers.SerializerMethodField()
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

    customer = serializers.PrimaryKeyRelatedField(
        source='customer_account',
        read_only=True,
    )
    provider = serializers.PrimaryKeyRelatedField(
        source='provider_account',
        read_only=True,
    )
    inviter = serializers.PrimaryKeyRelatedField(
        source='inviter_account',
        read_only=True,
    )
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    payment_status_display = serializers.CharField(source='get_payment_status_display', read_only=True)
    escort_mode_display = serializers.CharField(source='get_escort_mode_display', read_only=True)
    customer_name = serializers.SerializerMethodField()
    customer_username = serializers.CharField(source='customer_account.username', read_only=True)
    customer_nickname = serializers.CharField(source='customer_account.nickname', read_only=True)
    customer_phone = serializers.CharField(source='customer_account.phone', read_only=True)
    provider_name = serializers.SerializerMethodField()
    inviter_name = serializers.SerializerMethodField()
    promotion_title = serializers.CharField(source='promotion.title', read_only=True)
    providers = AdminOrderProviderSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            'provider_requirement',
            'id', 'order_no', 'status', 'status_display', 'payment_status', 'settled_at', 'settlement_mode',
            'payment_status_display', 'escort_mode', 'escort_mode_display', 'provider_gender',
            'amount', 'game_rounds', 'service_name_snapshot', 'gameplay_snapshot', 'roulette_result',
            'support_contact', 'support_contact_name_snapshot',
            'unit_price_snapshot', 'customer', 'customer_name', 'customer_username',
            'customer_nickname', 'customer_phone', 'customer_phone_snapshot',
            'provider', 'provider_name', 'commission_rate', 'provider_income',
            'providers',
            'inviter', 'inviter_name', 'inviter_commission', 'shop_income',
            'original_amount', 'boss_discount', 'promo_discount', 'coupon_discount',
            'promotion', 'promotion_title',
            'remark', 'cancel_reason', 'reject_count',
            'grabbed_at', 'in_service_at', 'completed_at', 'cancelled_at', 'refunded_at',
            'business_cycle', 'experience_kind', 'report_ready_at', 'consumption_ready_at', 'consumption_confirmed_at', 'aftersale_deadline', 'aftersale_status',
            'created_at', 'updated_at',
        ]

    def get_customer_name(self, obj):
        if not obj.customer_account_id:
            return ''
        return obj.customer_account.nickname or obj.customer_account.username

    def get_provider_name(self, obj):
        if not obj.provider_account_id:
            return obj.provider_name_snapshot or ''
        return obj.provider_account.nickname or obj.provider_account.username

    def get_inviter_name(self, obj):
        if not obj.inviter_account_id:
            return ''
        return obj.inviter_account.nickname or obj.inviter_account.username


class AdminOrderLogSerializer(serializers.ModelSerializer):
    operator = serializers.PrimaryKeyRelatedField(
        source='operator_account',
        read_only=True,
    )
    action_display = serializers.CharField(source='get_action_display', read_only=True)
    operator_name = serializers.SerializerMethodField()

    class Meta:
        model = OrderStatusLog
        fields = [
            'id', 'action', 'action_display', 'from_status', 'to_status',
            'operator', 'operator_name', 'reason', 'created_at',
        ]

    def get_operator_name(self, obj):
        if not obj.operator_account_id:
            return '系统'
        return obj.operator_account.nickname or obj.operator_account.username


class AdminEvaluationSerializer(serializers.ModelSerializer):
    customer_name = serializers.SerializerMethodField()
    customer_username = serializers.CharField(source='customer.username', read_only=True)
    customer_nickname = serializers.CharField(source='customer.nickname', read_only=True)
    provider_name = serializers.SerializerMethodField()
    provider_username = serializers.CharField(source='provider.username', read_only=True)
    provider_nickname = serializers.CharField(source='provider.nickname', read_only=True)
    service_name = serializers.SerializerMethodField()
    order_no = serializers.CharField(source='order.order_no', read_only=True)
    avg_score = serializers.FloatField(read_only=True)

    class Meta:
        model = Evaluation
        fields = ['id', 'order', 'order_no', 'customer', 'customer_name', 'customer_username',
                  'customer_nickname', 'provider', 'provider_name', 'provider_username',
                  'provider_nickname', 'service_name', 'score',
                  'skill_score', 'attitude_score', 'communication_score', 'avg_score',
                  'content', 'is_anonymous',
                  'reply_content', 'replied_at', 'created_at']

    def get_customer_name(self, obj):
        anon = '（匿名）' if obj.is_anonymous else ''
        return f"{obj.customer.nickname or obj.customer.username}{anon}"

    def get_provider_name(self, obj):
        if not obj.provider_id:
            return ''
        return obj.provider.nickname or obj.provider.username

    def get_service_name(self, obj):
        return obj.order.service_name_snapshot


# ---------------- 钱包/流水 ----------------
class AdminWalletSerializer(serializers.ModelSerializer):
    user_role = serializers.CharField(source='user.role', read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)
    nickname = serializers.CharField(source='user.nickname', read_only=True)
    phone = serializers.CharField(source='user.phone', read_only=True)
    boss_no = serializers.SerializerMethodField()
    account_id = serializers.IntegerField(read_only=True)
    avatar_url = serializers.CharField(source='user.avatar_url', read_only=True)
    boss_type_name = serializers.CharField(source='user.boss_type.name', read_only=True)

    class Meta:
        model = Wallet
        fields = ['id', 'user', 'user_role', 'account_id', 'username', 'nickname', 'phone', 'boss_no', 'avatar_url',
                  'boss_type_name', 'balance',
                  'frozen_amount', 'total_recharge', 'total_gift', 'is_active', 'updated_at']
        read_only_fields = ['user', 'balance', 'frozen_amount', 'total_recharge', 'total_gift']

    def get_boss_no(self, obj):
        return obj.account.account_no if obj.account_id else obj.user.boss_no


class AdminTransactionSerializer(serializers.ModelSerializer):
    tx_type_display = serializers.CharField(source='get_tx_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    username = serializers.SerializerMethodField()
    operator_name = serializers.SerializerMethodField()

    class Meta:
        model = Transaction
        fields = ['id', 'tx_no', 'wallet', 'username', 'order', 'amount', 'tx_type',
                  'tx_type_display', 'balance_before', 'balance_after', 'status',
                  'status_display', 'remark', 'operator', 'operator_name', 'created_at']

    def get_username(self, obj):
        return obj.wallet.user.username if obj.wallet_id else ''

    def get_operator_name(self, obj):
        if not obj.operator_id:
            return ''
        return obj.operator.nickname or obj.operator.username


class AdminRechargeRecordSerializer(serializers.ModelSerializer):
    user_role = serializers.CharField(source='user.role', read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)
    nickname = serializers.CharField(source='user.nickname', read_only=True)
    phone = serializers.CharField(source='user.phone', read_only=True)
    boss_no = serializers.SerializerMethodField()
    avatar_url = serializers.CharField(source='user.avatar_url', read_only=True)
    boss_type_name = serializers.CharField(source='user.boss_type.name', read_only=True)
    operator_name = serializers.SerializerMethodField()
    proof_image_url = serializers.SerializerMethodField()
    total_amount = serializers.IntegerField(read_only=True)

    class Meta:
        model = RechargeRecord
        fields = ['id', 'user', 'user_role', 'username', 'nickname', 'phone', 'boss_no', 'avatar_url',
                  'boss_type_name', 'amount', 'gift_amount',
                  'total_amount', 'trade_no', 'proof_image_url', 'remark',
                  'operator', 'operator_name', 'created_at']

    def get_boss_no(self, obj):
        return obj.account.account_no if obj.account_id else obj.user.boss_no

    def get_operator_name(self, obj):
        if not obj.operator_id:
            return ''
        return obj.operator.nickname or obj.operator.username

    def get_proof_image_url(self, obj):
        return build_media_url(self.context.get('request'), obj.proof_image)


class AdminDisposeRecordSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    nickname = serializers.CharField(source='user.nickname', read_only=True)
    dispose_type_display = serializers.CharField(source='get_dispose_type_display', read_only=True)
    operator_name = serializers.SerializerMethodField()

    class Meta:
        model = DisposeRecord
        fields = ['id', 'user', 'username', 'nickname', 'dispose_type', 'dispose_type_display',
                  'amount', 'reason', 'operator', 'operator_name', 'created_at']

    def get_operator_name(self, obj):
        if not obj.operator_id:
            return ''
        return obj.operator.nickname or obj.operator.username


# ---------------- 陪玩报单 ----------------
class AdminProviderReportSerializer(serializers.ModelSerializer):
    fulfillment = serializers.CharField(source='order.fulfillment', read_only=True, default='')
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    provider_name = serializers.SerializerMethodField()
    provider_username = serializers.CharField(source='provider.username', read_only=True)
    provider_nickname = serializers.CharField(source='provider.nickname', read_only=True)
    auditor_name = serializers.SerializerMethodField()
    proof_image_url = serializers.SerializerMethodField()
    suggested_commission_rate = serializers.SerializerMethodField()
    commission_source_label = serializers.SerializerMethodField()
    order_no = serializers.CharField(source='order.order_no', read_only=True, default='')
    entry_image_url = serializers.SerializerMethodField()
    completion_image_url = serializers.SerializerMethodField()
    result_image_urls = serializers.SerializerMethodField()
    settled_at = serializers.DateTimeField(source='order.settled_at', read_only=True, default=None)
    settlement_mode = serializers.CharField(source='order.settlement_mode', read_only=True, default='COMPLETION')
    expected_income = serializers.SerializerMethodField()

    class Meta:
        model = ProviderReport
        fields = [
            'id', 'provider', 'provider_name', 'provider_username', 'provider_nickname',
            'game_name', 'description', 'amount', 'order', 'order_no', 'fulfillment',
            'proof_image_url', 'entry_image_url', 'completion_image_url', 'result_image_urls',
            'status', 'status_display', 'commission_rate',
            'suggested_commission_rate', 'commission_source_label', 'payout_amount',
            'remark', 'audit_remark', 'auditor', 'auditor_name', 'transaction',
            'created_at', 'audited_at',
            'settled_at', 'settlement_mode', 'expected_income',
        ]

    def get_provider_name(self, obj):
        if not obj.provider_id:
            return ''
        return obj.provider.nickname or obj.provider.username

    def get_expected_income(self, obj):
        if obj.order_id:
            share = obj.order.providers.filter(provider_id=obj.provider_id).first()
            return share.provider_income if share else obj.order.provider_income
        return obj.amount * (100 - self.get_suggested_commission_rate(obj)) // 100

    def get_auditor_name(self, obj):
        if not obj.auditor_id:
            return ''
        return obj.auditor.nickname or obj.auditor.username

    def get_proof_image_url(self, obj):
        return build_media_url(self.context.get('request'), obj.proof_image)

    def _file_url(self, field):
        return build_media_url(self.context.get('request'), field)

    def get_entry_image_url(self, obj):
        return self._file_url(obj.entry_image)

    def get_completion_image_url(self, obj):
        return self._file_url(obj.completion_image)

    def get_result_image_urls(self, obj):
        return [self._file_url(item.image) for item in obj.result_images.all()]

    def get_suggested_commission_rate(self, obj):
        if obj.status == ProviderReport.Status.APPROVED:
            return obj.commission_rate
        rate, _ = self._resolve_commission(obj)
        return rate

    def get_commission_source_label(self, obj):
        if obj.status == ProviderReport.Status.APPROVED:
            return '审核快照'
        _, label = self._resolve_commission(obj)
        return label

    def _resolve_commission(self, obj):
        if obj.order_id:
            share = obj.order.providers.filter(provider_id=obj.provider_id).first()
            return (share.commission_rate if share else obj.order.commission_rate), '订单分账快照'
        provider = getattr(obj, 'provider', None)
        try:
            profile = provider.escort_profile if provider else None
        except EscortProfile.DoesNotExist:
            profile = None
        level = getattr(profile, 'level', None)
        if level is not None and level.is_active:
            return level.commission_rate, f'等级抽成（{level.name}）'
        return get_commission_rate(), '全局默认'


# ---------------- 提现申请 ----------------
class AdminWithdrawSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    payee_method_display = serializers.CharField(source='get_payee_method_display', read_only=True)
    user_name = serializers.SerializerMethodField()
    provider_username = serializers.CharField(source='user.username', read_only=True)
    provider_nickname = serializers.CharField(source='user.nickname', read_only=True)
    auditor_name = serializers.SerializerMethodField()

    class Meta:
        model = WithdrawRequest
        fields = [
            'id', 'user', 'user_name', 'provider_username', 'provider_nickname',
            'amount', 'tax_rate', 'tax_amount', 'actual_amount',
            'payee_method', 'payee_method_display',
            'payee_account', 'payee_name', 'status', 'status_display', 'remark',
            'audit_remark', 'payout_reference', 'paid_at',
            'auditor', 'auditor_name', 'transaction', 'created_at', 'audited_at',
        ]

    def get_user_name(self, obj):
        if not obj.user_id:
            return ''
        return obj.user.nickname or obj.user.username

    def get_auditor_name(self, obj):
        if not obj.auditor_id:
            return ''
        return obj.auditor.nickname or obj.auditor.username


# ---------------- 在线客服 ----------------
class AdminChatReplySerializer(serializers.Serializer):
    content = serializers.CharField(max_length=1000, required=False, allow_blank=True, default='')
    image = serializers.ImageField(required=False)

    def validate(self, attrs):
        if not attrs.get('content') and not attrs.get('image'):
            raise serializers.ValidationError('回复内容不能为空')
        return attrs


class AdminChatMessageSerializer(serializers.ModelSerializer):
    content_type_display = serializers.CharField(source='get_content_type_display', read_only=True)
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = ChatMessage
        fields = [
            'id', 'session', 'sender', 'is_from_support', 'content_type',
            'content_type_display', 'content', 'image_url', 'is_read', 'created_at',
        ]

    def get_image_url(self, obj):
        return build_media_url(self.context.get('request'), obj.image)


class AdminChatSessionSerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField()
    user_avatar = serializers.CharField(source='user.avatar_url', read_only=True)
    user_role_display = serializers.CharField(source='user.get_role_display', read_only=True)

    class Meta:
        model = ChatSession
        fields = [
            'id', 'user', 'user_name', 'user_avatar', 'user_role_display',
            'last_message', 'last_message_at', 'unread_support', 'created_at',
        ]

    def get_user_name(self, obj):
        if not obj.user_id:
            return ''
        return obj.user.nickname or obj.user.username


# ---------------- 优惠券 ----------------
class AdminCouponSerializer(serializers.ModelSerializer):
    discount_type_display = serializers.CharField(source='get_discount_type_display', read_only=True)
    is_claimable = serializers.BooleanField(read_only=True)

    class Meta:
        model = Coupon
        fields = ['id', 'name', 'discount_type', 'discount_type_display', 'threshold',
                  'discount_rate', 'scope', 'scope_variant', 'limit_units', 'is_public', 'service_ids', 'max_discount', 'experience_kind', 'platform',
                  'amount', 'valid_to', 'total_qty', 'claimed_qty', 'is_active',
                  'is_claimable', 'sort_order', 'created_at']
        read_only_fields = ['claimed_qty', 'created_at']

    def validate_service_ids(self, value):
        if value is None:
            return value
        if (not isinstance(value, list) or any(type(pk) is not int or pk <= 0 for pk in value)
                or len(set(value)) != len(value)):
            raise serializers.ValidationError('套餐编号须为不重复的正整数列表')
        from orders.catalog import visible_service_q
        services = ServiceItem.objects.filter(visible_service_q(), pk__in=value, is_addon=False, purchase_mode='DIRECT').exclude(service_category__is_gift=True)
        if set(services.values_list('pk', flat=True)) != set(value):
            raise serializers.ValidationError('套餐不存在、已停用或不能直接购买')
        return value

    def validate(self, attrs):
        amount = attrs.get('amount', getattr(self.instance, 'amount', 0))
        threshold = attrs.get('threshold', getattr(self.instance, 'threshold', 0))
        total = attrs.get('total_qty', getattr(self.instance, 'total_qty', 0))
        kind = attrs.get('discount_type', getattr(self.instance, 'discount_type', 'THRESHOLD'))
        if kind == Coupon.DiscountType.QUALIFICATION:
            ids = attrs.get('service_ids', getattr(self.instance, 'service_ids', None))
            if not ids:
                raise serializers.ValidationError({'service_ids': '资格券必须绑定可购买的套餐'})
            attrs.update(amount=0, threshold=0, discount_rate=100, limit_units=1)
        elif amount <= 0:
            raise serializers.ValidationError({'amount': '优惠券面额必须大于 0'})
        activity = attrs.get('experience_kind', getattr(self.instance, 'experience_kind', ''))
        platform = attrs.get('platform', getattr(self.instance, 'platform', ''))
        if activity:
            if kind != 'QUALIFICATION' or (activity == 'DAILY_VALUE' and platform not in ('PC','MOBILE')):
                raise serializers.ValidationError('超值券须为资格券，每日超值券须分别配置端游或手游')
            ids = attrs.get('service_ids', getattr(self.instance, 'service_ids', [])) or []
            services = ServiceItem.objects.filter(pk__in=ids)
            if any(row.experience_kind != activity or (platform and row.platform != platform) for row in services):
                raise serializers.ValidationError('绑定套餐的体验类型和平台须与资格券一致')
            attrs['valid_to'] = None  # daily expiration is frozen separately at claim
        if self.instance and self.instance.claimed_qty:
            for field in ('discount_type','experience_kind','platform'):
                if field in attrs and attrs[field] != getattr(self.instance, field):
                    raise serializers.ValidationError('已发放的优惠券不能变更资格身份，请新建券模板')
        if threshold < 0:
            raise serializers.ValidationError({'threshold': '使用门槛不能为负数'})
        if total and total < getattr(self.instance, 'claimed_qty', 0):
            raise serializers.ValidationError({'total_qty': '发放总量不能小于已领取数量'})
        return attrs


class AdminUserCouponSerializer(serializers.ModelSerializer):
    coupon_name = serializers.CharField(source='coupon.name', read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)
    user_name = serializers.SerializerMethodField()
    order_no = serializers.CharField(source='order.order_no', read_only=True, default='')
    status = serializers.SerializerMethodField()
    status_display = serializers.SerializerMethodField()

    class Meta:
        model = UserCoupon
        fields = ['id', 'user', 'username', 'user_name', 'coupon', 'coupon_name', 'status',
                  'status_display', 'order', 'order_no', 'claimed_at', 'used_at']

    def get_user_name(self, obj):
        account = obj.account or obj.user
        return (account.nickname or account.username) if account else ''

    def get_status(self, obj):
        if obj.status == UserCoupon.Status.UNUSED and (not obj.is_usable):
            return UserCoupon.Status.EXPIRED
        return obj.status

    def get_status_display(self, obj):
        return UserCoupon.Status(self.get_status(obj)).label


# ---------------- 公告 ----------------
class AdminAnnouncementSerializer(serializers.ModelSerializer):
    def validate_content(self, value):
        from engagement.serializers import rich_text
        return rich_text(value)
    class Meta:
        model = Announcement
        fields = ['id', 'title', 'content', 'is_pinned', 'is_active', 'sort_order',
                  'created_at', 'updated_at']


# ---------------- Banner ----------------
class AdminBannerSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()
    link_type_display = serializers.CharField(source='get_link_type_display', read_only=True)

    class Meta:
        model = Banner
        fields = ['id', 'image', 'image_url', 'title', 'link_type', 'link_type_display',
                  'link_value', 'sort_order', 'is_active', 'created_at']
        extra_kwargs = {'image': {'write_only': True, 'required': False}}

    def get_image_url(self, obj):
        return build_media_url(self.context.get('request'), obj.image)

    def validate(self, attrs):
        if self.instance is None and not attrs.get('image'):
            raise serializers.ValidationError({'image': '请上传轮播图片'})
        link_type = attrs.get('link_type', getattr(self.instance, 'link_type', Banner.LinkType.NONE))
        target = attrs.get('link_value', getattr(self.instance, 'link_value', '')).strip()
        if link_type == Banner.LinkType.NONE:
            attrs['link_value'] = ''
        elif link_type in (Banner.LinkType.PRODUCT, Banner.LinkType.ANNOUNCEMENT):
            model = ServiceItem if link_type == Banner.LinkType.PRODUCT else Announcement
            if not target.isdigit() or not model.objects.filter(pk=int(target)).exists():
                raise serializers.ValidationError({'link_value': '跳转目标不存在，请填写有效的商品或公告编号'})
        elif link_type == Banner.LinkType.URL:
            try:
                URLValidator(schemes=['https'])(target)
            except DjangoValidationError:
                raise serializers.ValidationError({'link_value': '请填写完整的 HTTPS 链接'})
        elif link_type in (Banner.LinkType.GAMEPLAY, Banner.LinkType.CONTENT):
            from orders.models import Gameplay
            from engagement.models import ClubContent
            model = Gameplay if link_type == Banner.LinkType.GAMEPLAY else ClubContent
            if not target.isascii() or not target.isdigit() or not 0 < int(target) < 2 ** 63 or not model.objects.filter(pk=int(target), is_active=True).exists():
                raise serializers.ValidationError({'link_value': '请选择已启用的玩法或运营内容'})
        elif link_type == Banner.LinkType.PAGE:
            if target not in {'catalog', 'companions', 'gifts', 'coupons', 'checkin', 'points', 'membership', 'support'}:
                raise serializers.ValidationError({'link_value': '请选择支持的小程序业务入口'})
        if link_type != Banner.LinkType.NONE:
            attrs['link_value'] = target
        return attrs

    def validate_image(self, image):
        # DRF ImageField 已用 Pillow 解析，校验过的文件带 .image(PIL) 属性
        pil = getattr(image, 'image', None)
        width = getattr(pil, 'width', None)
        height = getattr(pil, 'height', None)
        if width != BANNER_IMAGE_WIDTH or height != BANNER_IMAGE_HEIGHT:
            raise serializers.ValidationError(
                f'图片尺寸必须为 {BANNER_IMAGE_WIDTH}×{BANNER_IMAGE_HEIGHT} 像素，当前为 {width}×{height}。'
            )
        return image


# ---------------- 成就 ----------------
class AdminAchievementSerializer(serializers.ModelSerializer):
    metric_display = serializers.CharField(source='get_metric_display', read_only=True)

    class Meta:
        model = Achievement
        fields = ['id', 'code', 'title', 'desc', 'icon', 'metric', 'metric_display',
                  'target', 'sort_order', 'is_active', 'created_at']


class AdminCheckinRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = CheckinRuleConfig
        fields = [
            'id', 'daily_spend_required', 'makeup_card_spend_required',
            'max_makeup_cards', 'full_attendance_reward_name',
            'full_attendance_reward_desc', 'full_attendance_tag_code', 'updated_at',
        ]
        read_only_fields = ['id', 'updated_at']

    def validate(self, attrs):
        daily = attrs.get('daily_spend_required', getattr(self.instance, 'daily_spend_required', 0))
        card = attrs.get(
            'makeup_card_spend_required',
            getattr(self.instance, 'makeup_card_spend_required', 0),
        )
        if daily <= 0 or card <= 0:
            raise serializers.ValidationError('消费门槛必须大于 0')
        if card < daily:
            raise serializers.ValidationError('补签卡消费门槛不能低于签到门槛')
        return attrs


class AdminCheckinGiftSerializer(serializers.ModelSerializer):
    class Meta:
        model = CheckinGift
        fields = [
            'id', 'checkin_day', 'name', 'description', 'icon',
            'reward_amount', 'is_active', 'updated_at',
        ]
        read_only_fields = ['updated_at']

    def validate_checkin_day(self, value):
        if value < 1 or value > 31:
            raise serializers.ValidationError('签到天数必须在 1-31 之间')
        return value


class AdminCheckinProgressSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    nickname = serializers.CharField(source='user.nickname', read_only=True)
    checked_count = serializers.SerializerMethodField()

    class Meta:
        model = CheckinMonthProgress
        fields = [
            'id', 'user', 'username', 'nickname', 'year', 'month', 'checked_count',
            'makeup_cards', 'full_attendance_awarded', 'full_attendance_reward_name',
            'full_attendance_tag_code', 'full_attendance_awarded_at', 'updated_at',
        ]

    def get_checked_count(self, obj):
        return obj.user.checkin_records.filter(
            checkin_date__year=obj.year, checkin_date__month=obj.month,
        ).count()


# ---------------- 服务项/礼物单 ----------------
class AdminGameCategorySerializer(serializers.ModelSerializer):
    icon = serializers.ImageField(write_only=True, required=False, allow_null=True)
    icon_url = serializers.SerializerMethodField()
    service_count = serializers.IntegerField(read_only=True)
    active_service_count = serializers.IntegerField(read_only=True)
    escort_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = GameCategory
        fields = ['id', 'name', 'icon', 'icon_url', 'remark', 'sort_order', 'is_active', 'created_at', 'service_count', 'active_service_count', 'escort_count']
        read_only_fields = ['created_at']

    def get_icon_url(self, obj):
        return build_media_url(self.context.get('request'), obj.icon)


class AdminServiceCategorySerializer(serializers.ModelSerializer):
    def validate_is_gift(self, value):
        if self.instance and value != self.instance.is_gift and Order.objects.filter(service__service_category=self.instance).exists():
            raise serializers.ValidationError('该分类已有订单，不能变更礼物属性，请新建分类')
        return value

    class Meta:
        model = ServiceCategory
        fields = ['id', 'name', 'is_gift', 'order_kind', 'remark', 'sort_order', 'is_active', 'created_at']
        read_only_fields = ['created_at']


class AdminServiceItemSerializer(serializers.ModelSerializer):
    @transaction.atomic
    def update(self, instance, validated_data):
        old_game = instance.game_category_id
        instance = super().update(instance, validated_data)
        if old_game != instance.game_category_id:
            for profile in instance.escorts.select_for_update().order_by('pk'):
                if old_game and not profile.service_items.filter(game_category_id=old_game).exists():
                    profile.game_categories.remove(old_game)
                if instance.game_category_id:
                    profile.game_categories.add(instance.game_category_id)
        return instance

    rules = serializers.CharField(max_length=5000, allow_blank=True, required=False)
    details_html = serializers.CharField(max_length=100000, allow_blank=True, required=False)

    def validate_details_html(self, value):
        from common.rich_text import sanitize_catalog_html
        return sanitize_catalog_html(value)

    cover_url = serializers.CharField(max_length=500, allow_blank=True, required=False)
    rules_images = serializers.ListField(child=serializers.CharField(max_length=500), max_length=10, required=False)
    introduction_images = serializers.ListField(child=serializers.CharField(max_length=500), max_length=10, required=False)
    def validate_cover_url(self, value):
        if value.startswith('/media/club-catalog/'):
            from engagement.serializers import image_url
            return image_url(value)
        if value:
            serializers.URLField().run_validation(value)
        return value
    def validate_rules_images(self, value):
        return [self.validate_cover_url(url) for url in value if url]
    validate_introduction_images = validate_rules_images
    gameplay_name = serializers.CharField(source='gameplay.name', read_only=True, default='')
    is_gift = serializers.BooleanField(source='service_category.is_gift', read_only=True, default=False)

    def validate(self, attrs):
        from orders.gameplay import validate_pool
        get = lambda key, default=None: attrs.get(key, getattr(self.instance, key, default))
        from users.qualifications import grade_options
        provider_kind, grade = get('provider_kind',''), get('provider_grade','')
        if provider_kind or grade:
            if not provider_kind or not grade or not get('game_category') or grade not in grade_options(provider_kind,get('platform',''),get('security_level','')):
                raise serializers.ValidationError('请完整配置游戏、接单方向、端手游、难度及订单档位')
        experience = get('experience_kind', '')
        benefit = get('benefit_kind', '')
        if experience:
            attrs['service_type'] = 'EXPERIENCE'
        elif get('service_type','REGULAR') == 'EXPERIENCE':
            raise serializers.ValidationError('体验单须选择体验资格类型')
        if experience and (get('platform') not in ('PC','MOBILE') or get('is_addon')
                or get('billing_provider_count',1) != 1 or get('purchase_mode','DIRECT') != 'DIRECT'):
            raise serializers.ValidationError('体验套餐须为端游/手游的一份单陪主套餐，直接下单')
        if benefit and (experience or get('is_addon') or get('billing_provider_count',1) != 1):
            raise serializers.ValidationError('VIP服务券仅可绑定普通单陪主套餐')
        if benefit == 'COMPANION_HOUR' and get('price_unit','份') not in ('小时','时','hour'):
            raise serializers.ValidationError('小时陪玩券绑定套餐须按小时计价')
        if self.instance and Order.objects.filter(service=self.instance).exists():
            if experience != self.instance.experience_kind or benefit != self.instance.benefit_kind:
                raise serializers.ValidationError('已有订单的套餐不能变更体验资格或VIP权益类型，请新建套餐')
        category = get('service_category')
        is_gift = bool(category and category.is_gift)
        if not is_gift and (get('gift_naming_days', 0) or get('gift_festival', '') or get('gift_starts_at') or get('gift_ends_at')):
            raise serializers.ValidationError('冠名天数与节日限定仅适用于礼物')
        if get('gift_festival', '') and get('price', 0) < 2100:
            raise serializers.ValidationError('节日限定礼物最低 21 元，即 210 兴安币')
        start, end = get('gift_starts_at'), get('gift_ends_at')
        if start and end and start >= end:
            raise serializers.ValidationError('礼物结束时间必须晚于开始时间')
        if self.instance and Order.objects.filter(service=self.instance).exists():
            was_gift = bool(self.instance.service_category and self.instance.service_category.is_gift)
            if was_gift != is_gift:
                raise serializers.ValidationError('已有订单的商品不能在服务和礼物之间转换，请新建商品')
        if is_gift and (provider_kind or grade or get('required_level') or get('is_addon') or get('purchase_mode', 'DIRECT') != 'DIRECT'):
            raise serializers.ValidationError('礼物须直接赠送，不能配置接单档位、附加费用或客服确认下单')
        gameplay = get('gameplay')
        if gameplay:
            if get('purchase_mode', 'DIRECT') == 'DIRECT' and (not get('platform') or not get('security_level')):
                raise serializers.ValidationError('请选择游戏平台与适用难度（通用服务可选择不限难度）')
            category = get('service_category')
            if category and category.is_gift:
                raise serializers.ValidationError('礼物不能作为玩法商品')
            if gameplay.kind == 'ROULETTE':
                attrs['roulette_pool'] = validate_pool(get('roulette_pool', []))
            elif get('roulette_pool', []):
                raise serializers.ValidationError('普通玩法不能配置转盘奖池')
        elif not provider_kind and (get('platform') or get('security_level') or get('roulette_pool', [])):
            raise serializers.ValidationError('请先选择所属玩法')
        return attrs

    game_category_name = serializers.CharField(source='game_category.name', read_only=True, default='')
    service_category_name = serializers.CharField(source='service_category.name', read_only=True, default='')
    required_level_name = serializers.CharField(source='required_level.name', read_only=True, default='')

    class Meta:
        model = ServiceItem
        fields = ['id', 'name', 'description', 'price', 'gift_naming_days', 'gift_festival', 'gift_starts_at', 'gift_ends_at', 'gameplay', 'gameplay_name', 'platform', 'security_level', 'rules', 'details_html', 'rules_images', 'introduction_images', 'roulette_pool',
                  'game_category', 'game_category_name',
                  'service_category', 'service_category_name', 'is_gift',
                  'required_level', 'required_level_name', 'provider_kind', 'provider_grade',
                  'commission_rate', 'commission_policy', 'experience_kind', 'benefit_kind', 'service_type', 'allows_discount_coupons', 'cover_url', 'images', 'highlights', 'sort_order',
                  'is_active', 'created_at', 'price_unit', 'billing_provider_count', 'purchase_mode', 'purchase_note',
                  'catalog_key', 'catalog_source', 'is_addon', 'catalog_family', 'catalog_variant']
        read_only_fields = ['catalog_key', 'catalog_source']


# ---------------- 促销活动 ----------------
class AdminPromotionSerializer(serializers.ModelSerializer):
    scope_display = serializers.CharField(source='get_scope_display', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True, default='')
    item_names = serializers.SerializerMethodField()

    class Meta:
        model = Promotion
        fields = ['id', 'title', 'remark', 'discount_rate', 'commission_rate',
                  'scope', 'scope_display', 'category', 'category_name', 'items', 'item_names',
                  'start_at', 'end_at', 'priority', 'is_active',
                  'created_at', 'updated_at']
        read_only_fields = ['created_at', 'updated_at']

    def get_item_names(self, obj):
        return [{'id': s.id, 'name': s.name} for s in obj.items.all()]

    def validate(self, attrs):
        start_at = attrs.get('start_at', getattr(self.instance, 'start_at', None))
        end_at = attrs.get('end_at', getattr(self.instance, 'end_at', None))
        if start_at and end_at and end_at <= start_at:
            raise serializers.ValidationError({'end_at': '结束时间必须晚于开始时间'})
        scope = attrs.get('scope', getattr(self.instance, 'scope', Promotion.Scope.ALL))
        category = attrs.get('category', getattr(self.instance, 'category', None))
        if scope == Promotion.Scope.CATEGORY and not category:
            raise serializers.ValidationError({'category': '请选择适用分类'})
        if scope == Promotion.Scope.ITEMS:
            items = attrs.get('items', self.instance.items.all() if self.instance else [])
            if not items:
                raise serializers.ValidationError({'items': '请选择参与活动的商品'})
        return attrs


# ---------------- 客服名片 ----------------
class AdminSupportCardSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupportContactCard
        fields = ['id', 'name', 'company', 'wechat_id', 'wecom_corp_id',
                  'wecom_service_url', 'avatar_url', 'qrcode_url',
                  'tips', 'is_active', 'sort_order', 'created_at']

    def validate(self, attrs):
        corp_id = attrs.get('wecom_corp_id', getattr(self.instance, 'wecom_corp_id', ''))
        service_url = attrs.get('wecom_service_url', getattr(self.instance, 'wecom_service_url', ''))
        if bool(corp_id) != bool(service_url):
            raise serializers.ValidationError('企业ID和微信客服接入链接必须同时填写')
        if service_url:
            parts = urlsplit(service_url)
            if parts.scheme != 'https' or parts.netloc.lower() != 'work.weixin.qq.com' or not re.fullmatch(r'/kfid/[A-Za-z0-9_-]+/?', parts.path):
                raise serializers.ValidationError({'wecom_service_url': '请完整复制企业微信后台生成的 https://work.weixin.qq.com/kfid/... 客服链接'})
        return attrs


# ---------------- 试音链接 ----------------
class AdminAuditionLinkSerializer(serializers.ModelSerializer):
    operator_name = serializers.SerializerMethodField()
    boss_user_name = serializers.SerializerMethodField()
    provider_user_name = serializers.SerializerMethodField()
    boss_url = serializers.SerializerMethodField()
    provider_url = serializers.SerializerMethodField()

    class Meta:
        model = AuditionLink
        fields = ['id', 'title', 'remark', 'expire_at', 'is_active',
                  'operator', 'operator_name', 'boss_token', 'provider_token',
                  'boss_user', 'boss_user_name', 'provider_user', 'provider_user_name',
                  'boss_url', 'provider_url', 'created_at', 'updated_at']
        read_only_fields = ['operator', 'boss_token', 'provider_token',
                            'created_at', 'updated_at']

    def get_operator_name(self, obj):
        if not obj.operator_id:
            return ''
        return obj.operator.nickname or obj.operator.username

    def get_boss_user_name(self, obj):
        if not obj.boss_user_id:
            return ''
        return obj.boss_user.nickname or obj.boss_user.username

    def get_provider_user_name(self, obj):
        if not obj.provider_user_id:
            return ''
        return obj.provider_user.nickname or obj.provider_user.username

    def _build_url(self, token, role):
        from django.conf import settings
        base_setting = (
            'AUDITION_BOSS_LINK_BASE_URL'
            if role == 'boss'
            else 'AUDITION_PROVIDER_LINK_BASE_URL'
        )
        base = getattr(settings, base_setting, settings.AUDITION_LINK_BASE_URL).rstrip('/')
        return f'{base}?token={token}&role={role}'

    def get_boss_url(self, obj):
        return self._build_url(obj.boss_token, 'boss')

    def get_provider_url(self, obj):
        return self._build_url(obj.provider_token, 'provider')


class AdminAuditionSignupSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    applicant_name = serializers.SerializerMethodField()
    link_title = serializers.SerializerMethodField()
    auditor_name = serializers.SerializerMethodField()

    class Meta:
        model = AuditionSignup
        fields = ['id', 'link', 'link_title', 'applicant', 'applicant_name',
                  'contact', 'game', 'remark', 'status', 'status_display',
                  'auditor', 'auditor_name', 'audit_remark', 'audited_at',
                  'created_at', 'updated_at']

    def get_applicant_name(self, obj):
        if not obj.applicant_id:
            return ''
        return obj.applicant.nickname or obj.applicant.username

    def get_link_title(self, obj):
        return obj.link.title if obj.link_id else ''

    def get_auditor_name(self, obj):
        if not obj.auditor_id:
            return ''
        return obj.auditor.nickname or obj.auditor.username


# ---------------- 站内消息 ----------------
class AdminMessageSerializer(serializers.ModelSerializer):
    type_display = serializers.CharField(source='get_type_display', read_only=True)
    recipient_name = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = ['id', 'recipient', 'recipient_name', 'type', 'type_display', 'title',
                  'preview', 'detail', 'action_url', 'is_read', 'related_order_id',
                  'created_at']

    def get_recipient_name(self, obj):
        return obj.recipient.nickname or obj.recipient.username if obj.recipient_id else ''


# ---------------- 角色 ----------------
class AdminRoleSerializer(serializers.ModelSerializer):
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = AdminRole
        fields = ['id', 'name', 'code', 'description', 'permissions', 'is_active',
                  'sort_order', 'member_count', 'created_at']

    def get_member_count(self, obj):
        return obj.members.count()

    def validate_permissions(self, value):
        from .permissions import ALL_PERMISSION_SET
        if not isinstance(value, list) or any(not isinstance(p, str) for p in value):
            raise serializers.ValidationError('权限必须为权限标识列表')
        if any(p not in ALL_PERMISSION_SET for p in value):
            raise serializers.ValidationError('包含未注册的权限，请刷新权限列表后重试')
        return list(dict.fromkeys(value))

    def validate(self, attrs):
        if self.instance:
            if attrs.get('code', self.instance.code) != self.instance.code:
                raise serializers.ValidationError({'code': '已有角色的标识不可修改'})
            if self.instance.code == 'super_admin':
                if not attrs.get('is_active', self.instance.is_active):
                    raise serializers.ValidationError({'is_active': '内置超级管理员角色不可停用'})
                if set(attrs.get('permissions', self.instance.permissions)) != set(self.instance.permissions):
                    raise serializers.ValidationError({'permissions': '内置超级管理员权限受保护，请新建角色配置其他权限'})
        return attrs


# ---------------- 管理员 / 客服 ----------------
class AdminMembershipSerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(source='account', read_only=True)
    username = serializers.CharField(source='account.username', read_only=True)
    nickname = serializers.CharField(source='account.nickname', read_only=True)
    phone = serializers.CharField(source='account.phone', read_only=True)
    role_ids = serializers.SerializerMethodField()
    role_names = serializers.SerializerMethodField()
    is_superuser = serializers.SerializerMethodField()
    today_dispatch_amount = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = AdminMembership
        fields = ['id', 'user', 'username', 'nickname', 'phone', 'role_ids', 'role_names',
                  'is_superuser', 'remark', 'today_dispatch_amount',
                  'is_active', 'created_at']

    def get_role_ids(self, obj):
        return [r.id for r in obj.roles.all()]

    def get_role_names(self, obj):
        return [r.name for r in obj.roles.all()]

    def get_is_superuser(self, obj):
        return any(role.code == 'super_admin' for role in obj.roles.all())


class AdminCreateMembershipSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(min_length=6, max_length=128, trim_whitespace=False)
    nickname = serializers.CharField(max_length=50, required=False, allow_blank=True, default='')
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default='')
    remark = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    roles = serializers.PrimaryKeyRelatedField(queryset=AdminRole.objects.all(), many=True)

    def validate_username(self, value):
        value = value.strip()
        if (
            ClubAccount.objects.filter(username=value).exists()
            or User.objects.filter(username=value).exists()
        ):
            raise serializers.ValidationError('用户名已存在')
        return value


class AdminUpdateMembershipSerializer(serializers.Serializer):
    nickname = serializers.CharField(max_length=50, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    remark = serializers.CharField(max_length=255, required=False, allow_blank=True)
    roles = serializers.PrimaryKeyRelatedField(queryset=AdminRole.objects.all(), many=True, required=False)
    is_active = serializers.BooleanField(required=False)


class AdminAuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdminAuditLog
        fields = ['id', 'operator', 'operator_name', 'method', 'path', 'resource',
                  'object_id', 'request_body', 'status_code', 'ip', 'created_at']


# ---------------- 封禁审计 ----------------
class BanRecordSerializer(serializers.ModelSerializer):
    """封禁记录只读序列化：供审计列表/详情查看。"""
    # 不要写 source='account_id'：DRF 在字段 bind 阶段硬断言「source 与字段名相同」
    # 属于冗余并直接抛 AssertionError。FK 的隐式 account_id 属性本来就能直接取。
    account_id = serializers.IntegerField(read_only=True)
    account_nickname = serializers.CharField(
        source='account.nickname', read_only=True, default='',
    )
    account_type = serializers.CharField(
        source='account.account_type', read_only=True, default='',
    )
    operator_name = serializers.CharField(
        source='operator_account.nickname', read_only=True, default='',
    )
    lifted_by_name = serializers.CharField(
        source='lifted_by_account.nickname', read_only=True, default='',
    )
    status_display = serializers.CharField(
        source='get_status_display', read_only=True,
    )
    scope_display = serializers.CharField(
        source='get_scope_display', read_only=True,
    )

    class Meta:
        model = AccountBan
        fields = [
            'id', 'account_id', 'account_nickname', 'account_type',
            'reason', 'scope', 'scope_display',
            'operator', 'operator_account', 'operator_name',
            'status', 'status_display',
            'expires_at', 'banned_at',
            'lifted_at', 'lifted_by', 'lifted_by_name', 'lift_reason',
        ]


class BanActionSerializer(serializers.Serializer):
    """封禁/解封入参：原因必填，可选到期时间。"""
    reason = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate_reason(self, value):
        # 封禁时原因必填；解封时可选。由调用方区分 required。
        return value
