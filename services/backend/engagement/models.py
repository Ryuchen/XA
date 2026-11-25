"""会员运营：积分与资金分账，兑换、抽奖和播报均保留不可变业务快照。"""
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator


class AchievementLicenseEvent(models.Model):
    """Original authorization and real-sales snapshot for a single source."""
    event_key = models.CharField(max_length=100, unique=True)
    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT)
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT)
    business_cycle = models.PositiveIntegerField()
    outcome = models.CharField(max_length=8)
    license_evidence = models.JSONField(default=dict)
    metrics = models.JSONField(default=dict)
    eligible = models.JSONField(default=list)
    sales_snapshot = models.JSONField(null=True, blank=True)
    sales_snapshot_hash = models.CharField(max_length=64, blank=True)
    decided_at = models.DateTimeField(default=timezone.now)


class EarnedAchievement(models.Model):
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT)
    business_cycle = models.PositiveIntegerField()
    achievement = models.ForeignKey('users.Achievement', on_delete=models.PROTECT)
    source_event = models.ForeignKey(AchievementLicenseEvent, on_delete=models.PROTECT)
    earned_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'business_cycle', 'achievement'],
                                               name='unique_earned_achievement_cycle')]


class AchievementLicenseReview(models.Model):
    event = models.OneToOneField(AchievementLicenseEvent, on_delete=models.PROTECT, related_name='review')
    request_id = models.UUIDField(unique=True)
    platform_actor_id = models.UUIDField()
    outcome = models.CharField(max_length=8)
    reason = models.CharField(max_length=500)
    granted_ids = models.JSONField(default=list)
    reviewed_at = models.DateTimeField(default=timezone.now)


class ClubOperationConfig(models.Model):
    # 固定主键 1；运营配置与账本分离，修改配置不重算历史记录。
    points_enabled = models.BooleanField(default=True)
    base_points_rate = models.PositiveSmallIntegerField(default=100, validators=[MaxValueValidator(1000)])
    birthday_multiplier = models.PositiveSmallIntegerField(default=2, validators=[MinValueValidator(1), MaxValueValidator(10)])
    vip_enabled = models.BooleanField(default=True)
    vip_min_single_recharge = models.PositiveIntegerField(default=0)
    coupon_stacking = models.BooleanField(default=False)
    season_code = models.CharField(max_length=40, blank=True, default='')
    season_ends_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class VipDeferredRecalculation(models.Model):
    """Store-local snapshot from the beginning of one disabled VIP period."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    epoch = models.PositiveIntegerField(default=0)
    is_open = models.BooleanField(default=False)
    # Null means the store was already disabled before this feature: never
    # pretend that today's tiers were the tiers at the historical disable time.
    baseline_rules = models.JSONField(null=True, blank=True)
    pending = models.BooleanField(default=False)
    opened_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='vip_deferred_singleton')]


class VipUpgradeReview(models.Model):
    """An upgrade entitlement whose historical issuance needs human review."""
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT)
    tier = models.ForeignKey('engagement.VipTier', on_delete=models.PROTECT)
    business_cycle = models.PositiveIntegerField()
    epoch = models.PositiveIntegerField()
    status = models.CharField(max_length=16, default='PENDING', choices=[('PENDING', '待核对'), ('RESOLVED', '已核对')])
    evidence = models.JSONField(default=dict)
    resolution = models.CharField(max_length=24, blank=True, default='', choices=[
        ('', '未处理'), ('ALREADY_ISSUED', '确认已发'), ('ISSUED', '人工确认补发')])
    resolution_note = models.CharField(max_length=500, blank=True, default='')
    resolved_by = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, null=True, blank=True,
                                    related_name='+')
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'tier', 'business_cycle', 'epoch'],
                                               name='uniq_vip_upgrade_review_epoch')]


class MemberProfile(models.Model):
    account = models.OneToOneField('club_accounts.ClubAccount', on_delete=models.CASCADE, related_name='membership')
    # 百分之一积分：消费 1 分人民币积 0.01 分，避免小额消费被截断。
    points_balance = models.BigIntegerField(default=0)
    birthday = models.DateField(null=True, blank=True)
    birthday_public = models.BooleanField(default=True)
    selected_tags = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class LicenseConsumptionPointsDecision(models.Model):
    """License decision at actual consumption confirmation, per order."""
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='license_points_decision')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT)
    event_key = models.CharField(max_length=160, unique=True)
    points_amount = models.BigIntegerField()
    outcome = models.CharField(max_length=16, choices=[('ALLOW', '允许发放'), ('DENY', '不发放'),
                                                        ('UNKNOWN', '授权待核对')])
    license_evidence = models.JSONField(default=dict)
    decided_at = models.DateTimeField(default=timezone.now)


class LicenseConsumptionPointsReview(models.Model):
    """A later human review; the original UNKNOWN decision stays immutable."""
    decision = models.OneToOneField(LicenseConsumptionPointsDecision, on_delete=models.PROTECT, related_name='review')
    request_id = models.UUIDField(unique=True)
    platform_actor_id = models.UUIDField()
    outcome = models.CharField(max_length=8, choices=[('ALLOW', '允许补发'), ('DENY', '不予发放')])
    reason = models.CharField(max_length=500)
    awarded_points = models.PositiveBigIntegerField(default=0)
    refunded_principal_at_award = models.PositiveBigIntegerField(default=0)
    points_ledger = models.OneToOneField('engagement.PointLedger', on_delete=models.PROTECT,
                                         null=True, blank=True, related_name='+')
    skip_reason = models.CharField(max_length=24, blank=True, default='')
    reviewed_at = models.DateTimeField(default=timezone.now)


class LicenseRefundVipDecision(models.Model):
    """Immutable growth authorization at refunded service confirmation."""
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='license_refund_vip_decision')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT)
    business_cycle = models.PositiveIntegerField()
    paid_units = models.PositiveBigIntegerField()
    refunded_units = models.PositiveBigIntegerField()
    payment_vip_enabled = models.BooleanField()
    confirmation_vip_enabled = models.BooleanField()
    outcome = models.CharField(max_length=16, choices=[('ALLOW', '允许'), ('DENY', '不允许'), ('UNKNOWN', '待核对')])
    license_evidence = models.JSONField(default=dict)
    decided_at = models.DateTimeField()


class LicenseRefundVipReview(models.Model):
    decision = models.OneToOneField(LicenseRefundVipDecision, on_delete=models.PROTECT, related_name='review')
    request_id = models.UUIDField(unique=True)
    platform_actor_id = models.UUIDField()
    outcome = models.CharField(max_length=8, choices=[('ALLOW', '允许'), ('DENY', '不允许')])
    reason = models.CharField(max_length=500)
    vip_applied = models.BooleanField(default=False)
    vip_recalculation_pending = models.BooleanField(default=False)
    reviewed_at = models.DateTimeField(default=timezone.now)


class LicenseAddonBenefitsReview(models.Model):
    """Audited resolution of one add-on's unknown License decisions."""
    consumption = models.OneToOneField('orders.OrderAddonConsumption', on_delete=models.PROTECT, related_name='license_review')
    request_id = models.UUIDField(unique=True)
    platform_actor_id = models.UUIDField()
    vip_outcome = models.CharField(max_length=8, blank=True, default='')
    points_outcome = models.CharField(max_length=8, blank=True, default='')
    reason = models.CharField(max_length=500)
    vip_applied = models.BooleanField(default=False)
    vip_recalculation_pending = models.BooleanField(default=False)
    awarded_points = models.PositiveBigIntegerField(default=0)
    refunded_principal_at_award = models.PositiveBigIntegerField(default=0)
    points_ledger = models.OneToOneField('engagement.PointLedger', on_delete=models.PROTECT,
                                         null=True, blank=True, related_name='+')
    skip_reason = models.CharField(max_length=24, blank=True, default='')
    reviewed_at = models.DateTimeField(default=timezone.now)


