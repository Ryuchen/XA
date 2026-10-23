"""会员运营：积分与资金分账，兑换、抽奖和播报均保留不可变业务快照。"""
from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator


class ClubOperationConfig(models.Model):
    # 固定主键 1；运营配置与账本分离，修改配置不重算历史记录。
    points_enabled = models.BooleanField(default=True)
    birthday_multiplier = models.PositiveSmallIntegerField(default=2, validators=[MinValueValidator(1), MaxValueValidator(10)])
    vip_enabled = models.BooleanField(default=True)
    vip_min_single_recharge = models.PositiveIntegerField(default=0)
    coupon_stacking = models.BooleanField(default=False)
    season_code = models.CharField(max_length=40, blank=True, default='')
    season_ends_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class MemberProfile(models.Model):
    account = models.OneToOneField('club_accounts.ClubAccount', on_delete=models.CASCADE, related_name='membership')
    # 百分之一积分：消费 1 分人民币积 0.01 分，避免小额消费被截断。
    points_balance = models.BigIntegerField(default=0)
    birthday = models.DateField(null=True, blank=True)
    birthday_public = models.BooleanField(default=True)
    selected_tags = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class RechargePackage(models.Model):
    amount = models.PositiveIntegerField(unique=True)
    gift_amount = models.PositiveIntegerField(default=0)
    points_rate = models.PositiveSmallIntegerField(default=100, validators=[MinValueValidator(100), MaxValueValidator(1000)])
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['amount']


class PointFundingLot(models.Model):
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT)
    source_key = models.CharField(max_length=160, unique=True)
    remaining = models.PositiveIntegerField()
    points_rate = models.PositiveSmallIntegerField(default=100)
    created_at = models.DateTimeField(auto_now_add=True)


class OrderPointAllocation(models.Model):
    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, related_name='point_allocations')
    lot = models.ForeignKey(PointFundingLot, on_delete=models.PROTECT)
    amount = models.PositiveIntegerField()
    points_amount = models.PositiveBigIntegerField()
    released = models.BooleanField(default=False)
    refunded_amount = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['order', 'lot'], name='unique_order_point_lot')]


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
    title = models.CharField(max_length=30)
    expires_at = models.DateTimeField()
    is_visible = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['provider', 'sponsor'], name='unique_provider_sponsor_naming')]


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
    version = models.PositiveIntegerField(default=1)
    rules = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)


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
    count = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['account', 'campaign', 'business_cycle'], name='unique_lottery_personal_pity'),
            models.CheckConstraint(condition=models.Q(count__lt=240), name='lottery_pity_below_guarantee'),
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
    pity_before = models.PositiveSmallIntegerField(default=0)
    pity_after = models.PositiveSmallIntegerField(default=0)
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
    created_at = models.DateTimeField(auto_now_add=True)


class VipTier(models.Model):
    boss_type = models.OneToOneField('users.BossType', on_delete=models.PROTECT, related_name='vip_config')
    level = models.PositiveSmallIntegerField(unique=True)
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
    guild_id = models.CharField(max_length=64)
    checkin_channel_id = models.CharField(max_length=64)
    voice_channel_ids = models.JSONField(default=list, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=80, default='等待连接')


class KookBinding(models.Model):
    account = models.OneToOneField('club_accounts.ClubAccount', on_delete=models.PROTECT)
    user_id = models.CharField(max_length=64, unique=True)
    nickname = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    channel_id = models.CharField(max_length=64, blank=True)
    accounted_at = models.DateTimeField(null=True, blank=True)
    event_at = models.DateTimeField(null=True, blank=True)
    online_milliseconds = models.PositiveBigIntegerField(default=0)
    awarded_points = models.PositiveBigIntegerField(default=0)


class KookBindingChallenge(models.Model):
    account = models.OneToOneField('club_accounts.ClubAccount', on_delete=models.CASCADE)
    code_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()


class KookPointEvent(models.Model):
    event_id = models.CharField(max_length=100, unique=True)
    received_at = models.DateTimeField(auto_now_add=True)


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
