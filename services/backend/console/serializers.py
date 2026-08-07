from django.contrib.auth import get_user_model
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

        attempted = [
            field for field in self.LOCKED_SWITCH_REASONS
            if field in attrs and bool(attrs[field]) != bool(getattr(instance, field))
        ]
        if attempted:
            raise serializers.ValidationError({
                field: self.LOCKED_SWITCH_REASONS[field] for field in attempted
            })
        return attrs

    def update(self, instance, validated_data):
        instance = super().update(instance, validated_data)
        mapping = getattr(instance, 'legacy_mapping', None)
        if mapping is not None:
            legacy_user = User.objects.filter(pk=mapping.legacy_user_id).first()
            if legacy_user is not None:
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
class AdminEscortSerializer(serializers.ModelSerializer):
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
            'gender', 'gender_display',
            'city', 'service_area', 'price_per_hour', 'rank_tier', 'win_rate',
            'level', 'level_name',
            'game_categories', 'game_category_names',
            'service_items', 'service_item_names',
            'status', 'status_display', 'is_verified', 'escort_no',
            'deposit_required', 'deposit_paid', 'total_reward', 'total_penalty',
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

    def update(self, instance, validated_data):
        avatar = validated_data.get('avatar')
        instance = super().update(instance, validated_data)
        # 迁移期同步新旧账户头像，避免未切换的订单展示链路丢失头像。
        if avatar is not None and instance.avatar:
            avatar_url = build_media_url(self.context.get('request'), instance.avatar)
            if instance.account_id:
                instance.account.avatar_url = avatar_url
                instance.account.save(update_fields=['avatar_url'])
            instance.user.avatar_url = avatar_url
            instance.user.save(update_fields=['avatar_url'])
        return instance


class AdminCreateEscortSerializer(serializers.Serializer):
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
    avatar = serializers.ImageField(required=False, allow_null=True)
    intro_video = serializers.FileField(required=False, allow_null=True)
    cheat_proof = serializers.ImageField(required=False, allow_null=True)

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
            'settlement_base', 'provider_income', 'settled_at', 'created_at',
        ]

    def get_provider_name(self, obj):
        if obj.provider_account_id:
            return obj.provider_account.nickname or obj.provider_account.username
        return obj.provider_name_snapshot or ''


class AdminOrderSerializer(serializers.ModelSerializer):
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
            'id', 'order_no', 'status', 'status_display', 'payment_status',
            'payment_status_display', 'escort_mode', 'escort_mode_display',
            'amount', 'game_rounds', 'service_name_snapshot',
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
    username = serializers.CharField(source='user.username', read_only=True)
    nickname = serializers.CharField(source='user.nickname', read_only=True)
    phone = serializers.CharField(source='user.phone', read_only=True)
    boss_no = serializers.CharField(source='user.boss_no', read_only=True)
    avatar_url = serializers.CharField(source='user.avatar_url', read_only=True)
    boss_type_name = serializers.CharField(source='user.boss_type.name', read_only=True)

    class Meta:
        model = Wallet
        fields = ['id', 'user', 'username', 'nickname', 'phone', 'boss_no', 'avatar_url',
                  'boss_type_name', 'balance',
                  'frozen_amount', 'total_recharge', 'total_gift', 'is_active', 'updated_at']
        read_only_fields = ['user', 'balance', 'frozen_amount', 'total_recharge', 'total_gift']


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
    username = serializers.CharField(source='user.username', read_only=True)
    nickname = serializers.CharField(source='user.nickname', read_only=True)
    phone = serializers.CharField(source='user.phone', read_only=True)
    boss_no = serializers.CharField(source='user.boss_no', read_only=True)
    avatar_url = serializers.CharField(source='user.avatar_url', read_only=True)
    boss_type_name = serializers.CharField(source='user.boss_type.name', read_only=True)
    operator_name = serializers.SerializerMethodField()
    proof_image_url = serializers.SerializerMethodField()
    total_amount = serializers.IntegerField(read_only=True)

    class Meta:
        model = RechargeRecord
        fields = ['id', 'user', 'username', 'nickname', 'phone', 'boss_no', 'avatar_url',
                  'boss_type_name', 'amount', 'gift_amount',
                  'total_amount', 'trade_no', 'proof_image_url', 'remark',
                  'operator', 'operator_name', 'created_at']

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

    class Meta:
        model = ProviderReport
        fields = [
            'id', 'provider', 'provider_name', 'provider_username', 'provider_nickname',
            'game_name', 'description', 'amount', 'order', 'order_no',
            'proof_image_url', 'entry_image_url', 'completion_image_url', 'result_image_urls',
            'status', 'status_display', 'commission_rate',
            'suggested_commission_rate', 'commission_source_label', 'payout_amount',
            'remark', 'audit_remark', 'auditor', 'auditor_name', 'transaction',
            'created_at', 'audited_at',
        ]

    def get_provider_name(self, obj):
        if not obj.provider_id:
            return ''
        return obj.provider.nickname or obj.provider.username

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
                  'amount', 'valid_to', 'total_qty', 'claimed_qty', 'is_active',
                  'is_claimable', 'sort_order', 'created_at']
        read_only_fields = ['claimed_qty', 'created_at']


class AdminUserCouponSerializer(serializers.ModelSerializer):
    coupon_name = serializers.CharField(source='coupon.name', read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = UserCoupon
        fields = ['id', 'user', 'username', 'coupon', 'coupon_name', 'status',
                  'status_display', 'order', 'claimed_at', 'used_at']


# ---------------- 公告 ----------------
class AdminAnnouncementSerializer(serializers.ModelSerializer):
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

    class Meta:
        model = GameCategory
        fields = ['id', 'name', 'icon', 'icon_url', 'remark', 'sort_order', 'is_active', 'created_at']
        read_only_fields = ['created_at']

    def get_icon_url(self, obj):
        return build_media_url(self.context.get('request'), obj.icon)


class AdminServiceCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceCategory
        fields = ['id', 'name', 'is_gift', 'remark', 'sort_order', 'is_active', 'created_at']
        read_only_fields = ['created_at']


class AdminServiceItemSerializer(serializers.ModelSerializer):
    game_category_name = serializers.CharField(source='game_category.name', read_only=True, default='')
    service_category_name = serializers.CharField(source='service_category.name', read_only=True, default='')
    required_level_name = serializers.CharField(source='required_level.name', read_only=True, default='')

    class Meta:
        model = ServiceItem
        fields = ['id', 'name', 'description', 'price',
                  'game_category', 'game_category_name',
                  'service_category', 'service_category_name',
                  'required_level', 'required_level_name',
                  'commission_rate', 'cover_url', 'images', 'highlights', 'sort_order',
                  'is_active', 'created_at']


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
        if service_url and not service_url.startswith('https://work.weixin.qq.com/kfid/'):
            raise serializers.ValidationError({'wecom_service_url': '请填写企业微信后台生成的 kfid 客服链接'})
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
    password = serializers.CharField(max_length=128)
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