class RechargePackage(models.Model):
    amount = models.PositiveIntegerField(unique=True)
    gift_amount = models.PositiveIntegerField(default=0)
    points_rate = models.PositiveSmallIntegerField(default=100, validators=[MinValueValidator(100), MaxValueValidator(1000)])
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['amount']


class PointFundingLot(models.Model):
    class Kind(models.TextChoices):
        PREPAID = 'PREPAID', '预存'
        GIFT = 'GIFT', '赠送'
        COMPENSATION = 'COMPENSATION', '赔款'
        # 一笔充值的实充与赠送共用既有 FIFO 批次；读取余额时再按该批次的
        # 原始赠送金额拆分，避免迁移历史订单与退款分配关系。
        RECHARGE = 'RECHARGE', '充值混合批次'

    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT)
    source_key = models.CharField(max_length=160, unique=True)
    remaining = models.PositiveIntegerField()
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.PREPAID, db_index=True)
    points_rate = models.PositiveSmallIntegerField(default=100)
    # NULL preserves the historical inferred split until the new funding flow
    # explicitly locks and snapshots this mixed recharge's remaining gift.
    gift_remaining = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(
            condition=models.Q(gift_remaining__isnull=True) | (
                models.Q(kind='RECHARGE') & models.Q(gift_remaining__lte=models.F('remaining'))),
            name='funding_lot_gift_within_remaining',
        )]


class OrderPointAllocation(models.Model):
    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, related_name='point_allocations')
    lot = models.ForeignKey(PointFundingLot, on_delete=models.PROTECT)
    amount = models.PositiveIntegerField()
    points_amount = models.PositiveBigIntegerField()
    released = models.BooleanField(default=False)
    refunded_amount = models.PositiveIntegerField(default=0)
    # Populated only when a mixed lot already had a reliable explicit split.
    # NULL means older records whose original source cannot be reconstructed.
    source_gift_amount = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['order', 'lot'], name='unique_order_point_lot'),
            models.CheckConstraint(condition=models.Q(source_gift_amount__isnull=True)
                                   | models.Q(source_gift_amount__lte=models.F('amount')),
                                   name='order_point_gift_within_amount'),
        ]


