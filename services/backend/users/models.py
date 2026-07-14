from django.contrib.auth.models import AbstractUser
from django.core.validators import MaxValueValidator, MinValueValidator
from django.core.validators import FileExtensionValidator
from django.db import models

import random


def generate_user_no() -> str:
    """生成全系统人员编号：大写 XA + 6 位随机数字（如 XA042317）。

    通过查库校验唯一，冲突则重新生成（生成时重试策略）。
    """
    while True:
        code = 'XA' + f'{random.randint(0, 999999):06d}'
        if not CustomUser.objects.filter(boss_no=code).exists():
            return code


class BossType(models.Model):
    """老板分级：不同等级享受不同下单折扣。"""

    name = models.CharField(max_length=50, unique=True)
    discount_rate = models.PositiveSmallIntegerField(
        default=100, validators=[MinValueValidator(1), MaxValueValidator(100)]
    )  # 折扣率(%)，100=原价，90=九折
    remark = models.CharField(max_length=255, blank=True, default='')
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Boss Type'
        verbose_name_plural = 'Boss Types'
        ordering = ['sort_order', 'id']

    def __str__(self) -> str:
        return f"{self.name}({self.discount_rate}%)"


class EscortLevel(models.Model):
    """陪玩（打手）等级：不同等级对应不同的平台抽成率。

    抽成最终来源优先级：活动 > 陪玩等级 > 店铺(商品级/全局)。
    本轮等级由后台手动设置（EscortProfile.level），不做自动升级。
    """

    name = models.CharField(max_length=50, unique=True)
    commission_rate = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(100)]
    )  # 该等级平台抽成率(%)
    remark = models.CharField(max_length=255, blank=True, default='')
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Escort Level'
        verbose_name_plural = 'Escort Levels'
        ordering = ['sort_order', 'id']

    def __str__(self) -> str:
        return f"{self.name}({self.commission_rate}%)"


class CustomUser(AbstractUser):
    class Role(models.TextChoices):
        CUSTOMER = 'CUSTOMER', 'Customer'
        PROVIDER = 'PROVIDER', 'Provider'
        OPERATOR = 'OPERATOR', 'Operator'
        ADMIN = 'ADMIN', 'Admin'

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.CUSTOMER,
        db_index=True,
    )
    openid = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    phone = models.CharField(max_length=20, blank=True, null=True, db_index=True)
    avatar = models.ImageField(upload_to='customers/avatars/', blank=True, null=True)
    avatar_url = models.URLField(blank=True, null=True)
    nickname = models.CharField(max_length=50, blank=True, default='')
    real_name = models.CharField(max_length=50, blank=True, default='')
    is_phone_verified = models.BooleanField(default=False)
    is_openid_bound = models.BooleanField(default=False)
    # 老板常用游戏资料：下单时快速回填，可在账号设置页维护
    game_region = models.CharField(max_length=50, blank=True, default='')
    game_nickname = models.CharField(max_length=50, blank=True, default='')
    game_uid = models.CharField(max_length=50, blank=True, default='')
    # 推荐人体系：老板由谁推荐，推荐人按其订单平台抽成分佣
    inviter = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='invitees'
    )
    inviter_commission_rate = models.PositiveSmallIntegerField(
        default=0, validators=[MinValueValidator(0), MaxValueValidator(100)]
    )  # 推荐人分佣率(%)，按平台抽成部分计提
    # 老板分级与编号
    boss_type = models.ForeignKey(
        'users.BossType', on_delete=models.SET_NULL, null=True, blank=True, related_name='bosses'
    )
    boss_no = models.CharField(max_length=32, blank=True, default='', db_index=True)  # 老板编号
    can_login = models.BooleanField(default=True)  # 是否允许登录
    can_view = models.BooleanField(default=True)  # 是否允许查看数据
    last_active_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        # 全系统人员统一编号：boss_no 为空时自动生成 XA+6 位随机数字
        if not self.boss_no:
            self.boss_no = generate_user_no()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.nickname or self.username


class CustomerGameProfile(models.Model):
    """老板按后台游戏类目维护的常用账号资料。"""

    user = models.ForeignKey(
        'users.CustomUser', on_delete=models.CASCADE, related_name='game_profiles',
    )
    game_category = models.ForeignKey(
        'orders.GameCategory', on_delete=models.PROTECT, related_name='customer_profiles',
    )
    region = models.CharField(max_length=50, blank=True, default='')
    nickname = models.CharField(max_length=50, blank=True, default='')
    uid = models.CharField(max_length=50, blank=True, default='')
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'game_category'], name='uniq_customer_game_profile',
            ),
        ]
        ordering = ['game_category__sort_order', 'game_category_id']

    def __str__(self):
        return f'{self.user_id}-{self.game_category.name}'


