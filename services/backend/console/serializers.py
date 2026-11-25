import re
from urllib.parse import urlsplit

from stores.conf import settings
from stores.context import current_currency_name
from django.contrib.auth import get_user_model
from django.core.validators import URLValidator
from django.core.exceptions import ValidationError as DjangoValidationError
from stores.db import transaction
from django.utils import timezone
from rest_framework import serializers
from identifiers.serializers import EntityNumberModelSerializer, EntityNumberListSerializer
from wallet.serializers import PointsReportReadMixin
from wallet.settlement_display import OrderSettlementListSerializer, ReportSettlementListSerializer

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


def _check_content_field(serializer, field, value, *, allow_reserved=False):
    from common.compliance import check_content
    result = check_content(field, value, allow_reserved=allow_reserved)
    if result is not None:
        if not hasattr(serializer, '_compliance_results'):
            serializer._compliance_results = {}
        serializer._compliance_results[field] = result
    return value


def _official_name_allowed(serializer):
    from .permissions import get_account_permissions
    request = serializer.context.get('request')
    account = getattr(request, 'account', None) if request is not None else None
    return bool(account is not None and 'content:official_name' in get_account_permissions(account))


def _record_content_field(serializer, content_type, instance, field):
    from common.compliance import record_content_receipt, store_actor_from_request
    request = serializer.context.get('request')
    account = getattr(request, 'account', None) if request is not None else None
    actor_type, actor_id, actor_label = store_actor_from_request(request) if request is not None else ('SYSTEM', '', '')
    result = getattr(serializer, '_compliance_results', {}).get(field)
    if result is None:
        return
    record_content_receipt(content_type, instance.pk, field, result, actor_account=account,
                           actor_type=actor_type, actor_id=actor_id, actor_label=actor_label)


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
class AdminUserSerializer(EntityNumberModelSerializer):
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
        return wallet.balance if wallet else None

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
class AdminBossTypeSerializer(EntityNumberModelSerializer):
    class Meta:
        model = BossType
        fields = ['id', 'name', 'discount_rate', 'color', 'remark', 'sort_order',
                  'is_active', 'created_at']
        read_only_fields = ['created_at']


# ---------------- 陪玩等级 ----------------
class AdminEscortLevelSerializer(EntityNumberModelSerializer):
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