class DefaultPaymentPointsSnapshot(models.Model):
    """First-release source and points terms for the default wallet checkout."""
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='default_points_snapshot')
    points_rate = models.PositiveSmallIntegerField()
    birthday_multiplier = models.PositiveSmallIntegerField()
    points_enabled = models.BooleanField()
    vip_enabled = models.BooleanField(default=False)
    vip_tier_id_snapshot = models.PositiveBigIntegerField(null=True, blank=True)
    vip_level_snapshot = models.PositiveIntegerField(null=True, blank=True)
    awarded_refunded_principal = models.PositiveIntegerField(null=True, blank=True)


class DefaultPaymentSource(models.Model):
    snapshot = models.ForeignKey(DefaultPaymentPointsSnapshot, on_delete=models.PROTECT, related_name='sources')
    allocation = models.ForeignKey(OrderPointAllocation, on_delete=models.PROTECT, related_name='default_sources')
    kind = models.CharField(max_length=16, choices=[('PREPAID', '本金'), ('GIFT', '赠送'), ('COMPENSATION', '赔付')])
    amount = models.PositiveIntegerField()
    refunded_amount = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['allocation', 'kind'], name='default_payment_source_once'),
            models.CheckConstraint(condition=models.Q(kind__in=['PREPAID', 'GIFT', 'COMPENSATION']), name='default_payment_source_kind'),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='default_payment_source_positive'),
            models.CheckConstraint(condition=models.Q(refunded_amount__lte=models.F('amount')), name='default_payment_source_refund_limit'),
        ]


class OrderFundingAllocation(models.Model):
    """Source-specific payment fact for the new wallet flow.

    A historic mixed recharge can contribute both principal and gift from the
    same lot, so the kind is part of the unique identity. Legacy allocations
    are retained separately and must not be retroactively guessed.
    """
    class Kind(models.TextChoices):
        PREPAID = 'PREPAID', '本金'
        GIFT = 'GIFT', '赠送'
        COMPENSATION = 'COMPENSATION', '赔付'

    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, related_name='funding_allocations')
    lot = models.ForeignKey(PointFundingLot, on_delete=models.PROTECT)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    amount = models.PositiveIntegerField()
    refunded_amount = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['order', 'lot', 'kind'], name='unique_order_funding_source'),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='order_funding_amount_positive'),
            models.CheckConstraint(condition=models.Q(kind__in=['PREPAID', 'GIFT', 'COMPENSATION']),
                                   name='order_funding_kind_valid'),
            models.CheckConstraint(condition=models.Q(refunded_amount__lte=models.F('amount')),
                                   name='order_funding_refund_within_amount'),
        ]


class PointLedger(models.Model):
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='point_ledger')
    event_key = models.CharField(max_length=160, unique=True)
    kind = models.CharField(max_length=24)
    amount = models.BigIntegerField()  # 百分之一积分，有符号
    balance_after = models.BigIntegerField()
    remark = models.CharField(max_length=255)
    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, null=True, blank=True)
    operator = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']
        indexes = [models.Index(fields=['account', '-id'], name='points_account_time_idx')]


class BroadcastRule(models.Model):
    class Kind(models.TextChoices):
        NAMING = 'NAMING', '冠名播报'
        RECHARGE = 'RECHARGE', '充值播报'
        UPGRADE = 'UPGRADE', '升级播报'
        REVIEW = 'REVIEW', '好评广播'
        BIRTHDAY = 'BIRTHDAY', '生日祝福'
        GIFT = 'GIFT', '礼物单播报'
        LOTTERY = 'LOTTERY', '转盘中奖播报'
        LOTTERY_SERVICE = 'LOTTERY_SERVICE', '抽中体验单'
        LOTTERY_NAMING = 'LOTTERY_NAMING', '抽中冠名'
        LOTTERY_TAG = 'LOTTERY_TAG', '抽中 TAG'

    kind = models.CharField(max_length=24, choices=Kind.choices, unique=True)
    template = models.CharField(max_length=255)
    enabled = models.BooleanField(default=True)
    visible_hours = models.PositiveIntegerField(default=72, validators=[MinValueValidator(1), MaxValueValidator(8760)])
    sort_order = models.IntegerField(default=0)
    mask_nickname = models.BooleanField(default=False)


class Broadcast(models.Model):
    event_key = models.CharField(max_length=160, unique=True)
    kind = models.CharField(max_length=24, choices=BroadcastRule.Kind.choices)
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.SET_NULL, null=True, blank=True)
    content = models.CharField(max_length=255)
    is_visible = models.BooleanField(default=True)
    starts_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    notified_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ['-sort_order', '-id']
        indexes = [models.Index(fields=['is_visible', 'expires_at'], name='broadcast_visible_idx')]