class EscortProfile(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = 'AVAILABLE', 'Available'
        BUSY = 'BUSY', 'Busy'
        OFFLINE = 'OFFLINE', 'Offline'

    class Gender(models.TextChoices):
        MALE = 'MALE', '男陪'
        FEMALE = 'FEMALE', '女陪'
        UNKNOWN = 'UNKNOWN', '未知'

    class PassTier(models.TextChoices):
        BLACK = 'BLACK', '黑卡通行证'
        GOLD = 'GOLD', '金卡通行证'
        SILVER = 'SILVER', '银卡通行证'
        BRONZE = 'BRONZE', '铜卡通行证'

    user = models.OneToOneField(
        'users.CustomUser',
        on_delete=models.CASCADE,
        related_name='escort_profile',
    )
    display_name = models.CharField(max_length=50)
    gender = models.CharField(
        max_length=10, choices=Gender.choices, default=Gender.UNKNOWN, db_index=True
    )
    avatar = models.ImageField(
        upload_to='escorts/avatars/', blank=True, null=True
    )  # 陪玩头像
    intro_video = models.FileField(
        upload_to='escorts/videos/', blank=True, null=True
    )  # 视频介绍
    voice_card = models.FileField(
        upload_to='escorts/voice_cards/', blank=True, null=True,
        validators=[FileExtensionValidator(
            allowed_extensions=['mp3', 'm4a', 'wav', 'aac', 'ogg']
        )],
    )  # 陪玩语音卡
    cheat_proof = models.ImageField(
        upload_to='escorts/cheat_proofs/', blank=True, null=True
    )  # 查外挂依据图
    bio = models.TextField(blank=True, default='')
    city = models.CharField(max_length=50, blank=True, default='')
    service_area = models.CharField(max_length=255, blank=True, default='')
    price_per_hour = models.PositiveIntegerField(blank=True, null=True)
    rank_tier = models.CharField(max_length=50, blank=True, default='')
    level = models.ForeignKey(
        'users.EscortLevel', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='escorts',
    )  # 陪玩等级，影响平台抽成率
    win_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.AVAILABLE,
        db_index=True,
    )
    is_verified = models.BooleanField(default=False)
    escort_no = models.CharField(max_length=32, blank=True, default='', db_index=True)  # 陪玩编号
    deposit_required = models.PositiveIntegerField(default=0)  # 应缴押金兴安币（内部账务单位）
    deposit_paid = models.PositiveIntegerField(default=0)  # 已缴押金兴安币（内部账务单位）
    total_reward = models.PositiveIntegerField(default=0)  # 累计奖励兴安币（内部账务单位）
    total_penalty = models.PositiveIntegerField(default=0)  # 累计罚款兴安币（内部账务单位）
    rating_avg = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(5)],
    )
    rating_count = models.PositiveIntegerField(default=0)
    completed_order_count = models.PositiveIntegerField(default=0)
    pass_tier = models.CharField(
        max_length=10, choices=PassTier.choices, blank=True, default='', db_index=True,
    )
    pass_expires_at = models.DateTimeField(blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Escort Profile'
        verbose_name_plural = 'Escort Profiles'

    def save(self, *args, **kwargs):
        # 陪玩编号与该用户的全局编号保持一致（一人一码）
        if not self.escort_no and self.user_id:
            self.escort_no = self.user.boss_no
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.display_name

    @property
    def active_pass_tier(self):
        from django.utils import timezone
        if self.pass_tier and self.pass_expires_at and self.pass_expires_at > timezone.now():
            return self.pass_tier
        return ''

    @property
    def order_visibility_delay_seconds(self):
        return {
            self.PassTier.BLACK: 0,
            self.PassTier.GOLD: 30,
            self.PassTier.SILVER: 60,
            self.PassTier.BRONZE: 120,
            '': 300,
        }[self.active_pass_tier]


class ProviderPassPurchase(models.Model):
    provider = models.ForeignKey(
        'users.CustomUser', on_delete=models.CASCADE, related_name='pass_purchases',
    )
    tier = models.CharField(max_length=10, choices=EscortProfile.PassTier.choices)
    days = models.PositiveSmallIntegerField()
    daily_price = models.PositiveIntegerField()  # 分
    original_amount = models.PositiveIntegerField()  # 分
    discount_amount = models.PositiveIntegerField(default=0)  # 分
    paid_amount = models.PositiveIntegerField()  # 分
    starts_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    transaction = models.ForeignKey(
        'wallet.Transaction', on_delete=models.PROTECT, related_name='pass_purchases',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class CheckinRecord(models.Model):
    user = models.ForeignKey(
        'users.CustomUser',
        on_delete=models.CASCADE,
        related_name='checkin_records',
    )
    checkin_date = models.DateField(db_index=True)
    seq_in_month = models.PositiveIntegerField()  # 本月第几次签到，决定奖励档位
    reward_amount = models.IntegerField(default=0)  # 奖励兴安币（内部账务单位）
    gift_name = models.CharField(max_length=100, blank=True, default='')
    gift_icon = models.CharField(max_length=20, blank=True, default='')
    is_makeup = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Checkin Record'
        verbose_name_plural = 'Checkin Records'
        unique_together = ('user', 'checkin_date')
        ordering = ['-checkin_date']

    def __str__(self) -> str:
        return f"{self.user.username} - {self.checkin_date}"


class CheckinRuleConfig(models.Model):
    """签到全局规则。业务只使用 pk=1 的单例配置。"""

    # 188/388 元按 1:10 兑换为 1880/3880 兴安币；账务层再以 10 单位表示 1 兴安币。
    daily_spend_required = models.PositiveIntegerField(default=18800)
    makeup_card_spend_required = models.PositiveIntegerField(default=38800)
    max_makeup_cards = models.PositiveSmallIntegerField(default=3)
    full_attendance_reward_name = models.CharField(max_length=100, default='KOOK专属Tag')
    full_attendance_reward_desc = models.CharField(
        max_length=255, blank=True, default='当月完成全部签到后自动获得',
    )
    full_attendance_tag_code = models.CharField(max_length=100, default='XA-MONTH-FULL')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Checkin Rule Config'
        verbose_name_plural = 'Checkin Rule Config'


class CheckinGift(models.Model):
    """按当月累计签到天数配置的礼物。"""

    checkin_day = models.PositiveSmallIntegerField(unique=True)
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True, default='')
    icon = models.CharField(max_length=20, blank=True, default='🎁')
    reward_amount = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['checkin_day']

    def __str__(self):
        return f'第{self.checkin_day}天 - {self.name}'


class CheckinMonthProgress(models.Model):
    """老板每月补签卡库存与全勤奖励状态；按年月自然隔离即自动月度重置。"""

    user = models.ForeignKey(
        'users.CustomUser', on_delete=models.CASCADE, related_name='checkin_month_progresses',
    )
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    makeup_cards = models.PositiveSmallIntegerField(default=0)
    card_grant_days = models.JSONField(default=list, blank=True)
    full_attendance_awarded = models.BooleanField(default=False)
    full_attendance_reward_name = models.CharField(max_length=100, blank=True, default='')
    full_attendance_tag_code = models.CharField(max_length=100, blank=True, default='')
    full_attendance_awarded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'year', 'month')
        ordering = ['-year', '-month', '-updated_at']

    def __str__(self):
        return f'{self.user.username} {self.year}-{self.month:02d}'