class AdminEscortSerializer(EscortRelationsInputMixin, EntityNumberModelSerializer):
    qualifications = QualificationsField(required=False)
    user = serializers.PrimaryKeyRelatedField(source='account', read_only=True)
    username = serializers.CharField(source='account.username', read_only=True, allow_null=True)
    phone = serializers.CharField(source='account.phone', read_only=True, allow_null=True)
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
            'avatar_url', 'avatar_source', 'intro_video_url', 'cheat_proof_url',
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
        'deposit_required': '押金目标由门店统一配置，请在押金规则中修改',
    }

    def validate_display_name(self, value):
        return _check_content_field(self, 'PROVIDER_DISPLAY_NAME', value,
                                    allow_reserved=_official_name_allowed(self))

    def validate(self, attrs):
        """拦截绕过押金流程直改 ``deposit_paid`` 的后台 PATCH/PUT。

        ``deposit_paid`` 是资金字段：唯一合法写入点是 ``wallet.views.DepositView``
        （扣陪玩钱包 + 累加已缴 + 落 DEPOSIT 流水，三件事在同一事务里）。
        从「编辑陪玩」表单直接改它，会造成「已缴押金变了，但没有任何一笔流水
        对得上」——对账时这笔差额无法归因，也无法追责到操作人。

        与 :class:`AdminUserSerializer` 同款：**只在值真的发生变化时拒绝**。
        表单整体提交会带上当前值，一律拒绝会导致改个昵称都保存失败。

        ``deposit_required`` 是历史档案值；实际目标由门店配置统一决定。
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
        from wallet.club_rules import deposit_target
        mode = attrs.get('deposit_mode', instance.deposit_mode)
        if mode != instance.deposit_mode and instance.deposit_paid:
            raise serializers.ValidationError({'deposit_mode': '已有押金时请先走退还流程，再切换缴纳模式'})
        if mode != instance.deposit_mode:
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
        # Skill edits must take catalog locks before the provider lock, the
        # same order as checkout and the provider's own skill editor.
        if 'service_items' in validated_data:
            from orders.models import ServiceItem
            list(ServiceItem.objects.select_for_update().filter(
                pk__in=[item.pk for item in validated_data['service_items']]).order_by('pk'))
        avatar_changed = 'avatar' in validated_data
        account_id = instance.account_id
        if avatar_changed and account_id:
            from club_accounts.models import ClubAccount
            ClubAccount.objects.select_for_update().get(pk=account_id)
        instance = EscortProfile.objects.select_for_update().get(pk=instance.pk)
        if avatar_changed and instance.account_id != account_id:
            raise serializers.ValidationError('陪玩账号已变化，请刷新后重试')
        if validated_data.get('deposit_mode', instance.deposit_mode) != instance.deposit_mode and instance.deposit_paid:
            raise serializers.ValidationError({'deposit_mode': '已有押金时请先走退还流程，再切换缴纳模式'})
        avatar = validated_data.get('avatar')
        if avatar_changed:
            validated_data['avatar_source'] = 'STAFF_UPLOAD' if avatar else 'UNKNOWN'
        qualifications = validated_data.pop('qualifications', None)
        display_name_changed = ('display_name' in validated_data
                                and validated_data['display_name'] != instance.display_name)
        instance = super().update(instance, validated_data)
        if display_name_changed:
            _record_content_field(self, 'escort_profile', instance, 'PROVIDER_DISPLAY_NAME')
        if qualifications is not None: save_qualifications(instance,qualifications)
        # 迁移期同步新旧账户头像，避免未切换的订单展示链路丢失头像。
        if avatar_changed:
            avatar_url = build_media_url(self.context.get('request'), instance.avatar) if instance.avatar else ''
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
    phone = serializers.CharField(max_length=20, required=True, allow_blank=False)
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

    def validate_display_name(self, value):
        return _check_content_field(self, 'PROVIDER_DISPLAY_NAME', value,
                                    allow_reserved=_official_name_allowed(self))

    def validate(self, attrs):
        from wallet.club_rules import deposit_target
        attrs['deposit_required'] = deposit_target(attrs.get('deposit_mode', 'CASH'))
        return normalize_escort_skills(attrs)

    def validate_phone(self, value):
        value = value.strip()
        if not value.isascii() or not value.isdigit() or not 5 <= len(value) <= 20:
            raise serializers.ValidationError('请输入可接收短信的有效手机号')
        return value

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
class AdminOrderProviderSerializer(EntityNumberModelSerializer):
    provider = serializers.PrimaryKeyRelatedField(
        source='provider_account',
        read_only=True,
    )
    provider_name = serializers.SerializerMethodField()
    expected_income = serializers.SerializerMethodField()
    transferable_income = serializers.SerializerMethodField()

    def to_representation(self, obj):
        data = super().to_representation(obj)
        from orders.segment_financial_capture import transfer_income_pending
        if transfer_income_pending(obj.order, obj.provider_account_id):
            for field in ('provider_income', 'expected_income', 'transferable_income'):
                data[field] = None
        return data

    def get_expected_income(self, obj):
        from wallet.reporting import assignment_expected_income
        return assignment_expected_income(obj)

    def get_transferable_income(self, obj):
        if getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False):
            from orders.models import OrderPlatformSubsidyShare
            share = OrderPlatformSubsidyShare.objects.filter(assignment=obj).first()
            if share:
                from wallet.reporting import assignment_platform_subsidy
                amount = assignment_platform_subsidy(obj, order=obj.order)
                return None if amount is None else max(0, amount - share.retained_amount) if not obj.released_at else 0
        return self.get_expected_income(obj)
    commission_type_display = serializers.CharField(
        source='get_commission_type_display', read_only=True
    )

    class Meta:
        model = OrderProvider
        fields = [
            'id', 'provider', 'provider_name', 'provider_name_snapshot',
            'commission_type', 'commission_type_display',
            'commission_rate', 'commission_fixed',
            'settlement_base', 'provider_income', 'expected_income', 'transferable_income', 'settled_at', 'released_at', 'created_at',
        ]

    def get_provider_name(self, obj):
        if obj.provider_account_id:
            profile = getattr(obj.provider_account, 'escort_profile', None)
            return (profile.display_name if profile else '') or obj.provider_name_snapshot or obj.provider_account.nickname or obj.provider_account.username
        return obj.provider_name_snapshot or ''


class AdminOrderSerializer(EntityNumberModelSerializer):
    prestart_handoff_available = serializers.SerializerMethodField()
    prestart_handoffs = serializers.SerializerMethodField()
    prestart_legacy_minimum = serializers.SerializerMethodField()
    original_attribution = serializers.SerializerMethodField()
    service_item = serializers.PrimaryKeyRelatedField(source='service', read_only=True)
    source_order_no = serializers.CharField(source='source_order.order_no', read_only=True, default='')
    refund_day_amount = serializers.IntegerField(source='_refund_day_amount', read_only=True, default=None, allow_null=True)
    refund_day_count = serializers.IntegerField(source='_refund_day_count', read_only=True, default=None, allow_null=True)
    refund_day_latest = serializers.DateTimeField(source='_refund_day_latest', read_only=True, default=None, allow_null=True)
    settlement_status = serializers.SerializerMethodField()
    team_progress = serializers.SerializerMethodField()
    application_progress = serializers.SerializerMethodField()
    settlement_store_funding = serializers.SerializerMethodField()
    settlement_addon_contribution = serializers.SerializerMethodField()

    def get_settlement_store_funding(self, obj):
        return obj.settlement_store_funding if obj.settled_at else None

    def get_settlement_addon_contribution(self, obj):
        return obj.settlement_addon_contribution if obj.settled_at else None

    def to_representation(self, obj):
        data = super().to_representation(obj)
        from orders.segment_financial_capture import transfer_income_pending
        if transfer_income_pending(obj):
            for field in ('provider_income', 'inviter_commission', 'shop_income'):
                data[field] = None
        return data

    def get_prestart_handoff_available(self, obj):
        from django.conf import settings
        return bool(getattr(settings, 'PRESTART_HANDOFF_ENABLED', False) and obj.status == Order.Status.GRABBED
                    and obj.payment_status == Order.PaymentStatus.PAID and not obj.in_service_at and not obj.is_gift_snapshot)

    def get_prestart_legacy_minimum(self, obj):
        if (not self.get_prestart_handoff_available(obj) or
                getattr(self.context.get('view'), 'action', None) != 'retrieve'):
            return None
        from orders.prestart_handoff import legacy_minimum_status
        status = legacy_minimum_status(obj)
        if status.get('required'):
            from users.models import EscortLevel
            status['levels'] = list(EscortLevel.objects.filter(is_active=True).order_by('sort_order', 'pk')
                                    .values('id', 'name'))
        return status

    def get_original_attribution(self, obj):
        from django.conf import settings
        if not getattr(settings, 'ORIGINAL_ATTRIBUTION_ENABLED', False) or getattr(self.context.get('view'), 'action', None) != 'retrieve':
            return None
        from orders.models import OrderOriginalAttribution, OrderOriginalShareTransfer
        row = OrderOriginalAttribution.objects.filter(order=obj).first()
        if not row:
            return None
        from common.private_media import private_media_url
        from orders.segment_reports import supports_detail as segment_reports_support, report_data as segment_report_data
        request = self.context.get('request')
        from console.permissions import get_account_permissions, is_console_account
        account = getattr(request, 'account', None)
        can_read_reports = bool(account and is_console_account(account) and 'report:view' in get_account_permissions(account))
        segment_reporting = bool(can_read_reports and segment_reports_support(obj))
        segments = [{'id': segment.pk, 'provider': segment.provider_account_id, 'slot': segment.slot,
                     'original_share': segment.original_share, 'accepted_at': segment.accepted_at,
                     'started_at': segment.started_at, 'released_at': segment.released_at,
                     'entry_image_url': private_media_url(self.context.get('request'), segment.entry_image) if segment.entry_image else '',
                     'completion_image_url': private_media_url(self.context.get('request'), segment.completion_image) if segment.completion_image else '',
                     'segment_report': segment_report_data(segment, request, staff=True) if segment_reporting else None}
                    for segment in row.segments.order_by('pk')]
        events = [{'id': event.pk, 'old_provider': event.previous_segment.provider_account_id,
                   'new_provider': event.next_segment.provider_account_id, 'original_share_before': event.original_share_before,
                   'retained_share': event.retained_share, 'transferred_share': event.transferred_share,
                   'agreement': event.agreement, 'operator': event.operator_id,
                   'operator_name': event.operator.nickname or event.operator.username, 'created_at': event.created_at}
                  for event in OrderOriginalShareTransfer.objects.filter(previous_segment__attribution=row)
                  .select_related('previous_segment', 'next_segment', 'operator').order_by('pk')]
        result = {'status': row.status, 'review_reason': row.review_reason, 'original_amount': row.original_amount,
                  'rounding_surplus': row.rounding_surplus, 'segments': segments, 'transfers': events}
        if getattr(settings, 'SEGMENT_ECONOMICS_CAPTURE_ENABLED', False):
            from orders.models import OrderSegmentFinancialCoverage, OrderSegmentFinancialSnapshot, OrderSegmentTransferBasis
            coverage = OrderSegmentFinancialCoverage.objects.filter(attribution=row).first()
            if coverage:
                result['financial_capture'] = {'status': coverage.status, 'review_reason': coverage.review_reason,
                    'deal_amount': coverage.deal_amount, 'source': coverage.source,
                    'segments': [{'segment': snapshot.segment_id,
                        'provider': snapshot.segment.provider_account_id,
                        'incoming_basis': snapshot.incoming_basis, 'retained_basis': snapshot.retained_basis,
                        'commission_type': snapshot.commission_type, 'commission_rate': snapshot.commission_rate,
                        'commission_fixed': snapshot.commission_fixed, 'fixed_rule_total': snapshot.fixed_rule_total,
                        'commission_source': snapshot.commission_source, 'income_at_entry': snapshot.income_at_entry}
                        for snapshot in OrderSegmentFinancialSnapshot.objects.filter(segment__attribution=row)
                        .select_related('segment').order_by('segment__pk')],
                    'transfers': [{'id': allocation.pk,
                        'old_provider': allocation.transfer.previous_segment.provider_account_id,
                        'new_provider': allocation.transfer.next_segment.provider_account_id,
                        'new_segment': allocation.transfer.next_segment_id,
                        'basis_before': allocation.basis_before, 'retained_basis': allocation.retained_basis,
                        'transferred_basis': allocation.transferred_basis,
                        'fixed_fee_store_shortfall': allocation.fixed_fee_store_shortfall,
                        'remainder_numerator': allocation.remainder_numerator,
                        'remainder_denominator': allocation.transfer.original_share_before}
                        for allocation in OrderSegmentTransferBasis.objects.filter(transfer__previous_segment__attribution=row)
                        .select_related('transfer__previous_segment', 'transfer__next_segment').order_by('pk')]}
        return result

    def get_prestart_handoffs(self, obj):
        from django.conf import settings
        if not getattr(settings, 'PRESTART_HANDOFF_ENABLED', False) or getattr(self.context.get('view'), 'action', None) != 'retrieve':
            return []
        return [{'id': item.pk, 'old_provider':item.old_provider_id, 'new_provider':item.new_provider_id,
                 'old_snapshot':item.old_snapshot, 'new_snapshot':item.new_snapshot, 'reason':item.reason,
                 'operator':item.operator_id, 'operator_name':item.operator.nickname or item.operator.username,
                 'created_at':item.created_at} for item in obj.prestart_handoffs.select_related('operator').order_by('pk')]

    def get_application_progress(self, obj):
        if (obj.status != Order.Status.PENDING or obj.dispatch_route != Order.DispatchRoute.PUBLIC
                or not obj.requires_boss_selection or not hasattr(obj, '_pending_applications')):
            return None
        return {'total': obj._pending_applications, 'waiting_partner': obj._waiting_partner_applications}

    def get_team_progress(self, obj):
        from orders.team_progress import team_progress
        return team_progress(obj)
    settlement_review_reason = serializers.SerializerMethodField()

    def get_settlement_review_reason(self, obj):
        from wallet.settlement_display import legacy_customer_settlement_reason
        return legacy_customer_settlement_reason(obj)

    def get_settlement_status(self, obj):
        from wallet.settlement_display import order_settlement_status
        return order_settlement_status(obj)
    can_aftersale = serializers.SerializerMethodField()
    service_action_block_reason = serializers.SerializerMethodField()
    refund_summary = serializers.SerializerMethodField()
    def get_service_action_block_reason(self, obj):
        from orders.refunds import negotiated_aftersale_blocks_completion
        return ('售后处理中，请先处理售后再结单或转单' if obj.status == Order.Status.IN_SERVICE
                and negotiated_aftersale_blocks_completion(obj) else '')
    def get_can_aftersale(self,obj):
        from orders.refunds import can_request_aftersale
        return can_request_aftersale(obj)
    def get_refund_summary(self,obj):
        from orders.refunds import refund_summary, authorized_refund
        from .permissions import get_account_permissions
        account = getattr(self.context.get('request'),'account',None)
        if not hasattr(self, '_refund_visibility'):
            self._refund_visibility = (authorized_refund(account), 'order:view' in get_account_permissions(account))
        detailed, include_coupon_results = self._refund_visibility
        return refund_summary(obj,detailed=detailed,account=account,include_coupon_results=include_coupon_results)
    required_team_size = serializers.SerializerMethodField()

    def get_required_team_size(self, obj):
        from orders.team_rules import required_team_size
        return required_team_size(obj)
    provider_requirement=serializers.SerializerMethodField()
    def get_provider_requirement(self,obj):
        from users.qualifications import public_requirement
        return public_requirement(obj)
    aftersale_deadline = serializers.SerializerMethodField()
    aftersale_status = serializers.SerializerMethodField()
    aftersale_open_at = serializers.SerializerMethodField()
    def get_aftersale_deadline(self, obj):
        from engagement.confirmation import aftersale_deadline
        return aftersale_deadline(obj)
    def get_aftersale_status(self, obj):
        return next((row.status for row in self._aftersale_rows(obj)), '')

    def _aftersale_rows(self, obj):
        if not hasattr(obj, '_admin_aftersale_rows'):
            obj._admin_aftersale_rows = list(obj.aftersales.all())
        return obj._admin_aftersale_rows

    def get_aftersale_open_at(self, obj):
        opened = next((row.created_at for row in self._aftersale_rows(obj) if row.status == 'OPEN'), None)
        return serializers.DateTimeField().to_representation(opened) if opened else None

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
    customer_username = serializers.SerializerMethodField()
    customer_entity_no = serializers.SerializerMethodField()
    customer_nickname = serializers.SerializerMethodField()
    customer_phone = serializers.SerializerMethodField()
    provider_name = serializers.SerializerMethodField()
    inviter_name = serializers.SerializerMethodField()
    promotion_title = serializers.CharField(source='promotion.title', read_only=True)
    providers = AdminOrderProviderSerializer(many=True, read_only=True)
    staff_dispatch_actionable = serializers.SerializerMethodField()
    dispatch_assignment_allowed = serializers.SerializerMethodField()
    dispatch_message_allowed = serializers.SerializerMethodField()
    dispatch_message_unavailable_reason = serializers.SerializerMethodField()
    pending_deadline_expired = serializers.SerializerMethodField()

    def get_pending_deadline_expired(self, obj):
        return bool(obj.status == Order.Status.PENDING and obj.payment_status == Order.PaymentStatus.PAID
                    and obj.auto_cancel_at and obj.auto_cancel_at <= timezone.now())

    def _dispatch_message_error(self, obj):
        request = self.context.get('request')
        if not request or not getattr(request, 'account', None):
            return '订单状态待核对'
        cache = self.context.setdefault('_dispatch_message_eligibility', {})
        if obj.pk not in cache:
            from orders.dispatching import eligibility_error
            cache[obj.pk] = eligibility_error(obj)
        return cache[obj.pk]

    def get_dispatch_message_allowed(self, obj):
        return not self._dispatch_message_error(obj)

    def get_dispatch_message_unavailable_reason(self, obj):
        return self._dispatch_message_error(obj)

    def get_dispatch_assignment_allowed(self, obj):
        from orders.staff_dispatch_readiness import dispatch_assignment_allowed
        # Without an authenticated console request this is only an unscoped
        # serialization, not a grant to show or offer an assignment action.
        request = self.context.get('request')
        if not request or not getattr(request, 'account', None):
            return False
        cache = self.context.setdefault('_dispatch_assignment_readiness', {})
        if obj.pk not in cache:
            cache[obj.pk] = dispatch_assignment_allowed(obj)
        return cache[obj.pk]

    def get_staff_dispatch_actionable(self, obj):
        return (obj.dispatch_route == Order.DispatchRoute.STAFF
                and self.get_dispatch_assignment_allowed(obj))

    class Meta:
        model = Order
        list_serializer_class = OrderSettlementListSerializer
        fields = [
            'provider_requirement',
            'id', 'order_no', 'service_item', 'status', 'status_display', 'payment_status', 'settled_at', 'settlement_status', 'settlement_review_reason', 'settlement_mode', 'dispatch_route', 'requires_boss_selection',
            'payment_status_display', 'escort_mode', 'escort_mode_display', 'provider_gender',
            'required_provider_count', 'required_team_size',
            'amount', 'refund_day_amount', 'refund_day_count', 'refund_day_latest', 'game_rounds', 'service_name_snapshot', 'gameplay_snapshot', 'roulette_result', 'is_gift_snapshot', 'source_order', 'source_order_no',
            'support_contact', 'support_contact_name_snapshot', 'responsible_staff', 'funding_confirmation',
            'unit_price_snapshot', 'customer', 'customer_name', 'customer_username', 'customer_entity_no',
            'customer_nickname', 'customer_phone', 'customer_phone_snapshot',
            'provider', 'provider_name', 'commission_type', 'commission_fixed', 'commission_override', 'commission_source', 'commission_rate', 'commission_allocation_pending', 'provider_income',
            'providers', 'team_progress', 'application_progress', 'prestart_handoff_available', 'prestart_handoffs', 'prestart_legacy_minimum', 'original_attribution',
            'inviter', 'inviter_name', 'inviter_commission', 'shop_income',
            'settlement_store_funding', 'settlement_addon_contribution',
            'original_amount', 'boss_discount', 'promo_discount', 'coupon_discount',
            'promotion', 'promotion_title',
            'remark', 'cancel_reason', 'reject_count', 'auto_cancel_at', 'pending_timeout_minutes', 'pending_deadline_expired', 'staff_dispatch_actionable', 'dispatch_assignment_allowed', 'dispatch_message_allowed', 'dispatch_message_unavailable_reason',
            'grabbed_at', 'in_service_at', 'completed_at', 'cancelled_at', 'refunded_at',
            'business_cycle', 'experience_kind', 'report_ready_at', 'consumption_ready_at', 'consumption_confirmed_at', 'aftersale_deadline', 'aftersale_status', 'aftersale_open_at', 'can_aftersale', 'service_action_block_reason', 'refund_summary',
            'created_at', 'updated_at',
        ]

    def get_customer_name(self, obj):
        customer = obj.customer_account if obj.customer_account_id else obj.customer
        return (customer.nickname or customer.username) if customer else ''

    def _customer_field(self, obj, field):
        customer = obj.customer_account if obj.customer_account_id else obj.customer
        return (getattr(customer, field, '') or '') if customer else ''

    def get_customer_username(self, obj):
        return self._customer_field(obj, 'username')

    def get_customer_entity_no(self, obj):
        from identifiers.services import get_entity_number
        customer = obj.customer_account if obj.customer_account_id else obj.customer
        return get_entity_number(customer) if customer else ''

    def get_customer_nickname(self, obj):
        return self._customer_field(obj, 'nickname')

    def get_customer_phone(self, obj):
        return self._customer_field(obj, 'phone')

    def get_provider_name(self, obj):
        if not obj.provider_account_id:
            return obj.provider_name_snapshot or ''
        profile = getattr(obj.provider_account, 'escort_profile', None)
        return (profile.display_name if profile else '') or obj.provider_name_snapshot or obj.provider_account.nickname or obj.provider_account.username

    def get_inviter_name(self, obj):
        if not obj.inviter_account_id:
            return ''
        return obj.inviter_account.nickname or obj.inviter_account.username


def _order_provider_identity(obj):
    identity = obj.provider_account if obj.provider_account_id else obj.provider if obj.provider_id else None
    profile = getattr(identity, 'escort_profile', None) if identity else None
    return {
        'provider_no': (profile.escort_no or '') if profile else '',
        'provider_username': (identity.username or '') if identity else '',
        'provider_identity_source': 'ACCOUNT' if obj.provider_account_id else 'LEGACY' if obj.provider_id else '',
    }


class AdminOrderProviderIdentitySerializer(AdminOrderProviderSerializer):
    provider_no = serializers.SerializerMethodField()
    provider_username = serializers.SerializerMethodField()
    provider_identity_source = serializers.SerializerMethodField()

    class Meta(AdminOrderProviderSerializer.Meta):
        fields = [*AdminOrderProviderSerializer.Meta.fields, 'provider_no', 'provider_username', 'provider_identity_source']

    def get_provider_no(self, obj):
        return _order_provider_identity(obj)['provider_no']

    def get_provider_username(self, obj):
        return _order_provider_identity(obj)['provider_username']

    def get_provider_identity_source(self, obj):
        return _order_provider_identity(obj)['provider_identity_source']


class AdminOrderWithIdentitySerializer(AdminOrderSerializer):
    providers = AdminOrderProviderIdentitySerializer(many=True, read_only=True)
    provider_no = serializers.SerializerMethodField()
    provider_username = serializers.SerializerMethodField()
    provider_identity_source = serializers.SerializerMethodField()

    class Meta(AdminOrderSerializer.Meta):
        fields = [*AdminOrderSerializer.Meta.fields, 'provider_no', 'provider_username', 'provider_identity_source']

    def get_provider_no(self, obj):
        return _order_provider_identity(obj)['provider_no']

    def get_provider_username(self, obj):
        return _order_provider_identity(obj)['provider_username']

    def get_provider_identity_source(self, obj):
        return _order_provider_identity(obj)['provider_identity_source']


class AdminOrderDetailSerializer(AdminOrderWithIdentitySerializer):
    reservation_origin = serializers.SerializerMethodField()
    negotiation_continuation_supported = serializers.SerializerMethodField()

    def get_negotiation_continuation_supported(self, obj):
        # Capability, not an authorization or eligibility decision: the
        # mutation rechecks the receipt and currently active participants.
        return True

    def get_reservation_origin(self, obj):
        from django.core.exceptions import ObjectDoesNotExist
        from orders.serializers import reservation_origin
        try:
            return reservation_origin(obj.reservation)
        except ObjectDoesNotExist:
            return None

    class Meta(AdminOrderWithIdentitySerializer.Meta):
        fields = [*AdminOrderWithIdentitySerializer.Meta.fields, 'game_region', 'game_nickname', 'game_uid',
                  'reservation_origin', 'negotiation_continuation_supported']


class AdminOrderLogSerializer(EntityNumberModelSerializer):
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


class AdminEvaluationSerializer(EntityNumberModelSerializer):
    customer_name = serializers.SerializerMethodField()
    customer_username = serializers.SerializerMethodField()
    customer_nickname = serializers.SerializerMethodField()
    provider_name = serializers.SerializerMethodField()
    provider_username = serializers.SerializerMethodField()
    provider_nickname = serializers.SerializerMethodField()
    service_name = serializers.SerializerMethodField()
    order_no = serializers.CharField(source='order.order_no', read_only=True)
    avg_score = serializers.FloatField(read_only=True)
    void_supported = serializers.SerializerMethodField()

    def get_void_supported(self, obj):
        return True

    class Meta:
        model = Evaluation
        fields = ['id', 'order', 'order_no', 'customer', 'customer_name', 'customer_username',
                  'customer_nickname', 'provider', 'provider_name', 'provider_username',
                  'provider_nickname', 'service_name', 'score',
                  'skill_score', 'attitude_score', 'communication_score', 'avg_score',
                  'content', 'is_anonymous', 'low_rating_aftersale_state', 'low_rating_aftersale',
                  'voided_at', 'void_reason', 'voided_by', 'void_supported',
                  'reply_content', 'reply_author', 'replied_at', 'reply_version', 'created_at']

    def get_customer_name(self, obj):
        anon = '（匿名）' if obj.is_anonymous else ''
        customer = obj.customer_account if obj.customer_account_id else obj.customer
        return f"{customer.nickname or customer.username}{anon}"

    def get_customer_username(self, obj):
        customer = obj.customer_account if obj.customer_account_id else obj.customer
        return customer.username

    def get_customer_nickname(self, obj):
        customer = obj.customer_account if obj.customer_account_id else obj.customer
        return customer.nickname

    def get_provider_name(self, obj):
        from orders.evaluation_targets import evaluation_provider_name
        return evaluation_provider_name(obj)

    def get_provider_username(self, obj):
        provider = obj.provider_account if obj.provider_account_id else obj.provider
        return provider.username if provider else ''

    def get_provider_nickname(self, obj):
        provider = obj.provider_account if obj.provider_account_id else obj.provider
        return provider.nickname if provider else ''

    def get_service_name(self, obj):
        return obj.order.service_name_snapshot


# ---------------- 钱包/流水 ----------------
class AdminWalletSerializer(EntityNumberModelSerializer):
    user_role = serializers.SerializerMethodField()
    username = serializers.SerializerMethodField()
    nickname = serializers.SerializerMethodField()
    phone = serializers.SerializerMethodField()
    boss_no = serializers.SerializerMethodField()
    account_id = serializers.IntegerField(read_only=True)
    avatar_url = serializers.SerializerMethodField()
    boss_type_name = serializers.SerializerMethodField()

    class Meta:
        model = Wallet
        fields = ['id', 'user', 'user_role', 'account_id', 'username', 'nickname', 'phone', 'boss_no', 'avatar_url',
                  'boss_type_name', 'balance',
                  'frozen_amount', 'total_recharge', 'total_gift', 'is_active', 'updated_at']
        read_only_fields = ['user', 'balance', 'frozen_amount', 'total_recharge', 'total_gift']

    def _owner(self, obj):
        return obj.account if obj.account_id else obj.user

    def get_user_role(self, obj):
        if obj.account_id:
            return {'BOSS': 'CUSTOMER', 'PROVIDER': 'PROVIDER'}.get(obj.account.account_type, obj.account.account_type)
        return obj.user.role

    def get_username(self, obj):
        return self._owner(obj).username

    def get_nickname(self, obj):
        return self._owner(obj).nickname

    def get_phone(self, obj):
        return self._owner(obj).phone

    def get_avatar_url(self, obj):
        return self._owner(obj).avatar_url

    def get_boss_type_name(self, obj):
        owner = self._owner(obj)
        return owner.boss_type.name if owner.boss_type_id else None

    def get_boss_no(self, obj):
        return obj.account.account_no if obj.account_id else obj.user.boss_no


class AdminTransactionSerializer(EntityNumberModelSerializer):
    is_reversal = serializers.SerializerMethodField()
    def get_is_reversal(self,obj):
        return bool(obj.reversal_of_id)
    tx_type_display = serializers.CharField(source='get_tx_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    username = serializers.SerializerMethodField()
    operator_name = serializers.SerializerMethodField()
    order_no = serializers.SerializerMethodField()
    related_order_id = serializers.SerializerMethodField()
    addon = serializers.SerializerMethodField()

    class Meta:
        model = Transaction
        fields = ['id', 'tx_no', 'wallet', 'username', 'order', 'order_no', 'related_order_id', 'addon', 'amount', 'tx_type',
                  'tx_type_display', 'balance_before', 'balance_after', 'status',
                  'status_display', 'remark', 'operator', 'operator_name', 'created_at','reversal_of','is_reversal']

    def get_order_no(self, obj):
        if obj.order_id:
            return obj.order.order_no
        return self.context.get('addon_links', {}).get(obj.pk, {}).get('order_no')

    def get_related_order_id(self, obj):
        return obj.order_id or self.context.get('addon_links', {}).get(obj.pk, {}).get('order_id')

    def get_addon(self, obj):
        return self.context.get('addon_links', {}).get(obj.pk)

    def get_username(self, obj):
        if not obj.wallet_id:
            return ''
        owner = obj.wallet.account if obj.wallet.account_id else obj.wallet.user
        return owner.username

    def get_operator_name(self, obj):
        operator = obj.operator_account if obj.operator_account_id else obj.operator
        return (operator.nickname or operator.username) if operator else ''


class AdminRechargeRecordSerializer(EntityNumberModelSerializer):
    user_role = serializers.SerializerMethodField()
    username = serializers.SerializerMethodField()
    nickname = serializers.SerializerMethodField()
    phone = serializers.SerializerMethodField()
    boss_no = serializers.SerializerMethodField()
    avatar_url = serializers.SerializerMethodField()
    boss_type_name = serializers.SerializerMethodField()
    operator_name = serializers.SerializerMethodField()
    proof_image_url = serializers.SerializerMethodField()
    total_amount = serializers.IntegerField(read_only=True)
    gift_approval = serializers.SerializerMethodField()

    class Meta:
        model = RechargeRecord
        fields = ['id', 'user', 'user_role', 'username', 'nickname', 'phone', 'boss_no', 'avatar_url',
                  'boss_type_name', 'amount', 'gift_amount', 'bonus_snapshot', 'gift_approval',
                  'total_amount', 'trade_no', 'proof_image_url', 'remark',
                  'operator', 'operator_name', 'created_at']

    def _owner(self, obj):
        return obj.account if obj.account_id else obj.user

    def get_gift_approval(self, obj):
        from wallet.models import RechargeGiftApproval
        try:
            approval = obj.gift_approval
        except RechargeGiftApproval.DoesNotExist:
            return None
        return {'status': approval.status, 'standard_amount': approval.standard_amount,
                'requested_amount': approval.requested_amount, 'decision_reason': approval.decision_reason,
                'decided_by': approval.decided_by_id, 'decided_at': approval.decided_at}

    def get_user_role(self, obj):
        if obj.account_id:
            return {'BOSS': 'CUSTOMER', 'PROVIDER': 'PROVIDER'}.get(obj.account.account_type, obj.account.account_type)
        return obj.user.role

    def get_username(self, obj):
        return self._owner(obj).username

    def get_nickname(self, obj):
        return self._owner(obj).nickname

    def get_phone(self, obj):
        return self._owner(obj).phone

    def get_avatar_url(self, obj):
        return self._owner(obj).avatar_url

    def get_boss_type_name(self, obj):
        owner = self._owner(obj)
        return owner.boss_type.name if owner.boss_type_id else None

    def get_boss_no(self, obj):
        return obj.account.account_no if obj.account_id else obj.user.boss_no

    def get_operator_name(self, obj):
        operator = obj.operator_account if obj.operator_account_id else obj.operator
        return (operator.nickname or operator.username) if operator else ''

    def get_proof_image_url(self, obj):
        return build_media_url(self.context.get('request'), obj.proof_image)


class AdminDisposeRecordSerializer(EntityNumberModelSerializer):
    username = serializers.SerializerMethodField()
    nickname = serializers.SerializerMethodField()
    dispose_type_display = serializers.CharField(source='get_dispose_type_display', read_only=True)
    operator_name = serializers.SerializerMethodField()

    class Meta:
        model = DisposeRecord
        fields = ['id', 'user', 'username', 'nickname', 'dispose_type', 'dispose_type_display',
                  'amount', 'reason', 'operator', 'operator_name', 'created_at']

    def get_username(self, obj):
        return (obj.account if obj.account_id else obj.user).username

    def get_nickname(self, obj):
        return (obj.account if obj.account_id else obj.user).nickname

    def get_operator_name(self, obj):
        operator = obj.operator_account if obj.operator_account_id else obj.operator
        return (operator.nickname or operator.username) if operator else ''


# ---------------- 陪玩报单 ----------------
def report_order_approval_block_reason(obj, *, formal_fields=None):
    """Explain whether the order and its verified report can be approved."""
    if obj.status != ProviderReport.Status.PENDING or not obj.order_id:
        return ''
    order = obj.order
    from orders.points_service_payment import display_has_points
    if display_has_points(order):
        if formal_fields is not None:
            if formal_fields.approval_block_reason:
                return formal_fields.approval_block_reason
        else:
            from orders.points_checkout_test import report_enabled
            if not report_enabled():
                return '积分订单报单及收益结算尚未接通'
            from wallet.reporting import points_report_snapshot
            if not points_report_snapshot(obj):
                return '积分订单报单或收益快照待核对'
    from orders.segment_financial_capture import transfer_income_pending
    if transfer_income_pending(order, obj.provider_account_id):
        return '转单收益尚未完成逐段核对，暂不能审核'
    if order.payment_status != Order.PaymentStatus.PAID:
        from orders.negotiation_settlement import report_allowed
        from orders.settlement import SettlementError
        try:
            if report_allowed(order, obj):
                return '' if order.status == Order.Status.COMPLETED else '订单未完成，不能通过报单'
        except SettlementError:
            return '协商退款凭证待核对，不能通过报单'
        if order.payment_status == Order.PaymentStatus.REFUNDED:
            return '订单已退款，不能通过报单'
        if order.payment_status == Order.PaymentStatus.PARTIALLY_REFUNDED:
            return '订单已部分退款，不能通过报单'
        return '订单未支付，不能通过报单'
    return '' if order.status == Order.Status.COMPLETED else '订单未完成，不能通过报单'


def pending_report_order_block_q(base_queryset=None, *, verify_formal_points=True):
    """Classify order state, verified negotiations, and transfer review."""
    from django.db.models import Exists, OuterRef, Q
    from orders.models import OrderRefund
    blocked = (Q(status=ProviderReport.Status.PENDING, order__isnull=False)
               & (~Q(order__status=Order.Status.COMPLETED) | ~Q(order__payment_status=Order.PaymentStatus.PAID)))
    queryset = base_queryset if base_queryset is not None else ProviderReport.objects.all()
    marked = OrderRefund.objects.filter(order_id=OuterRef('order_id'),
        benefit_effects__negotiation_execution_version=1)
    candidates = queryset.filter(status=ProviderReport.Status.PENDING, order__status=Order.Status.COMPLETED,
        order__payment_status__in=[Order.PaymentStatus.REFUNDED, Order.PaymentStatus.PARTIALLY_REFUNDED]
    ).annotate(_negotiation_marker=Exists(marked)).filter(_negotiation_marker=True).select_related('order')
    allowed = [row.pk for row in candidates.iterator(chunk_size=100) if not report_order_approval_block_reason(row)]
    # Paid orders with transfer evidence can also be blocked for a specific
    # participant. Only inspect the extra tables when their protection is on,
    # preserving compatibility with stores not yet migrated to these features.
    from orders.segment_financial_capture import enforcement_enabled
    from orders.segment_settlement import protection_enabled
    possible = Q(pk__in=[])
    paid = queryset.filter(status=ProviderReport.Status.PENDING, order__status=Order.Status.COMPLETED,
                           order__payment_status=Order.PaymentStatus.PAID)
    if protection_enabled():
        from orders.models import OrderSegmentSettlementManifest
        manifests = OrderSegmentSettlementManifest.objects.filter(order_id=OuterRef('order_id'))
        paid = paid.annotate(_report_manifest=Exists(manifests))
        possible |= Q(_report_manifest=True)
    if enforcement_enabled():
        from orders.models import OrderOriginalShareTransfer
        transfers = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order_id=OuterRef('order_id'),
            previous_segment__attribution__financial_coverage__isnull=False)
        paid = paid.annotate(_report_transfer=Exists(transfers))
        possible |= Q(_report_transfer=True, order__settled_at__isnull=True)
    transfer_candidates = paid.filter(possible).select_related('order')
    transfer_blocked = [row.pk for row in transfer_candidates.iterator(chunk_size=100)
                        if report_order_approval_block_reason(row)]
    # Keep all independent payment evidence in the SQL predicate, including a
    # receipt whose marker or ledger has been damaged. Older stores may not yet
    # have the receipt table, so inspect the tenant schema before referring to it.
    from engagement.models import PointLedger
    from orders.models import OrderPointsRedemption
    from orders.points_service_payment import MARKER, points_receipt_table_exists
    from django.db import DatabaseError
    ledger = PointLedger.objects.filter(order_id=OuterRef('order_id'), kind='ORDER_POINTS_REDEEM')
    points = queryset.filter(status=ProviderReport.Status.PENDING, order__isnull=False).annotate(
        _points_ledger=Exists(ledger))
    points_evidence = Q(order__funding_terms__has_key=MARKER) | Q(_points_ledger=True)
    try:
        receipt_exists = points_receipt_table_exists()
    except DatabaseError:
        return Q(status=ProviderReport.Status.PENDING, order__isnull=False)
    if receipt_exists:
        receipts = OrderPointsRedemption.objects.filter(order_id=OuterRef('order_id'))
        points = points.annotate(_points_receipt=Exists(receipts))
        points_evidence |= Q(_points_receipt=True)
    from orders.points_checkout_test import report_enabled
    point_candidates = points.filter(points_evidence)
    if report_enabled():
        point_blocked = [row.pk for row in point_candidates.select_related('order').iterator(chunk_size=100)
                         if report_order_approval_block_reason(row)]
        points_q = Q(pk__in=point_blocked)
    else:
        if verify_formal_points and getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True:
            from wallet.reporting import formal_points_report_fields
            complete_paid = point_candidates.filter(order__status=Order.Status.COMPLETED,
                order__payment_status=Order.PaymentStatus.PAID).select_related('order')
            verified_ids = []
            for row in complete_paid.iterator(chunk_size=100):
                fields = formal_points_report_fields(row, check_approval=True)
                if fields is not None and not report_order_approval_block_reason(row, formal_fields=fields):
                    verified_ids.append(row.pk)
            point_candidates = point_candidates.exclude(pk__in=verified_ids)
        points_q = Q(pk__in=point_candidates.values('pk'))
    return (blocked & ~Q(pk__in=allowed)) | Q(pk__in=transfer_blocked) | points_q


class AdminProviderReportSerializer(PointsReportReadMixin, EntityNumberModelSerializer):
    check_formal_approval = True
    amount = serializers.SerializerMethodField()
    commission_rate = serializers.SerializerMethodField()
    payout_amount = serializers.SerializerMethodField()
    order_approval_block_reason = serializers.SerializerMethodField()
    settlement_review_reason = serializers.SerializerMethodField()
    aftersale_status = serializers.SerializerMethodField()
    negotiated_settlement = serializers.SerializerMethodField()

    def get_negotiated_settlement(self, obj):
        from orders.negotiation_settlement import report_income
        return report_income(obj, display_only=True)[0]

    def get_amount(self, obj):
        from orders.segment_financial_capture import transfer_income_pending
        from orders.points_service_payment import display_has_points
        if obj.order_id and display_has_points(obj.order):
            return obj.amount if self._points_snapshot(obj) else None
        return None if obj.order_id and transfer_income_pending(obj.order, obj.provider_account_id) else obj.amount

    def get_commission_rate(self, obj):
        from orders.segment_financial_capture import transfer_income_pending
        from orders.points_service_payment import display_has_points
        if obj.order_id and display_has_points(obj.order):
            return obj.commission_rate if self._points_snapshot(obj) else None
        return None if obj.order_id and transfer_income_pending(obj.order, obj.provider_account_id) else obj.commission_rate

    def get_aftersale_status(self, obj):
        return next((row.status for row in obj.order.aftersales.all()), '') if obj.order_id else ''

    def get_settlement_review_reason(self, obj):
        from wallet.settlement_display import legacy_customer_settlement_reason
        return legacy_customer_settlement_reason(obj.order) if obj.order_id else ''

    def get_order_approval_block_reason(self, obj):
        return report_order_approval_block_reason(obj, formal_fields=self._formal_points_fields)

    def get_payout_amount(self, obj):
        from wallet.reporting import report_has_valid_order_share
        from orders.segment_financial_capture import transfer_income_pending
        from orders.points_service_payment import display_has_points
        if obj.order_id and display_has_points(obj.order):
            return obj.payout_amount if obj.status == ProviderReport.Status.APPROVED and self._points_snapshot(obj) else None
        if obj.order_id and transfer_income_pending(obj.order, obj.provider_account_id):
            return None
        from orders.negotiation_settlement import report_income
        negotiated, income = report_income(obj, display_only=True)
        if negotiated:
            return income if obj.status == ProviderReport.Status.APPROVED else None
        return obj.payout_amount if report_has_valid_order_share(obj) else None
    settlement_status = serializers.SerializerMethodField()

    def get_settlement_status(self, obj):
        from wallet.settlement_display import report_settlement_status
        return report_settlement_status(obj)
    submitted_at = serializers.DateTimeField(read_only=True)
    fulfillment = serializers.CharField(source='order.fulfillment', read_only=True, default='')
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    provider_name = serializers.SerializerMethodField()
    provider_username = serializers.SerializerMethodField()
    provider_nickname = serializers.SerializerMethodField()
    provider_no = serializers.SerializerMethodField()
    auditor_name = serializers.SerializerMethodField()
    proof_image_url = serializers.SerializerMethodField()
    suggested_commission_rate = serializers.SerializerMethodField()
    commission_source_label = serializers.SerializerMethodField()
    order_no = serializers.CharField(source='order.order_no', read_only=True, default='')
    order_entity_no = serializers.SerializerMethodField()

    def get_order_entity_no(self, obj):
        if not obj.order_id:
            return ''
        from identifiers.services import get_entity_number
        return get_entity_number(obj.order)

    order_payment_status = serializers.CharField(source='order.payment_status', read_only=True, default='')
    entry_image_url = serializers.SerializerMethodField()
    completion_image_url = serializers.SerializerMethodField()
    result_image_urls = serializers.SerializerMethodField()
    settled_at = serializers.SerializerMethodField()

    def get_settled_at(self, obj):
        if not obj.order_id:
            return None
        from orders.points_service_payment import display_has_points
        from orders.points_checkout_test import ordinary_settlement_order
        if display_has_points(obj.order) or ordinary_settlement_order(obj.order):
            from orders.points_settlement_test import verified_person_settlement_display
            return verified_person_settlement_display(obj)[1]
        from orders.negotiation_settlement import report_settlement_snapshot
        negotiated = report_settlement_snapshot(obj)
        return negotiated[1] if negotiated is not None else obj.order.settled_at
    settlement_mode = serializers.CharField(source='order.settlement_mode', read_only=True, default='COMPLETION')
    expected_income = serializers.SerializerMethodField()
    platform_subsidy_income = serializers.SerializerMethodField()
    platform_funding_insufficient = serializers.SerializerMethodField()
    commission_type = serializers.SerializerMethodField()
    commission_fixed = serializers.SerializerMethodField()

    class Meta:
        model = ProviderReport
        list_serializer_class = ReportSettlementListSerializer
        fields = [
            'id', 'provider', 'provider_name', 'provider_username', 'provider_nickname', 'provider_no',
            'game_name', 'description', 'amount', 'order', 'order_no', 'order_entity_no', 'order_payment_status', 'fulfillment',
            'proof_image_url', 'entry_image_url', 'completion_image_url', 'result_image_urls',
            'status', 'status_display', 'commission_rate',
            'suggested_commission_rate', 'commission_source_label', 'commission_type', 'commission_fixed', 'payout_amount',
            'remark', 'audit_remark', 'auditor', 'auditor_name', 'transaction',
            'created_at', 'submitted_at', 'audited_at',
            'settled_at', 'settlement_status', 'negotiated_settlement', 'settlement_review_reason', 'settlement_mode', 'expected_income', 'platform_subsidy_income', 'platform_funding_insufficient', 'order_approval_block_reason', 'aftersale_status',
        ]

    def get_provider_name(self, obj):
        identity = obj.provider_account if obj.provider_account_id else obj.provider
        profile = getattr(identity, 'escort_profile', None) if identity else None
        return (profile.display_name if profile else '') or ((identity.nickname or identity.username) if identity else '')

    def get_provider_username(self, obj):
        identity = obj.provider_account if obj.provider_account_id else obj.provider
        return identity.username if identity else ''

    def get_provider_nickname(self, obj):
        identity = obj.provider_account if obj.provider_account_id else obj.provider
        return (identity.nickname or '') if identity else ''

    def get_provider_no(self, obj):
        identity = obj.provider_account if obj.provider_account_id else obj.provider
        profile = getattr(identity, 'escort_profile', None) if identity else None
        return profile.escort_no if profile else ''

    def get_expected_income(self, obj):
        return self._financial_snapshot(obj)['expected_income']

    def get_platform_subsidy_income(self, obj):
        return self._financial_snapshot(obj)['platform_subsidy_income']

    def get_commission_type(self, obj):
        return self._financial_snapshot(obj)['commission_type']

    def get_commission_fixed(self, obj):
        return self._financial_snapshot(obj)['commission_fixed']

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
        return self._financial_snapshot(obj)['suggested_commission_rate']

    def get_commission_source_label(self, obj):
        return self._financial_snapshot(obj)['commission_source_label']


# ---------------- 提现申请 ----------------
class AdminWithdrawSerializer(EntityNumberModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    payee_method_display = serializers.CharField(source='get_payee_method_display', read_only=True)
    user_name = serializers.SerializerMethodField()
    provider_username = serializers.SerializerMethodField()
    provider_nickname = serializers.SerializerMethodField()
    auditor_name = serializers.SerializerMethodField()
    claimed_by_name = serializers.SerializerMethodField()
    claim_is_mine = serializers.SerializerMethodField()

    class Meta:
        model = WithdrawRequest
        fields = [
            'id', 'user', 'user_name', 'provider_username', 'provider_nickname',
            'amount', 'tax_rate', 'tax_amount', 'actual_amount',
            'payee_method', 'payee_method_display',
            'payee_account', 'payee_name', 'status', 'status_display', 'remark',
            'audit_remark', 'payout_reference', 'paid_at',
            'auditor', 'auditor_name', 'claimed_by_account', 'claimed_by_name', 'claimed_at', 'claim_is_mine',
            'transaction', 'created_at', 'audited_at', 'workflow_version',
        ]

    def get_user_name(self, obj):
        user = obj.account if obj.account_id else obj.user
        return (user.nickname or user.username) if user else ''

    def get_provider_username(self, obj):
        user = obj.account if obj.account_id else obj.user
        return user.username if user else ''

    def get_provider_nickname(self, obj):
        user = obj.account if obj.account_id else obj.user
        return user.nickname if user else ''

    def get_auditor_name(self, obj):
        auditor = obj.auditor_account if obj.auditor_account_id else obj.auditor
        return (auditor.nickname or auditor.username) if auditor else ''

    def get_claimed_by_name(self, obj):
        account = obj.claimed_by_account if obj.claimed_by_account_id else None
        return (account.nickname or account.username) if account else ''

    def get_claim_is_mine(self, obj):
        account = getattr(self.context.get('request'), 'account', None)
        return bool(account and obj.claimed_by_account_id == account.pk)


# ---------------- 在线客服 ----------------
class AdminChatReadSerializer(serializers.Serializer):
    through_id = serializers.IntegerField(min_value=0, required=False)
    message_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=200, required=False)

    def validate(self, attrs):
        if 'through_id' in attrs and 'message_ids' in attrs:
            raise serializers.ValidationError('请选择一种已读范围')
        if 'through_id' not in attrs and 'message_ids' not in attrs:
            raise serializers.ValidationError('请选择已查看的消息范围')
        return attrs


class AdminChatReplySerializer(serializers.Serializer):
    content = serializers.CharField(max_length=1000, required=False, allow_blank=True, default='')
    image = serializers.ImageField(required=False)
    read_through_id = serializers.IntegerField(min_value=0, required=False)
    client_message_id = serializers.RegexField(regex=r'^[a-zA-Z0-9_-]{8,64}$', required=False)

    def validate_image(self, value):
        from chat.images import chat_image_error
        error = chat_image_error(value)
        if error:
            raise serializers.ValidationError(error)
        return value

    def validate(self, attrs):
        if not attrs.get('content') and not attrs.get('image'):
            raise serializers.ValidationError('回复内容不能为空')
        return attrs


class AdminChatMessageSerializer(EntityNumberModelSerializer):
    content_type_display = serializers.CharField(source='get_content_type_display', read_only=True)
    image_url = serializers.SerializerMethodField()
    order_card = serializers.SerializerMethodField()
    sender_name = serializers.SerializerMethodField()

    class Meta:
        model = ChatMessage
        fields = [
            'id', 'session', 'sender', 'sender_account', 'sender_name', 'is_from_support', 'content_type',
            'content_type_display', 'content', 'image_url', 'is_read', 'created_at', 'client_message_id', 'order_card',
        ]

    def get_image_url(self, obj):
        return build_media_url(self.context.get('request'), obj.image)

    def get_order_card(self, obj):
        from chat.order_context import serialized_order_card
        return serialized_order_card(self.context.get('request'), obj, self.context)

    def get_sender_name(self, obj):
        from chat.serializers import chat_sender_name
        return chat_sender_name(obj, staff_view=True)


class AdminChatSessionSerializer(EntityNumberModelSerializer):
    user_name = serializers.SerializerMethodField()
    user_avatar = serializers.SerializerMethodField()
    user_role_display = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    can_read = serializers.SerializerMethodField()
    claimed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ChatSession
        fields = [
            'id', 'user', 'user_name', 'user_avatar', 'user_role_display',
            'last_message', 'last_message_at', 'unread_support', 'created_at',
            'claimed_by', 'claimed_by_name', 'claim_version', 'claimed_at', 'can_read',
        ]

    def get_can_read(self, obj):
        from chat.authorization import can_read_session
        return can_read_session(getattr(self.context.get('request'), 'account', None), obj)

    def get_last_message(self, obj):
        return obj.last_message if self.get_can_read(obj) else ''

    def get_claimed_by_name(self, obj):
        return (obj.claimed_by.nickname or obj.claimed_by.username) if obj.claimed_by_id else ''

    def get_user_name(self, obj):
        if obj.account_id:
            return obj.account.nickname or obj.account.username
        if not obj.user_id:
            return ''
        return obj.user.nickname or obj.user.username

    def get_user_avatar(self, obj):
        return (obj.account.avatar_url or '') if obj.account_id else (obj.user.avatar_url or '' if obj.user_id else '')

    def get_user_role_display(self, obj):
        return obj.account.get_account_type_display() if obj.account_id else (obj.user.get_role_display() if obj.user_id else '')


# ---------------- 优惠券 ----------------
class AdminCouponSerializer(EntityNumberModelSerializer):
    discount_type_display = serializers.CharField(source='get_discount_type_display', read_only=True)
    is_claimable = serializers.BooleanField(read_only=True)
    amount = serializers.IntegerField(required=False, min_value=0)

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
        elif kind == Coupon.DiscountType.RATE:
            if not self.instance and 'amount' not in attrs:
                attrs['amount'] = 0
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
        if kind == Coupon.DiscountType.DIRECT and threshold and (
            not self.instance or self.instance.discount_type != kind or self.instance.threshold != threshold
        ):
            raise serializers.ValidationError({'threshold': '无门槛券不能设置使用门槛'})
        if total and total < getattr(self.instance, 'claimed_qty', 0):
            raise serializers.ValidationError({'total_qty': '发放总量不能小于已领取数量'})
        return attrs


class AdminUserCouponSerializer(EntityNumberModelSerializer):
    coupon_name = serializers.CharField(source='coupon.name', read_only=True)
    username = serializers.SerializerMethodField()
    user_name = serializers.SerializerMethodField()
    order_no = serializers.CharField(source='order.order_no', read_only=True, default='')
    status = serializers.SerializerMethodField()
    status_display = serializers.SerializerMethodField()
    effective_valid_to = serializers.DateTimeField(source='valid_to', read_only=True)

    class Meta:
        model = UserCoupon
        fields = ['id', 'user', 'username', 'user_name', 'coupon', 'coupon_name', 'status',
                  'status_display', 'order', 'order_no', 'claimed_at', 'used_at', 'effective_valid_to']

    def get_user_name(self, obj):
        account = obj.account or obj.user
        return (account.nickname or account.username) if account else ''

    def get_username(self, obj):
        return (obj.account or obj.user).username

    def get_status(self, obj):
        if hasattr(obj, 'admin_status'):
            return obj.admin_status
        if obj.status == UserCoupon.Status.UNUSED:
            deadline = obj.expires_at or obj.coupon.valid_to
            if deadline is not None and deadline <= timezone.now():
                return 'EXPIRED'
            if not obj.coupon.is_active:
                return 'DISABLED'
            if obj.account_id and obj.business_cycle != obj.account.business_cycle:
                return 'PREVIOUS_CYCLE'
        return obj.status

    def get_status_display(self, obj):
        return {'UNUSED': '未使用', 'USED': '已使用', 'EXPIRED': '已过期',
                'DISABLED': '模板停用', 'PREVIOUS_CYCLE': '非当前周期'}.get(self.get_status(obj), '状态待核对')


# ---------------- 公告 ----------------
class AdminAnnouncementSerializer(EntityNumberModelSerializer):
    def validate_content(self, value):
        from engagement.serializers import rich_text
        return rich_text(value)
    class Meta:
        model = Announcement
        fields = ['id', 'title', 'content', 'is_pinned', 'is_active', 'sort_order',
                  'created_at', 'updated_at']


# ---------------- Banner ----------------
class AdminBannerListSerializer(EntityNumberListSerializer):
    def to_representation(self, data):
        from banners.availability import prepare_banner_availability
        return super().to_representation(prepare_banner_availability(data.all() if hasattr(data, 'all') else data))


class AdminBannerSerializer(EntityNumberModelSerializer):
    image_url = serializers.SerializerMethodField()
    link_available = serializers.SerializerMethodField()
    link_type_display = serializers.CharField(source='get_link_type_display', read_only=True)

    class Meta:
        model = Banner
        list_serializer_class = AdminBannerListSerializer
        fields = ['id', 'image', 'image_url', 'title', 'link_type', 'link_type_display',
                  'link_value', 'link_available', 'sort_order', 'is_active', 'created_at']
        extra_kwargs = {'image': {'write_only': True, 'required': False}}

    def get_image_url(self, obj):
        return build_media_url(self.context.get('request'), obj.image)

    def get_link_available(self, obj):
        if not hasattr(obj, 'link_available'):
            from banners.availability import prepare_banner_availability
            prepare_banner_availability([obj])
        return obj.link_available

    def validate(self, attrs):
        if self.instance is None and not attrs.get('image'):
            raise serializers.ValidationError({'image': '请上传轮播图片'})
        # Hiding an existing banner must remain possible if its former link
        # target has since been removed.
        if self.instance is not None and set(attrs) == {'is_active'} and attrs['is_active'] is False:
            return attrs
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
                raise serializers.ValidationError({'link_value': '请选择支持的网页业务入口'})
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
class AdminAchievementSerializer(EntityNumberModelSerializer):
    metric_display = serializers.SerializerMethodField()

    class Meta:
        model = Achievement
        fields = ['id', 'code', 'title', 'desc', 'icon', 'metric', 'metric_display',
                  'target', 'sort_order', 'is_active', 'created_at']

    def get_metric_display(self, obj):
        if obj.metric == Achievement.Metric.AMOUNT:
            from stores.context import current_currency_name
            return f'累计消费（{current_currency_name()}）'
        return obj.get_metric_display()


class AdminCheckinRuleSerializer(EntityNumberModelSerializer):
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


class AdminCheckinGiftSerializer(EntityNumberModelSerializer):
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


class AdminCheckinProgressSerializer(EntityNumberModelSerializer):
    username = serializers.SerializerMethodField()
    nickname = serializers.SerializerMethodField()
    checked_count = serializers.SerializerMethodField()

    class Meta:
        model = CheckinMonthProgress
        fields = [
            'id', 'user', 'username', 'nickname', 'year', 'month', 'checked_count',
            'makeup_cards', 'full_attendance_awarded', 'full_attendance_reward_name',
            'full_attendance_tag_code', 'full_attendance_awarded_at', 'updated_at',
        ]

    def get_checked_count(self, obj):
        from users.models import CheckinRecord
        owner = {'account_id': obj.account_id} if obj.account_id else {'user_id': obj.user_id}
        return CheckinRecord.objects.filter(**owner).filter(
            checkin_date__year=obj.year, checkin_date__month=obj.month,
        ).count()

    def get_username(self, obj):
        return obj.account.username if obj.account_id else obj.user.username

    def get_nickname(self, obj):
        return obj.account.nickname if obj.account_id else obj.user.nickname


# ---------------- 服务项/礼物单 ----------------
class AdminGameCategorySerializer(EntityNumberModelSerializer):
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


class AdminServiceCategorySerializer(EntityNumberModelSerializer):
    def validate_is_gift(self, value):
        if self.instance and value != self.instance.is_gift and Order.objects.filter(service__service_category=self.instance).exists():
            raise serializers.ValidationError('该分类已有订单，不能变更礼物属性，请新建分类')
        return value

    class Meta:
        model = ServiceCategory
        fields = ['id', 'name', 'is_gift', 'order_kind', 'remark', 'sort_order', 'is_active', 'created_at']
        read_only_fields = ['created_at']


class AdminServiceItemSerializer(EntityNumberModelSerializer):
    price = serializers.IntegerField(min_value=1, max_value=2147483647, error_messages={
        'min_value': '商品价格必须大于 0', 'max_value': '商品价格超出允许范围',
    })

    @transaction.atomic
    def update(self, instance, validated_data):
        old_game = instance.game_category_id
        changed_text = tuple(
            field for field in ('rules', 'details_html')
            if field in validated_data and validated_data[field] != getattr(instance, field)
        )
        instance = super().update(instance, validated_data)
        compliance_fields = {'rules': 'SERVICE_RULES', 'details_html': 'SERVICE_DETAILS'}
        if not self.context.get('compliance_receipts_recorded_externally'):
            for field in changed_text:
                _record_content_field(self, 'service_item', instance, compliance_fields[field])
        if old_game != instance.game_category_id:
            for profile in instance.escorts.select_for_update().order_by('pk'):
                if old_game and not profile.service_items.filter(game_category_id=old_game).exists():
                    profile.game_categories.remove(old_game)
                if instance.game_category_id:
                    profile.game_categories.add(instance.game_category_id)
        return instance

    rules = serializers.CharField(max_length=5000, allow_blank=True, required=False)
    details_html = serializers.CharField(max_length=100000, allow_blank=True, required=False)

    def validate_rules(self, value):
        return _check_content_field(self, 'SERVICE_RULES', value)

    def validate_details_html(self, value):
        from common.rich_text import sanitize_catalog_html
        return _check_content_field(self, 'SERVICE_DETAILS', sanitize_catalog_html(value))

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
        if get('fulfillment_provider_count', 1) == 1 and get('billing_provider_count', 1) != 1:
            raise serializers.ValidationError('单陪服务计费人数必须为 1')
        if experience:
            attrs['service_type'] = 'EXPERIENCE'
        elif get('service_type','REGULAR') == 'EXPERIENCE':
            raise serializers.ValidationError('体验单须选择体验资格类型')
        if experience and (get('platform') not in ('PC','MOBILE') or get('is_addon')
                or get('fulfillment_provider_count',1) != 1 or get('billing_provider_count',1) != 1 or get('purchase_mode','DIRECT') != 'DIRECT'):
            raise serializers.ValidationError('体验套餐须为端游/手游的一份单陪主套餐，直接下单')
        if benefit and (experience or get('is_addon') or get('fulfillment_provider_count',1) != 1 or get('billing_provider_count',1) != 1):
            raise serializers.ValidationError('VIP服务券仅可绑定普通单陪主套餐')
        if benefit == 'COMPANION_HOUR' and get('price_unit','份') not in ('小时','时','hour'):
            raise serializers.ValidationError('小时陪玩券绑定套餐须按小时计价')
        if self.instance and Order.objects.filter(service=self.instance).exists():
            if experience != self.instance.experience_kind or benefit != self.instance.benefit_kind:
                raise serializers.ValidationError('已有订单的套餐不能变更体验资格或VIP权益类型，请新建套餐')
        category = get('service_category')
        is_gift = bool(category and category.is_gift)
        if get('room_enabled', False) and (is_gift or get('is_addon')):
            raise serializers.ValidationError({'room_enabled': '礼物和加购项目不能单独创建服务房间'})
        # One store coin is ten ledger units. Service catalogue list prices
        # are configured in multiples of ten store coins; gift prices have
        # their own rules. Do not rewrite older prices or block unrelated edits.
        price_is_new = (self.instance is None or
            ('price' in attrs and attrs['price'] != self.instance.price) or
            ('service_category' in attrs and self.instance.service_category_id
             and self.instance.service_category.is_gift and not is_gift) or
            (attrs.get('is_active') is True and not self.instance.is_active))
        if not is_gift and price_is_new and get('price', 0) % 100:
            raise serializers.ValidationError({'price': '服务标价必须是 10 店币的整数倍'})
        if get('commission_override',False):
            if is_gift:
                raise serializers.ValidationError('礼物使用独立抽成规则，不支持自定义订单抽成')
            if get('commission_type','PERCENT') == 'PERCENT' and get('commission_rate') is None:
                raise serializers.ValidationError({'commission_rate':'自定义百分比抽成须填写抽成率'})
        if not is_gift and (get('gift_naming_days', 0) or get('gift_festival', '') or get('gift_starts_at') or get('gift_ends_at')):
            raise serializers.ValidationError('冠名天数与节日限定仅适用于礼物')
        if get('gift_festival', '') and get('price', 0) < 2100:
            raise serializers.ValidationError(f'节日限定礼物最低 21 元，即 210 {current_currency_name()}')
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
                  'commission_override', 'commission_type', 'commission_fixed', 'commission_rate', 'commission_policy', 'experience_kind', 'benefit_kind', 'service_type', 'allows_discount_coupons', 'cover_url', 'images', 'highlights', 'sort_order',
                  'is_active', 'created_at', 'price_unit', 'billing_provider_count', 'fulfillment_provider_count', 'purchase_mode', 'purchase_note',
                  'catalog_key', 'catalog_source', 'is_addon', 'room_enabled', 'catalog_family', 'catalog_variant']
        read_only_fields = ['catalog_key', 'catalog_source']


# ---------------- 促销活动 ----------------
class AdminPromotionSerializer(EntityNumberModelSerializer):
    scope_display = serializers.CharField(source='get_scope_display', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True, default='')
    item_names = serializers.SerializerMethodField()
    window_status = serializers.CharField(read_only=True)

    class Meta:
        model = Promotion
        fields = ['id', 'title', 'remark', 'discount_rate', 'commission_type', 'commission_fixed', 'commission_rate',
                  'scope', 'scope_display', 'category', 'category_name', 'items', 'item_names',
                  'start_at', 'end_at', 'priority', 'is_active', 'window_status',
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
class AdminSupportCardSerializer(EntityNumberModelSerializer):
    class Meta:
        model = SupportContactCard
        fields = ['id', 'name', 'company', 'wechat_id', 'wecom_corp_id',
                  'wecom_service_url', 'avatar_url', 'qrcode_url',
                  'tips', 'is_active', 'sort_order', 'staff_account', 'created_at', 'updated_at']

    def validate(self, attrs):
        from support.assignment import active_staff
        account = attrs.get('staff_account', getattr(self.instance, 'staff_account', None))
        active = attrs.get('is_active', getattr(self.instance, 'is_active', True))
        if active and (not account or not active_staff(account.pk)):
            raise serializers.ValidationError({'staff_account': '启用的客服名片须绑定本店在职后台员工'})
        if account and not active_staff(account.pk):
            raise serializers.ValidationError({'staff_account': '请选择本店在职后台员工'})
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
class AdminAuditionLinkSerializer(EntityNumberModelSerializer):
    operator_name = serializers.SerializerMethodField()
    boss_user_name = serializers.SerializerMethodField()
    provider_user_name = serializers.SerializerMethodField()
    boss_url = serializers.SerializerMethodField()
    boss_token = serializers.SerializerMethodField()
    provider_url = serializers.SerializerMethodField()
    provider_token = serializers.SerializerMethodField()
    boss_binding = serializers.SerializerMethodField()
    provider_binding = serializers.SerializerMethodField()
    entry_status = serializers.CharField(read_only=True)
    boss_url_placeholder = serializers.SerializerMethodField()
    provider_url_placeholder = serializers.SerializerMethodField()

    class Meta:
        model = AuditionLink
        fields = ['id', 'title', 'remark', 'expire_at', 'is_active',
                  'operator', 'operator_name', 'boss_token', 'provider_token',
                  'boss_user', 'boss_user_name', 'provider_user', 'provider_user_name',
                  'boss_binding', 'provider_binding', 'entry_status', 'boss_url_placeholder', 'provider_url_placeholder',
                  'boss_url', 'provider_url', 'created_at', 'updated_at']
        read_only_fields = ['operator', 'boss_token', 'provider_token',
                            'created_at', 'updated_at']

    def validate(self, attrs):
        if 'boss_user' in self.initial_data:
            raise serializers.ValidationError({'boss_user': '请使用老板账号绑定操作，旧入口不可直接修改'})
        if 'provider_user' in self.initial_data:
            raise serializers.ValidationError({'provider_user': '请使用陪玩账号绑定操作，旧入口不可直接修改'})
        return attrs

    def get_boss_binding(self, obj):
        if obj.boss_account_id:
            account = obj.boss_account
            return {'kind': 'account', 'id': account.id, 'name': account.nickname or account.username,
                    'username': account.username, 'account_no': account.account_no,
                    'available': account.is_active and account.can_login and account.account_type == 'BOSS'}
        if obj.boss_user_id:
            return {'kind': 'legacy', 'id': None, 'name': obj.boss_user.nickname or obj.boss_user.username,
                    'username': obj.boss_user.username, 'account_no': '', 'available': None}
        return {'kind': 'unbound', 'id': None, 'name': '', 'username': '', 'account_no': '', 'available': False}

    def get_provider_binding(self, obj):
        if obj.provider_account_id:
            from club_accounts.otp import otp_required
            account = obj.provider_account
            return {'kind': 'account', 'id': account.id, 'name': account.nickname or account.username,
                    'username': account.username, 'account_no': account.account_no,
                    'available': account.is_active and account.can_login and account.account_type == 'PROVIDER',
                    'otp_required': otp_required(account)}
        if obj.provider_user_id:
            return {'kind': 'legacy', 'id': None, 'name': obj.provider_user.nickname or obj.provider_user.username,
                    'username': obj.provider_user.username, 'account_no': '', 'available': None, 'otp_required': None}
        return {'kind': 'unbound', 'id': None, 'name': '', 'username': '', 'account_no': '', 'available': None, 'otp_required': None}

    def _placeholder(self, setting):
        role = 'boss' if setting == 'AUDITION_BOSS_LINK_BASE_URL' else 'provider'
        host = (urlsplit(self._audition_base(role)).hostname or '').lower()
        return not host or host == 'example.com' or host.endswith('.example.com') or host.endswith('.example')

    def get_boss_url_placeholder(self, obj):
        return self._placeholder('AUDITION_BOSS_LINK_BASE_URL')

    def get_provider_url_placeholder(self, obj):
        return self._placeholder('AUDITION_PROVIDER_LINK_BASE_URL')

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

    def _audition_base(self, role):
        from stores.conf import settings
        base_setting = (
            'AUDITION_BOSS_LINK_BASE_URL'
            if role == 'boss'
            else 'AUDITION_PROVIDER_LINK_BASE_URL'
        )
        base = getattr(settings, base_setting, settings.AUDITION_LINK_BASE_URL).rstrip('/')
        from stores.context import current_store
        store = current_store()
        if store is not None:
            entry = store.boss_url if role == 'boss' else store.provider_url
            if entry:
                # Current React entry points use hash routes.
                from urllib.parse import urlsplit, urlunsplit
                parts = urlsplit(entry)
                base = urlunsplit(parts._replace(fragment='/audition'))
        return base

    def _build_url(self, token, role):
        from stores.links import audition_url
        return audition_url(self._audition_base(role), token, role)

    def get_boss_url(self, obj):
        return self._build_url(obj.boss_token, 'boss') if self._can_manage_boss_link() else None

    def get_boss_token(self, obj):
        return obj.boss_token if self._can_manage_boss_link() else None

    def _can_manage_boss_link(self):
        if not hasattr(self, '_boss_link_allowed'):
            from .permissions import get_account_permissions
            request = self.context.get('request')
            self._boss_link_allowed = bool(request and 'audition:bind_boss' in get_account_permissions(getattr(request, 'account', None)))
        return self._boss_link_allowed

    def get_provider_url(self, obj):
        return self._build_url(obj.provider_token, 'provider') if self._can_access_provider_link(obj) else None

    def get_provider_token(self, obj):
        return obj.provider_token if self._can_access_provider_link(obj) else None

    def _can_access_provider_link(self, obj):
        if not obj.provider_account_id and not obj.provider_user_id:
            return True
        if not hasattr(self, '_provider_link_allowed'):
            from .permissions import get_account_permissions
            request = self.context.get('request')
            self._provider_link_allowed = bool(request and 'audition:bind_provider' in get_account_permissions(getattr(request, 'account', None)))
        return self._provider_link_allowed


class AdminAuditionSignupSerializer(EntityNumberModelSerializer):
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    applicant_name = serializers.SerializerMethodField()
    applicant_username = serializers.SerializerMethodField()
    applicant_no = serializers.CharField(source='applicant_account.account_no', read_only=True, default='')
    link_title = serializers.SerializerMethodField()
    auditor_name = serializers.SerializerMethodField()

    class Meta:
        model = AuditionSignup
        fields = ['id', 'link', 'link_title', 'applicant', 'applicant_name', 'applicant_username', 'applicant_no',
                  'contact', 'game', 'remark', 'status', 'status_display',
                  'auditor', 'auditor_name', 'audit_remark', 'audited_at',
                  'created_at', 'updated_at']

    def get_applicant_name(self, obj):
        if obj.applicant_account_id:
            return obj.applicant_account.nickname or obj.applicant_account.username
        if not obj.applicant_id:
            return ''
        return obj.applicant.nickname or obj.applicant.username

    def get_applicant_username(self, obj):
        return obj.applicant_account.username if obj.applicant_account_id else (obj.applicant.username if obj.applicant_id else '')

    def get_link_title(self, obj):
        return obj.link.title if obj.link_id else ''

    def get_auditor_name(self, obj):
        if obj.auditor_account_id:
            return obj.auditor_account.nickname or obj.auditor_account.username
        if not obj.auditor_id:
            return ''
        return obj.auditor.nickname or obj.auditor.username


# ---------------- 站内消息 ----------------
class AdminMessageSerializer(EntityNumberModelSerializer):
    type_display = serializers.CharField(source='get_type_display', read_only=True)
    recipient_name = serializers.SerializerMethodField()
    recipient_username = serializers.SerializerMethodField()
    recipient_no = serializers.CharField(source='recipient_account.account_no', read_only=True, default='')

    class Meta:
        model = Message
        fields = ['id', 'recipient', 'recipient_account', 'recipient_name', 'recipient_username', 'recipient_no', 'type', 'type_display', 'title',
                  'preview', 'detail', 'action_url', 'is_read', 'related_order_id',
                  'created_at']

    def get_recipient_name(self, obj):
        if obj.recipient_account_id:
            return obj.recipient_account.nickname or obj.recipient_account.username
        return obj.recipient.nickname or obj.recipient.username if obj.recipient_id else ''

    def get_recipient_username(self, obj):
        return obj.recipient_account.username if obj.recipient_account_id else (obj.recipient.username if obj.recipient_id else '')


# ---------------- 角色 ----------------
class AdminRoleSerializer(EntityNumberModelSerializer):
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
        from .store_roles import is_store_finance
        code = attrs.get('code', getattr(self.instance, 'code', ''))
        name = attrs.get('name', getattr(self.instance, 'name', ''))
        if is_store_finance(code, name):
            raise serializers.ValidationError('门店不设置财务角色；平台财务使用独立平台账号')
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
class AdminMembershipSerializer(EntityNumberModelSerializer):
    user = serializers.PrimaryKeyRelatedField(source='account', read_only=True)
    username = serializers.CharField(source='account.username', read_only=True)
    nickname = serializers.CharField(source='account.nickname', read_only=True)
    phone = serializers.CharField(source='account.phone', read_only=True)
    role_ids = serializers.SerializerMethodField()
    role_names = serializers.SerializerMethodField()
    is_superuser = serializers.SerializerMethodField()
    today_dispatch_amount = serializers.SerializerMethodField()
    today_dispatch_review_count = serializers.SerializerMethodField()

    class Meta:
        model = AdminMembership
        fields = ['id', 'user', 'username', 'nickname', 'phone', 'role_ids', 'role_names',
                  'is_superuser', 'remark', 'today_dispatch_amount', 'today_dispatch_review_count',
                  'is_active', 'created_at']

    def get_role_ids(self, obj):
        return [r.id for r in obj.roles.all()]

    def get_role_names(self, obj):
        return [r.name for r in obj.roles.all()]

    def get_is_superuser(self, obj):
        return any(role.code == 'super_admin' for role in obj.roles.all())

    def _dispatch_metric(self, obj):
        view = self.context.get('view')
        if view is None or not hasattr(view, 'today_dispatch_metrics'):
            return {'amount': None, 'review_count': None}
        return view.today_dispatch_metrics().get(obj.account_id, {'amount': 0, 'review_count': 0})

    def get_today_dispatch_amount(self, obj):
        return self._dispatch_metric(obj)['amount']

    def get_today_dispatch_review_count(self, obj):
        return self._dispatch_metric(obj)['review_count']


class AdminCreateMembershipSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(min_length=6, max_length=128, trim_whitespace=False)
    nickname = serializers.CharField(max_length=50, required=False, allow_blank=True, default='')
    phone = serializers.CharField(max_length=20, required=True, allow_blank=False)
    remark = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    roles = serializers.PrimaryKeyRelatedField(queryset=AdminRole.objects.all(), many=True)

    def validate_roles(self, roles):
        from .store_roles import is_store_finance
        if any(is_store_finance(role.code, role.name) for role in roles):
            raise serializers.ValidationError('不能分配门店财务角色；平台财务使用独立平台账号')
        return roles

    def validate_phone(self, value):
        value = value.strip()
        if not value.isascii() or not value.isdigit() or not 5 <= len(value) <= 20:
            raise serializers.ValidationError('请输入可接收短信的有效手机号')
        return value

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

    def validate_roles(self, roles):
        from .store_roles import is_store_finance
        if any(is_store_finance(role.code, role.name) for role in roles):
            raise serializers.ValidationError('不能分配门店财务角色；平台财务使用独立平台账号')
        return roles

    def validate_phone(self, value):
        value = value.strip()
        if not value.isascii() or not value.isdigit() or not 5 <= len(value) <= 20:
            raise serializers.ValidationError('请输入可接收短信的有效手机号')
        return value


class AdminAuditLogSerializer(EntityNumberModelSerializer):
    class Meta:
        model = AdminAuditLog
        fields = ['id', 'operator', 'operator_name', 'method', 'path', 'resource',
                  'object_id', 'request_body', 'status_code', 'ip', 'created_at']


# ---------------- 封禁审计 ----------------
class BanRecordSerializer(EntityNumberModelSerializer):
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