class RewardItem(models.Model):
    class Audience(models.TextChoices):
        BOSS = 'BOSS', '老板'
        PROVIDER = 'PROVIDER', '陪玩'
        ALL = 'ALL', '全部会员'
    class Kind(models.TextChoices):
        PHYSICAL = 'PHYSICAL', '实物寄送'
        VIRTUAL = 'VIRTUAL', '游戏内交付'
        SERVICE = 'SERVICE', '服务权益'
        NAMING = 'NAMING', '冠名权益'
        TAG = 'TAG', '自定义 TAG'
        BALANCE = 'BALANCE', '余额兑换'
        POINTS = 'POINTS', '积分'

    code = models.SlugField(max_length=80, unique=True)
    vip_quota_kind = models.CharField(max_length=20, blank=True, default='', choices=[('', '按权益项目补差'), ('FRIEND', '好友位'), ('BIRTHDAY_GIFT', '生日礼物')])
    audience = models.CharField(max_length=8, choices=Audience.choices, default=Audience.BOSS)
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    image_url = models.CharField(max_length=500, blank=True, default='')
    description = models.TextField(blank=True, default='')
    point_cost = models.PositiveIntegerField(default=0)
    point_value = models.PositiveIntegerField(default=0)
    balance_amount = models.PositiveIntegerField(default=0)  # 余额账务单位；不可增加累充
    duration_days = models.PositiveIntegerField(default=0)
    duration_months = models.PositiveSmallIntegerField(default=0)
    highlighted = models.BooleanField(default=False)
    service = models.ForeignKey('orders.ServiceItem', on_delete=models.PROTECT, null=True, blank=True)
    stock = models.PositiveIntegerField(null=True, blank=True)  # NULL=不限
    redeemed_count = models.PositiveIntegerField(default=0, editable=False)
    is_active = models.BooleanField(default=False)
    in_shop = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'id']


class RewardClaim(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', '待处理'
        FULFILLING = 'FULFILLING', '交付中'
        COMPLETED = 'COMPLETED', '已完成'
        CANCELLED = 'CANCELLED', '已取消'

    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='reward_claims')
    reward = models.ForeignKey(RewardItem, on_delete=models.PROTECT)
    source_order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, null=True, blank=True)
    event_key = models.CharField(max_length=160, unique=True)
    request_fingerprint = models.CharField(max_length=64, blank=True, default='')
    source = models.CharField(max_length=24, default='REDEMPTION')
    name_snapshot = models.CharField(max_length=100)
    kind_snapshot = models.CharField(max_length=16)
    duration_days = models.PositiveIntegerField(default=0)
    duration_months = models.PositiveSmallIntegerField(default=0)
    highlighted = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)
    points_spent = models.PositiveIntegerField(default=0)  # 整数积分
    balance_amount = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    details = models.JSONField(default=dict, blank=True)  # 本人及有权限客服可读，绝不进入广播
    tag_revision_required = models.BooleanField(default=False)
    delivery_note = models.CharField(max_length=255, blank=True, default='')
    operator = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-id']


class GiftBenefitRule(models.Model):
    name = models.CharField(max_length=100, unique=True)
    reward = models.ForeignKey(RewardItem, on_delete=models.PROTECT)
    category = models.ForeignKey('orders.ServiceCategory', on_delete=models.PROTECT, null=True, blank=True)
    threshold = models.PositiveIntegerField()
    inclusive = models.BooleanField(default=False)
    cumulative = models.BooleanField(default=False)
    once_per_account = models.BooleanField(default=False)
    enabled = models.BooleanField(default=False)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)


class ProviderNaming(models.Model):
    provider = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='received_namings')
    sponsor = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='sponsored_namings')
    provider_business_cycle = models.PositiveIntegerField(editable=False)
    title = models.CharField(max_length=30)
    expires_at = models.DateTimeField()
    is_visible = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['provider', 'sponsor', 'provider_business_cycle'],
                                               name='unique_provider_sponsor_cycle_naming')]


class GiftNamingGrant(models.Model):
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT)
    naming = models.ForeignKey(ProviderNaming, on_delete=models.PROTECT)
    days = models.PositiveIntegerField()
    title_snapshot = models.CharField(max_length=30)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)


class LotteryCampaign(models.Model):
    name = models.CharField(max_length=100)
    point_cost = models.PositiveIntegerField(default=0)
    enabled = models.BooleanField(default=False)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    daily_limit = models.PositiveIntegerField(default=0)
    pity_enabled = models.BooleanField(default=True)
    pity_limit = models.PositiveIntegerField(default=240)
    pity_target_reward = models.ForeignKey('RewardItem', null=True, blank=True, on_delete=models.PROTECT,
                                         related_name='pity_campaigns')
    pity_generation = models.PositiveIntegerField(default=0, editable=False)
    version = models.PositiveIntegerField(default=1)
    rules = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(pity_limit__gte=1),
                                              name='lottery_pity_limit_positive')]