class Achievement(models.Model):
    """老板成就配置：后台可定制，解锁状态按已完成订单实时计算。"""

    class Metric(models.TextChoices):
        ORDERS = 'orders', '完成订单数'
        AMOUNT = 'amount', '累计消费（兴安币）'

    code = models.CharField(max_length=50, unique=True)
    title = models.CharField(max_length=50)
    desc = models.CharField(max_length=100, blank=True, default='')
    icon = models.CharField(max_length=20, blank=True, default='')
    metric = models.CharField(
        max_length=16, choices=Metric.choices, default=Metric.ORDERS
    )
    target = models.PositiveIntegerField()  # amount 维度为兴安币内部账务单位
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Achievement'
        verbose_name_plural = 'Achievements'
        ordering = ['sort_order', 'id']

    def __str__(self) -> str:
        return f"{self.code} - {self.title}"


class EscortSchedule(models.Model):
    """陪玩每周循环可接单时段。

    采用「每周循环」模型：weekday 0=周一 … 6=周日，
    时段以当日分钟数表示（start_minute/end_minute，0~1440）。
    前端按四段制勾选（00-06 / 06-12 / 12-18 / 18-24），
    每勾选一段落库一条；PUT 整表覆盖。
    """

    provider = models.ForeignKey(
        'users.CustomUser',
        on_delete=models.CASCADE,
        related_name='schedules',
    )
    weekday = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(6)]
    )  # 0=周一 … 6=周日
    start_minute = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(1440)]
    )
    end_minute = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(1440)]
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Escort Schedule'
        verbose_name_plural = 'Escort Schedules'
        unique_together = ('provider', 'weekday', 'start_minute', 'end_minute')
        ordering = ['weekday', 'start_minute']

    @classmethod
    def provider_is_scheduled_now(cls, provider, now=None) -> bool:
        """未配置档期视为不限制；配置后仅允许在命中的周时段接单。"""
        from django.utils import timezone

        now = timezone.localtime(now or timezone.now())
        schedules = cls.objects.filter(provider=provider)
        if not schedules.exists():
            return True
        minute = now.hour * 60 + now.minute
        return schedules.filter(
            weekday=now.weekday(), start_minute__lte=minute, end_minute__gt=minute,
        ).exists()

    def __str__(self) -> str:
        return f"{self.provider_id} - w{self.weekday} {self.start_minute}-{self.end_minute}"