class LotteryPrize(models.Model):
    campaign = models.ForeignKey(LotteryCampaign, on_delete=models.CASCADE, related_name='prizes')
    reward = models.ForeignKey(RewardItem, on_delete=models.PROTECT)
    probability = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(10000)])
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'id']
        constraints = [
            models.UniqueConstraint(fields=['campaign', 'reward'], name='unique_campaign_reward'),
            models.CheckConstraint(condition=models.Q(probability__lte=10000), name='lottery_probability_valid'),
        ]


class LotteryPityState(models.Model):
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT)
    campaign = models.ForeignKey(LotteryCampaign, on_delete=models.PROTECT)
    business_cycle = models.PositiveIntegerField(default=1, editable=False)
    count = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['account', 'campaign', 'business_cycle'], name='unique_lottery_personal_pity'),
        ]


class LotteryBatch(models.Model):
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT)
    campaign = models.ForeignKey(LotteryCampaign, on_delete=models.PROTECT)
    business_cycle = models.PositiveIntegerField(default=1, editable=False)
    event_key = models.CharField(max_length=160, unique=True)
    request_fingerprint = models.CharField(max_length=64)
    count = models.PositiveSmallIntegerField()
    campaign_version = models.PositiveIntegerField()
    total_point_cost = models.PositiveBigIntegerField()
    points_balance = models.BigIntegerField()  # 与 MemberProfile 一致，以百分之一积分存储
    pity_snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(count__in=[1, 10]), name='lottery_batch_one_or_ten')]


class LotteryDraw(models.Model):
    business_cycle = models.PositiveIntegerField(default=1, editable=False)
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='lottery_draws')
    campaign = models.ForeignKey(LotteryCampaign, on_delete=models.PROTECT)
    prize = models.ForeignKey(LotteryPrize, on_delete=models.PROTECT)
    claim = models.OneToOneField(RewardClaim, on_delete=models.PROTECT)
    event_key = models.CharField(max_length=160, unique=True)
    point_cost = models.PositiveIntegerField()
    campaign_version = models.PositiveIntegerField()
    random_ticket = models.PositiveSmallIntegerField()
    probability_snapshot = models.JSONField()
    batch = models.ForeignKey(LotteryBatch, on_delete=models.PROTECT, null=True, blank=True, related_name='draws')
    draw_index = models.PositiveSmallIntegerField(default=1)
    rarity = models.CharField(max_length=3, blank=True, default='')
    is_pity = models.BooleanField(default=False)
    is_ten_guarantee = models.BooleanField(default=False)
    selection_pool = models.JSONField(default=list)
    pity_before = models.PositiveIntegerField(default=0)
    pity_after = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['batch', 'draw_index'], name='unique_lottery_batch_draw_index')]


class MemberTag(models.Model):
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.CASCADE, related_name='member_tags')
    event_key = models.CharField(max_length=160, unique=True)
    title = models.CharField(max_length=30)
    priority = models.PositiveIntegerField(default=0)
    highlighted = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspended_by = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    suspension_reason = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)


class VipTier(models.Model):
    boss_type = models.OneToOneField('users.BossType', on_delete=models.PROTECT, related_name='vip_config')
    level = models.PositiveSmallIntegerField(unique=True)
    points_rate = models.PositiveSmallIntegerField(default=100, validators=[MaxValueValidator(1000)])
    recharge_threshold = models.PositiveIntegerField()  # 历史存储字段名；现为累计已完成订单实付门槛，人民币分
    benefits = models.JSONField(default=dict)
    upgrade_rewards = models.JSONField(default=list, blank=True)
    companion_service_ids = models.JSONField(null=True, blank=True, default=None)
    safe_box_service_ids = models.JSONField(null=True, blank=True, default=None)
    monthly_discount = models.PositiveSmallIntegerField(default=100)
    monthly_coupon_count = models.PositiveSmallIntegerField(default=0)
    season_discount = models.PositiveSmallIntegerField(default=100)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['level']


class MemberBenefitGrant(models.Model):
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT)
    tier = models.ForeignKey(VipTier, on_delete=models.PROTECT)
    event_key = models.CharField(max_length=160, unique=True)
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=24)
    quantity = models.PositiveIntegerField(default=1)
    details = models.JSONField(default=dict)
    claimed_at = models.DateTimeField(auto_now_add=True)


class ClubContent(models.Model):
    class Kind(models.TextChoices):
        RIGHTS = 'RIGHTS', '老板权益'
        POPUP = 'POPUP', '旧版弹窗（已停用）'
        HOT = 'HOT', '俱乐部活动'
        ORDER_RULES = 'ORDER_RULES', '下单须知'
        PROVIDER_RULES = 'PROVIDER_RULES', '打手保障'
        AFTERSALE_RULES = 'AFTERSALE_RULES', '售后标准'
        POINT_RULES = 'POINT_RULES', '积分规则'

    code = models.SlugField(max_length=80, unique=True)
    kind = models.CharField(max_length=24, choices=Kind.choices)
    title = models.CharField(max_length=100)
    image_url = models.CharField(max_length=500, blank=True, default='')
    content = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    sort_order = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ['-sort_order', '-id']


class KookPointsConfig(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    enabled = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=0)
    listener_revision = models.PositiveIntegerField(default=0)
    daily_checkin_points = models.PositiveIntegerField(default=5)
    guild_id = models.CharField(max_length=64)
    checkin_channel_id = models.CharField(max_length=64)
    voice_channel_ids = models.JSONField(default=list, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=80, default='等待连接')


class KookCheckinRuleVersion(models.Model):
    revision = models.PositiveIntegerField(unique=True)
    points = models.PositiveIntegerField()
    effective_at = models.DateTimeField()
    operator = models.ForeignKey('club_accounts.ClubAccount', null=True, on_delete=models.PROTECT)


class KookOnlineRuleDraft(models.Model):
    """Administrator-submitted terms; never used for accrual until published."""
    config_revision = models.PositiveIntegerField(unique=True)
    window_ms = models.PositiveIntegerField()
    points = models.PositiveIntegerField()
    operator = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class KookBinding(models.Model):
    account = models.OneToOneField('club_accounts.ClubAccount', on_delete=models.PROTECT)
    business_cycle = models.PositiveIntegerField(null=True, blank=True, editable=False)
    user_id = models.CharField(max_length=64, unique=True)
    nickname = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    channel_id = models.CharField(max_length=64, blank=True)
    accounted_at = models.DateTimeField(null=True, blank=True)
    event_at = models.DateTimeField(null=True, blank=True)
    online_milliseconds = models.PositiveBigIntegerField(default=0)
    awarded_points = models.PositiveBigIntegerField(default=0)
    cycle_online_base = models.PositiveBigIntegerField(default=0)
    cycle_awarded_base = models.PositiveBigIntegerField(default=0)


class KookOnlineRuleVersion(models.Model):
    """Immutable terms used by online interval evidence."""
    revision = models.PositiveIntegerField(primary_key=True)
    window_ms = models.PositiveIntegerField()
    points = models.PositiveIntegerField()
    effective_at = models.DateTimeField(null=True, blank=True)
    operator = models.ForeignKey('club_accounts.ClubAccount', null=True, blank=True, on_delete=models.PROTECT)
    source_draft = models.OneToOneField('KookOnlineRuleDraft', null=True, blank=True, on_delete=models.PROTECT)


class KookOnlineInterval(models.Model):
    """Observed online interval and original entitlement decision."""
    binding = models.ForeignKey('KookBinding', on_delete=models.PROTECT, related_name='online_intervals')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT)
    account_cycle = models.PositiveIntegerField()
    binding_cycle = models.PositiveIntegerField(null=True, blank=True)
    channel_id = models.CharField(max_length=64)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField()
    elapsed_ms = models.PositiveIntegerField()
    rule_revision = models.PositiveIntegerField(default=0)
    rule_version = models.ForeignKey('KookOnlineRuleVersion', null=True, on_delete=models.PROTECT)
    rule_window_ms = models.PositiveIntegerField(default=7200000)
    rule_points = models.PositiveIntegerField(default=1)
    online_before = models.PositiveBigIntegerField()
    online_after = models.PositiveBigIntegerField()
    awarded_before = models.PositiveBigIntegerField()
    awarded_after = models.PositiveBigIntegerField()
    online_base_before = models.PositiveBigIntegerField()
    online_base_after = models.PositiveBigIntegerField()
    award_base_before = models.PositiveBigIntegerField()
    award_base_after = models.PositiveBigIntegerField()
    points_enabled_used = models.BooleanField(null=True)
    processing_status = models.CharField(max_length=12, default='DONE')
    processed_at = models.DateTimeField(null=True, blank=True)
    interval_outcome = models.CharField(max_length=8)
    award_outcome = models.CharField(max_length=8)
    points_ledger = models.ForeignKey('PointLedger', null=True, blank=True, on_delete=models.PROTECT)
    decision_source = models.ForeignKey('console.AdminAuditLog', null=True, blank=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['binding', 'started_at', 'ended_at'],
                                               name='kook_online_interval_unique')]


class KookOnlineCursor(models.Model):
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='kook_online_cursors')
    binding = models.ForeignKey('KookBinding', null=True, blank=True, on_delete=models.PROTECT, related_name='online_cursors')
    business_cycle = models.PositiveIntegerField()
    blocked = models.BooleanField(default=False)
    rule_version = models.ForeignKey('KookOnlineRuleVersion', default=0, on_delete=models.PROTECT)
    remainder_ms = models.PositiveBigIntegerField(default=0)
    pending_points = models.PositiveBigIntegerField(default=0)
    unbound_online_ms = models.PositiveBigIntegerField(default=0)
    unbound_awarded_points = models.PositiveBigIntegerField(default=0)
    last_ended_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'business_cycle'], name='kook_online_cursor_cycle_unique')]


class KookOnlineRuleBalance(models.Model):
    """Transactional accrued remainder and unpaid groups for a cycle and rule."""
    cursor = models.ForeignKey('KookOnlineCursor', on_delete=models.PROTECT, related_name='rule_balances')
    rule_version = models.ForeignKey('KookOnlineRuleVersion', on_delete=models.PROTECT)
    remainder_ms = models.PositiveBigIntegerField(default=0)
    pending_points = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['cursor', 'rule_version'], name='kook_online_rule_balance_unique')]


class KookOnlineRuleTransition(models.Model):
    """Actual per-cycle carry from one immutable rule into its successor."""
    cursor = models.ForeignKey('KookOnlineCursor', on_delete=models.PROTECT, related_name='rule_transitions')
    from_rule = models.ForeignKey('KookOnlineRuleVersion', on_delete=models.PROTECT, related_name='+')
    to_rule = models.ForeignKey('KookOnlineRuleVersion', on_delete=models.PROTECT, related_name='+')
    effective_at = models.DateTimeField()
    carried_ms = models.PositiveBigIntegerField()
    old_pending_points = models.PositiveBigIntegerField()
    new_remainder_ms = models.PositiveBigIntegerField()
    new_pending_points = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['cursor', 'to_rule'], name='kook_online_transition_unique')]


class KookOnlinePointAllocation(models.Model):
    """Share of an issued points ledger attributed to one original rule."""
    cursor = models.ForeignKey('KookOnlineCursor', on_delete=models.PROTECT)
    ledger = models.ForeignKey('PointLedger', on_delete=models.PROTECT, related_name='kook_rule_allocations')
    rule_version = models.ForeignKey('KookOnlineRuleVersion', on_delete=models.PROTECT)
    points = models.PositiveBigIntegerField()
    review_reason = models.CharField(max_length=500, blank=True, default='')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['ledger', 'rule_version'], name='kook_online_allocation_unique')]


class KookOnlineProcessing(models.Model):
    interval = models.OneToOneField('KookOnlineInterval', on_delete=models.PROTECT, related_name='processing')
    online_before = models.PositiveBigIntegerField()
    online_after = models.PositiveBigIntegerField()
    awarded_before = models.PositiveBigIntegerField()
    awarded_after = models.PositiveBigIntegerField()
    points_ledger = models.ForeignKey('PointLedger', null=True, blank=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class KookManualOnlineReview(models.Model):
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT)
    business_cycle = models.PositiveIntegerField()
    cursor = models.ForeignKey('KookOnlineCursor', on_delete=models.PROTECT)
    source_id = models.CharField(max_length=100)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField()
    online_seconds = models.PositiveIntegerField()
    operator = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    status = models.CharField(max_length=20, default='PENDING_REVIEW')
    reason = models.CharField(max_length=100, blank=True, default='')
    points_ledger = models.ForeignKey('PointLedger', null=True, blank=True, on_delete=models.PROTECT)
    resolution_request_id = models.UUIDField(null=True, blank=True, unique=True)
    resolution_outcome = models.CharField(max_length=8, blank=True, default='')
    resolution_points = models.PositiveIntegerField(null=True, blank=True)
    resolution_reason = models.CharField(max_length=500, blank=True, default='')
    platform_actor_id = models.UUIDField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'business_cycle', 'source_id'],
                                               name='kook_manual_online_source_unique')]


class KookManualOnlineSegment(models.Model):
    """Immutable rule-at-event facts for an actual manual source interval."""
    review = models.ForeignKey('KookManualOnlineReview', on_delete=models.PROTECT, related_name='rule_segments')
    rule_version = models.ForeignKey('KookOnlineRuleVersion', on_delete=models.PROTECT)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField()
    elapsed_ms = models.PositiveBigIntegerField()
    rule_window_ms = models.PositiveIntegerField()
    rule_points = models.PositiveIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['review', 'started_at'], name='kook_manual_segment_start_unique')]


class KookBindingChallenge(models.Model):
    account = models.OneToOneField('club_accounts.ClubAccount', on_delete=models.CASCADE)
    business_cycle = models.PositiveIntegerField(null=True, blank=True, editable=False)
    code_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()


class KookPointEvent(models.Model):
    event_id = models.CharField(max_length=100, unique=True)
    received_at = models.DateTimeField(auto_now_add=True)


class KookLicensePointsReview(models.Model):
    """Platform decision against one immutable UNKNOWN source event."""
    source = models.OneToOneField('console.AdminAuditLog', on_delete=models.PROTECT, related_name='kook_license_review')
    request_id = models.UUIDField(unique=True)
    platform_actor_id = models.UUIDField()
    outcome = models.CharField(max_length=8, choices=[('ALLOW', '允许'), ('DENY', '不允许')])
    evidence_digest = models.CharField(max_length=64)
    reason = models.CharField(max_length=500)
    awarded_points = models.PositiveBigIntegerField(default=0)
    credited_milliseconds = models.PositiveBigIntegerField(default=0)
    points_ledger = models.OneToOneField('engagement.PointLedger', on_delete=models.PROTECT,
                                         null=True, blank=True, related_name='+')
    skip_reason = models.CharField(max_length=24, blank=True, default='')
    reviewed_at = models.DateTimeField(default=timezone.now)


class LicenseGiftVipDecision(models.Model):
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='license_gift_vip_decision')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT)
    business_cycle = models.PositiveIntegerField()
    paid_units = models.PositiveBigIntegerField()
    license_evidence = models.JSONField(default=dict)
    decided_at = models.DateTimeField(default=timezone.now)


class LicenseGiftVipReview(models.Model):
    decision = models.OneToOneField(LicenseGiftVipDecision, on_delete=models.PROTECT, related_name='review')
    request_id = models.UUIDField(unique=True)
    platform_actor_id = models.UUIDField()
    outcome = models.CharField(max_length=8, choices=[('ALLOW', '允许'), ('DENY', '不允许')])
    reason = models.CharField(max_length=500)
    vip_applied = models.BooleanField(default=False)
    vip_recalculation_pending = models.BooleanField(default=False)
    refunded_units = models.PositiveBigIntegerField(default=0)
    skip_reason = models.CharField(max_length=24, blank=True, default='')
    reviewed_at = models.DateTimeField(default=timezone.now)


class ConsumptionConfirmation(models.Model):
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='consumption_confirmation')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT)
    business_cycle = models.PositiveIntegerField()
    amount = models.PositiveIntegerField()
    confirmed_at = models.DateTimeField(default=timezone.now)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by_refund = models.ForeignKey('orders.OrderRefund', on_delete=models.PROTECT, null=True, blank=True, related_name='consumption_reversals')
    refunded_amount = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=['account', 'business_cycle'], name='confirmed_cycle_idx')]


class RetirementAudit(models.Model):
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT)
    business_cycle = models.PositiveIntegerField()
    operator = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    reason = models.CharField(max_length=500)
    snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'business_cycle'], name='unique_retirement_cycle')]


class H5Page(models.Model):
    """One stable public link per page kind in this store database."""

    class Kind(models.TextChoices):
        PRICE_LIST = 'PRICE_LIST', '价目表'
        ORDER_NOTICE = 'ORDER_NOTICE', '下单须知'
        INTRODUCTION = 'INTRODUCTION', '门店介绍'

    kind = models.CharField(max_length=16, choices=Kind.choices, unique=True)
    public_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    revision = models.PositiveIntegerField(default=0)
    current_version = models.ForeignKey(
        'H5PageVersion', null=True, blank=True, on_delete=models.PROTECT,
        related_name='+', editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(revision__gte=0), name='h5_page_revision_nonnegative')]
        ordering = ['kind']

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous = type(self).objects.using(self._state.db).filter(pk=self.pk).only('public_token').first()
            if previous is not None and previous.public_token != self.public_token:
                raise ValidationError('H5 公开链接标识创建后不可更改')
            if self.current_version_id and self.current_version.page_id != self.pk:
                raise ValidationError('H5 当前版本必须属于同一页面')
        elif self.current_version_id:
            raise ValidationError('H5 页面首次创建时不能指定已发布版本')
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.get_kind_display()


class H5PageVersion(models.Model):
    """Immutable published text snapshot; withdrawal only clears the page pointer."""

    class ComplianceStatus(models.TextChoices):
        CHECKED = 'CHECKED', '已校验'
        DISABLED = 'DISABLED', '门店已停用文本合规'

    page = models.ForeignKey(H5Page, on_delete=models.PROTECT, related_name='versions')
    version = models.PositiveIntegerField()
    snapshot = models.JSONField()
    content_sha256 = models.CharField(max_length=64)
    compliance_status = models.CharField(max_length=16, choices=ComplianceStatus.choices)
    compliance_receipt_ids = models.JSONField(default=list)
    publisher = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    copyright_confirmed = models.BooleanField(default=False)
    published_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['page', 'version'], name='unique_h5_page_version'),
            models.CheckConstraint(condition=models.Q(version__gte=1), name='h5_page_version_positive'),
            models.CheckConstraint(condition=models.Q(copyright_confirmed=True), name='h5_page_copyright_confirmed'),
        ]
        ordering = ['page', 'version']
        indexes = [models.Index(fields=['page', '-version'], name='h5_page_history_idx')]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('H5 历史发布版本不可修改')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('H5 历史发布版本不可删除')


from .refunded_points_models import RefundedPointsConsumptionEvidence, TransferRefundPointsConsumptionEvidence  # noqa: E402,F401
