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
    # 标识色(hex，如 #C99A2E)：用于接单工作台按等级渲染卡片底色与徽章；
    # 留空表示不做特殊视觉标记，前端走默认样式。
    color = models.CharField(max_length=9, blank=True, default='')
    remark = models.CharField(max_length=255, blank=True, default='')
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Boss Type'
        verbose_name_plural = 'Boss Types'
        ordering = ['sort_order', 'id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(discount_rate__range=(1, 100)),
                name='boss_type_discount_valid',
            ),
        ]

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
        constraints = [
            models.CheckConstraint(
                condition=models.Q(commission_rate__range=(0, 100)),
                name='escort_level_rate_valid',
            ),
        ]

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
    # 同一微信身份只能绑定一个系统账号。MySQL 的 UNIQUE 允许存在多个 NULL，
    # 因此后台预建但尚未绑定微信的老板仍可正常保留。
    openid = models.CharField(max_length=100, blank=True, null=True, unique=True)
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
    boss_no = models.CharField(max_length=32, blank=True, default='', unique=True)  # 全局人员编号
    can_login = models.BooleanField(default=True)  # 是否允许登录
    can_view = models.BooleanField(default=True)  # 是否允许查看数据
    last_active_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(AbstractUser.Meta):
        constraints = [
            models.CheckConstraint(
                condition=models.Q(role__in=['CUSTOMER', 'PROVIDER', 'OPERATOR', 'ADMIN']),
                name='custom_user_role_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(inviter_commission_rate__range=(0, 100)),
                name='custom_user_inviter_rate_valid',
            ),
        ]
        indexes = [
            models.Index(
                fields=['role', 'can_login', 'is_active'],
                name='custom_user_login_idx',
            ),
        ]

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
        null=True, blank=True,
    )
    account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='game_profiles',
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
            models.CheckConstraint(condition=models.Q(account__isnull=False) | models.Q(user__isnull=False),
                                   name='game_profile_has_owner'),
            models.UniqueConstraint(
                fields=['user', 'game_category'], name='uniq_customer_game_profile',
            ),
            models.UniqueConstraint(
                fields=['account', 'game_category'],
                name='uniq_account_game_profile',
            ),
        ]
        ordering = ['game_category__sort_order', 'game_category_id']

    def __str__(self):
        return f'{self.account_id or self.user_id}-{self.game_category.name}'


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
    account = models.OneToOneField(
        'club_accounts.ClubAccount',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='escort_profile',
    )
    display_name = models.CharField(max_length=50)
    recommendation_slot = models.PositiveSmallIntegerField(
        null=True, blank=True, unique=True, choices=[(n, f'推荐位 {n}') for n in range(1, 5)],
    )
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
    # A legacy exemption is evidence of prior activity, not a claim that an
    # old avatar came from WeChat. New providers need the verified timestamp.
    wechat_avatar_legacy_exempt = models.BooleanField(default=False, editable=False)
    wechat_avatar_verified_at = models.DateTimeField(null=True, blank=True, editable=False)
    avatar_source = models.CharField(max_length=12, default='UNKNOWN', editable=False,
        choices=[('UNKNOWN', '未知'), ('WECHAT', '微信'), ('UPLOAD', '本人上传'), ('STAFF_UPLOAD', '后台上传')])
    escort_no = models.CharField(max_length=32, blank=True, default='', db_index=True)  # 陪玩编号
    deposit_required = models.PositiveIntegerField(default=0)  # 应缴押金浪花（内部账务单位）
    deposit_paid = models.PositiveIntegerField(default=0)  # 已缴押金浪花（内部账务单位）
    deposit_business_cycle = models.PositiveIntegerField(null=True, blank=True, editable=False)
    deposit_mode = models.CharField(
        max_length=10, default='CASH', choices=[('CASH', '现金缴纳'), ('EARNINGS', '打单抵扣')],
    )
    total_reward = models.PositiveIntegerField(default=0)  # 累计奖励浪花（内部账务单位）
    total_penalty = models.PositiveIntegerField(default=0)  # 累计罚款浪花（内部账务单位）
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
    # 可接游戏类目：陪玩与游戏为多对多，一个陪玩可接多种游戏，一个游戏可被多名陪玩接单。
    # 下单指定游戏后，仅展示可接该游戏的陪玩；未配置任何游戏的陪玩不会在指定游戏筛选下出现。
    game_categories = models.ManyToManyField(
        'orders.GameCategory', blank=True, related_name='escorts',
    )
    # 可接服务项：陪玩与服务项为多对多，粒度比游戏更细（如"三角洲·排位上分"）。
    # 陪玩勾选服务项即隐含可接其所属游戏；下单指定服务项后仅展示可接该服务项的陪玩。
    service_items = models.ManyToManyField(
        'orders.ServiceItem', blank=True, related_name='escorts',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    dispatch_notification_preferences = models.JSONField(default=dict, db_default={}, blank=True, verbose_name='派单通知偏好')

    class Meta:
        verbose_name = 'Escort Profile'
        verbose_name_plural = 'Escort Profiles'
        constraints = [
            models.CheckConstraint(
                condition=models.Q(win_rate__range=(0, 100)),
                name='escort_profile_win_rate_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(recommendation_slot__isnull=True) | models.Q(recommendation_slot__range=(1, 4)),
                name='escort_recommendation_slot_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(rating_avg__range=(0, 5)),
                name='escort_profile_rating_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=['AVAILABLE', 'BUSY', 'OFFLINE']),
                name='escort_profile_status_valid',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(pass_tier='')
                    | models.Q(pass_tier__in=['BLACK', 'GOLD', 'SILVER', 'BRONZE'])
                ),
                name='escort_profile_pass_tier_valid',
            ),
        ]

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
        """当前通行证档位在公共单池中的可见延迟（秒）。

        取值下沉到 ``SystemConfig``（``wallet.get_pass_delay_seconds``），
        运营调档不再需要发版。此前这张表在模型、视图里各硬编码了一份，
        改一处漏一处就会出现「购买页显示提前 30 秒，实际抢单池按 60 秒放行」。

        延迟导入 wallet：避免 users 与 wallet 在 app 加载期互相牵引。
        """
        from wallet.models import get_pass_delay_seconds

        return get_pass_delay_seconds(self.active_pass_tier)

    @property
    def level_rank(self):
        """陪玩档位高低数值：未设档位视为最低(-1)。值越大档位越高。"""
        if self.level_id is None or self.level is None or not self.level.is_active:
            return -1
        return self.level.sort_order

    def can_take_service(self, service) -> bool:
        """是否满足服务项要求的最低档位：陪玩档位 >= 服务要求档位。

        服务未绑定档位(required_level 为空)时视为不限档，任何陪玩可接。
        """
        from .qualifications import service_requirement, matching_qualification
        requirement=service_requirement(service)
        if requirement is not None:
            return matching_qualification(self,requirement) is not None
        required = getattr(getattr(service,'service',service), 'required_level', None)
        if required is None:
            return True
        return self.level_rank >= required.sort_order

    def open_refund_deficits(self, *, lock=False):
        from wallet.models import RefundDepositDeficit
        rows = RefundDepositDeficit.objects.filter(account_id=self.account_id, remaining__gt=0)
        if lock:
            rows = rows.select_for_update().order_by('created_at', 'pk')
        return list(rows.values('profile_id', 'remaining', 'created_at'))

    def order_acceptance_error(self, service=None, *, club_rules_enabled=None, provider_gender="ANY", allow_refund_recovery=False,
                               refund_recovery_order_id=None, deposit_required_override=None, reservation_confirmed_at=None,
                               lock_refund_recovery=False, require_current_schedule=True, refund_deficits=None):
        """老板指定、客服派单和抢单共用准入；BUSY 不替代并发名额校验。"""
        account = self.account if self.account_id else None
        if (account and (not account.is_active or not account.can_login)) or not self.user.is_active:
            return '陪玩账号已停用'
        # A retired account may still have an active login/profile. Never
        # offer it a new assignment until re-entry has passed the quota gate.
        # A previously confirmed reservation is a separate commitment; its
        # conversion validates the recorded participants and cycles itself.
        if account and not account.license_membership_active and reservation_confirmed_at is None:
            return '陪玩已退出门店，请重新入驻后接单'
        if not self.is_verified:
            return '陪玩尚未通过认证'
        from django.conf import settings
        if settings.PROVIDER_WECHAT_GATE_ENABLED:
            reason = self.wechat_acceptance_error()
            if reason:
                return reason
        from wallet.refund_deposit import protection_enabled
        recovery_eligible = False
        if protection_enabled() and self.account_id:
            deficits = (self.open_refund_deficits(lock=lock_refund_recovery)
                        if lock_refund_recovery or refund_deficits is None else refund_deficits)
            if deficits:
                if (reservation_confirmed_at is not None
                        and all(row['profile_id'] == self.pk and row['created_at'] > reservation_confirmed_at
                                for row in deficits)
                        and (self.deposit_mode == 'EARNINGS' or
                             max((self.effective_deposit_required if deposit_required_override is None
                                  else deposit_required_override) - self.deposit_paid, 0) <=
                             sum(row['remaining'] for row in deficits))):
                    recovery_eligible = True
                elif not allow_refund_recovery:
                    return '退款押金缺口待补，补差接单功能尚未开放'
                else:
                    from wallet.refund_recovery_order import eligibility_error
                    reason = eligibility_error(self, existing_order_id=refund_recovery_order_id,
                                               lock_current=lock_refund_recovery)
                    if reason:
                        return reason
                    recovery_eligible = True
        if self.deposit_mode != 'EARNINGS':
            required = self.effective_deposit_required if deposit_required_override is None else deposit_required_override
            if self.deposit_paid < required and not recovery_eligible:
                return '陪玩押金未缴齐'
        if self.status == self.Status.OFFLINE:
            return '陪玩当前处于离线状态'
        # A confirmed future reservation holds its own time slot. Paying it
        # does not require the provider to be on an ordinary shift right now.
        if (require_current_schedule and reservation_confirmed_at is None
                and not EscortSchedule.provider_is_scheduled_now(self.user)):
            return '当前时间不在陪玩的接单档期内'
        if provider_gender != 'ANY' and self.gender != provider_gender:
            return '陪玩性别不符合老板选择，请重新选择陪玩'
        if service and not self.can_take_service(service):
            return '陪玩未开通对应端手游、难度或档位的接单资格'
        # 已归类游戏按具体技能准入；历史未归类服务继续使用原有准入规则。
        from .qualifications import service_skill
        source=getattr(service,'service',service)
        if source and source.game_category_id and not service_skill(self,service):
            return '陪玩尚未配置该服务技能'
        return ''

    def wechat_acceptance_error(self):
        account = self.account if self.account_id else None
        if not account or not account.is_openid_bound:
            return '请先绑定本人微信后接单'
        if not account.openid:
            has_identity = getattr(self, 'has_wechat_identity', None)
            if has_identity is None:
                has_identity = account.wechat_identities.exists()
            if not has_identity:
                return '请先绑定本人微信后接单'
        if not (self.wechat_avatar_legacy_exempt or self.wechat_avatar_verified_at):
            return '首次接单前请从本人微信获取头像'
        return ''

    @property
    def effective_deposit_required(self):
        from wallet.club_rules import deposit_target
        return deposit_target(self.deposit_mode)


class ProviderQualification(models.Model):
    profile = models.ForeignKey(EscortProfile, on_delete=models.CASCADE, related_name='qualifications')
    game_category = models.ForeignKey('orders.GameCategory', on_delete=models.PROTECT, related_name='provider_qualifications')
    provider_kind = models.CharField(max_length=12, choices=[('GUARD','护航'),('COMPANION','陪玩'),('GRIND','代肝'),('RANK','上分')])
    platform = models.CharField(max_length=10, choices=[('PC','端游'),('MOBILE','手游')])
    security_level = models.CharField(max_length=15, choices=[('CONFIDENTIAL','机密'),('TOP_SECRET','绝密'),('ANY','不限难度')])
    grade = models.CharField(max_length=16, choices=[('ENTERTAINMENT','娱乐'),('TECHNICAL','技术'),('MASTER','大师'),('KING','王者'),('LEGEND','传奇'),('GLORY','荣耀'),('STAR','明星'),('OPEN','不分档')])
    price_per_hour = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['game_category_id','provider_kind','platform','security_level','id']
        constraints = [models.UniqueConstraint(fields=['profile','game_category','provider_kind','platform','security_level'],name='unique_provider_qualification_scope')]

    def clean(self):
        from django.core.exceptions import ValidationError
        from .qualifications import grade_options
        if self.grade not in grade_options(self.provider_kind,self.platform,self.security_level):
            raise ValidationError({'grade':'该资格范围不支持此档位'})


class ProviderPassPurchase(models.Model):
    provider = models.ForeignKey(
        'users.CustomUser', on_delete=models.CASCADE, related_name='pass_purchases',
    )
    account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='pass_purchases',
    )
    business_cycle = models.PositiveIntegerField(null=True, blank=True, editable=False)
    tier = models.CharField(max_length=10, choices=EscortProfile.PassTier.choices)
    days = models.PositiveSmallIntegerField()
    daily_price = models.PositiveIntegerField()  # 分
    original_amount = models.PositiveIntegerField()  # 分
    discount_amount = models.PositiveIntegerField(default=0)  # 分
    point_discount_amount = models.PositiveIntegerField(default=0)  # 积分抵扣等值，内部账务单位
    paid_amount = models.PositiveIntegerField()  # 分
    starts_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    transaction = models.ForeignKey(
        'wallet.Transaction', on_delete=models.PROTECT, related_name='pass_purchases',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(
                fields=['account', '-created_at'],
                name='pass_purchase_account_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(days__gte=1),
                name='pass_purchase_days_positive',
            ),
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F('starts_at')),
                name='pass_purchase_period_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(discount_amount__lte=models.F('original_amount')),
                name='pass_purchase_discount_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    paid_amount=models.F('original_amount') - models.F('discount_amount') - models.F('point_discount_amount')
                ),
                name='pass_purchase_amount_valid',
            ),
        ]


class ProviderPassPointRedemption(models.Model):
    """Immutable payment facts for points spent on a pass purchase."""
    purchase = models.OneToOneField(ProviderPassPurchase, on_delete=models.PROTECT, related_name='point_redemption')
    ledger = models.OneToOneField('engagement.PointLedger', on_delete=models.PROTECT, related_name='pass_redemption')
    points_hundredths = models.PositiveBigIntegerField()
    discount_units = models.PositiveIntegerField()
    group_points_hundredths = models.PositiveIntegerField()
    group_coin_units = models.PositiveIntegerField()
    max_ratio_basis_points = models.PositiveSmallIntegerField()
    config_version = models.PositiveIntegerField()
    groups = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(points_hundredths__gt=0, discount_units__gt=0,
                group_points_hundredths__gt=0, group_coin_units__gt=0, groups__gt=0, config_version__gt=0,
                max_ratio_basis_points__lt=10000), name='pass_points_values_positive'),
            models.CheckConstraint(condition=models.Q(points_hundredths=models.F('groups') * models.F('group_points_hundredths')),
                                   name='pass_points_groups_match'),
            models.CheckConstraint(condition=models.Q(discount_units=models.F('groups') * models.F('group_coin_units')),
                                   name='pass_points_value_match'),
        ]


class ProviderPassRefund(models.Model):
    """一次历史通行证退款及其门店资金缺口，不修改原始付款流水。"""
    purchase = models.OneToOneField(
        ProviderPassPurchase, on_delete=models.PROTECT, related_name='refund',
    )
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT)
    amount = models.PositiveIntegerField()
    funded_amount = models.PositiveIntegerField()
    shortfall_amount = models.PositiveIntegerField()
    points_returned = models.PositiveBigIntegerField(default=0)
    points_return_ledger = models.OneToOneField('engagement.PointLedger', on_delete=models.PROTECT,
        null=True, blank=True, related_name='pass_refund_return')
    provider_transaction = models.OneToOneField(
        'wallet.Transaction', on_delete=models.PROTECT, related_name='pass_refund',
    )
    shop_transaction = models.OneToOneField(
        'wallet.Transaction', on_delete=models.PROTECT, null=True, blank=True,
        related_name='shop_pass_refund',
    )
    original_starts_at = models.DateTimeField()
    original_expires_at = models.DateTimeField()
    # Audit of each later purchase before and after schedule adjustment.
    schedule_changes = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount=models.F('funded_amount') + models.F('shortfall_amount')),
                name='pass_refund_amount_conserved',
            ),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='pass_refund_amount_positive'),
            models.CheckConstraint(
                condition=(models.Q(funded_amount=0, shop_transaction__isnull=True)
                           | models.Q(funded_amount__gt=0, shop_transaction__isnull=False)),
                name='pass_refund_shop_tx_matches_fund',
            ),
            models.CheckConstraint(condition=(models.Q(points_returned=0, points_return_ledger__isnull=True)
                                   | models.Q(points_returned__gt=0, points_return_ledger__isnull=False)),
                                   name='pass_refund_points_match_ledger'),
        ]


class PassRefundRepaymentReceipt(models.Model):
    """Store-scoped evidence of an offline payment to the platform advance account."""
    request_key = models.CharField(max_length=64, unique=True)
    external_reference = models.CharField(max_length=128, unique=True)
    amount = models.PositiveIntegerField()  # Internal units, not store-wallet balance.
    received_at = models.DateTimeField()
    evidence = models.TextField()
    registered_by = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    verified_by = models.CharField(max_length=64, blank=True, default='')
    verified_at = models.DateTimeField(null=True, blank=True)
    # NULL means the experimental receipt-local ledger is not initialized.
    usage_version = models.PositiveBigIntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='pass_repay_receipt_positive'),
            models.CheckConstraint(condition=(models.Q(verified_at__isnull=True, verified_by='')
                | (models.Q(verified_at__isnull=False) & ~models.Q(verified_by=''))),
                name='pass_repay_receipt_verify_pair'),
        ]


class PassRefundRepaymentAllocation(models.Model):
    """Append-only allocation of verified receipt funds to one refund shortfall."""
    request_key = models.CharField(max_length=64, unique=True)
    receipt = models.ForeignKey(PassRefundRepaymentReceipt, on_delete=models.PROTECT, related_name='allocations')
    refund = models.ForeignKey(ProviderPassRefund, on_delete=models.PROTECT, related_name='repayment_allocations')
    amount = models.PositiveIntegerField()
    allocated_by = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name='pass_repay_allocation_positive')]


class PassRefundRepaymentAction(models.Model):
    """Tenant-side durable result for a platform verification or allocation."""
    request_key = models.CharField(max_length=64, unique=True)
    kind = models.CharField(max_length=12)
    receipt = models.ForeignKey(PassRefundRepaymentReceipt, on_delete=models.PROTECT)
    allocation = models.OneToOneField(PassRefundRepaymentAllocation, on_delete=models.PROTECT, null=True, blank=True)
    actor = models.CharField(max_length=64)
    result = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=(
            models.Q(kind='VERIFY', allocation__isnull=True) | models.Q(kind='ALLOCATE', allocation__isnull=False)),
            name='pass_repay_action_kind_link')]


class CheckinRecord(models.Model):
    user = models.ForeignKey(
        'users.CustomUser',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='checkin_records',
    )
    account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='checkin_records',
    )
    checkin_date = models.DateField(db_index=True)
    seq_in_month = models.PositiveIntegerField()  # 本月第几次签到，决定奖励档位
    reward_amount = models.IntegerField(default=0)  # 奖励浪花（内部账务单位）
    gift_name = models.CharField(max_length=100, blank=True, default='')
    gift_icon = models.CharField(max_length=20, blank=True, default='')
    is_makeup = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Checkin Record'
        verbose_name_plural = 'Checkin Records'
        unique_together = ('user', 'checkin_date')
        ordering = ['-checkin_date']
        constraints = [
            models.UniqueConstraint(
                fields=['account', 'checkin_date'],
                name='uniq_account_checkin_date',
            ),
            models.CheckConstraint(
                condition=models.Q(seq_in_month__range=(1, 31)),
                name='checkin_sequence_valid',
            ),
        ]
        indexes = [
            models.Index(
                fields=['account', '-checkin_date'],
                name='checkin_account_date_idx',
            ),
        ]

    def __str__(self) -> str:
        return f"{self.account.username if self.account_id else self.user.username} - {self.checkin_date}"


class CheckinRuleConfig(models.Model):
    """签到全局规则。业务只使用 pk=1 的单例配置。"""

    # 188/388 元按 1:10 兑换为 1880/3880 浪花；账务层再以 10 单位表示 1 浪花。
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
        constraints = [
            models.CheckConstraint(
                condition=models.Q(checkin_day__range=(1, 31)),
                name='checkin_gift_day_valid',
            ),
        ]

    def __str__(self):
        return f'第{self.checkin_day}天 - {self.name}'


class CheckinMonthProgress(models.Model):
    """老板每月补签卡库存与全勤奖励状态；按年月自然隔离即自动月度重置。"""

    user = models.ForeignKey(
        'users.CustomUser', on_delete=models.CASCADE, related_name='checkin_month_progresses', null=True, blank=True,
    )
    account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='checkin_month_progresses',
    )
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    makeup_cards = models.PositiveSmallIntegerField(default=0)
    card_grant_days = models.JSONField(default=list, blank=True)
    full_attendance_awarded = models.BooleanField(default=False)
    full_attendance_reward_name = models.CharField(max_length=100, blank=True, default='')
    full_attendance_tag_code = models.CharField(max_length=100, blank=True, default='')
    full_attendance_awarded_at = models.DateTimeField(null=True, blank=True)
    full_attendance_notice_intent = models.BooleanField(default=False, editable=False)
    full_attendance_notice_cycle = models.PositiveIntegerField(null=True, blank=True, editable=False)
    full_attendance_notice_detail = models.TextField(blank=True, default='', editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'year', 'month')
        ordering = ['-year', '-month', '-updated_at']
        constraints = [
            models.UniqueConstraint(
                fields=['account', 'year', 'month'],
                name='uniq_account_checkin_month',
            ),
            models.CheckConstraint(
                condition=models.Q(year__gte=2000),
                name='checkin_month_year_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(month__range=(1, 12)),
                name='checkin_month_value_valid',
            ),
        ]

    def __str__(self):
        return f'{self.account.username if self.account_id else self.user.username} {self.year}-{self.month:02d}'


class Achievement(models.Model):
    """老板成就配置；解锁状态按本店已确认的服务实销计算。"""

    class Metric(models.TextChoices):
        ORDERS = 'orders', '完成订单数'
        AMOUNT = 'amount', '累计消费（浪花）'

    code = models.CharField(max_length=50, unique=True)
    title = models.CharField(max_length=50)
    desc = models.CharField(max_length=100, blank=True, default='')
    icon = models.CharField(max_length=20, blank=True, default='')
    metric = models.CharField(
        max_length=16, choices=Metric.choices, default=Metric.ORDERS
    )
    target = models.PositiveIntegerField()  # amount 维度为浪花内部账务单位
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
    时段以当日分钟数表示；结束早于开始时表示次日结束。
    结束为 1440 表示当日 24:00；旧四段档期保持原语义。
    """

    provider = models.ForeignKey(
        'users.CustomUser',
        on_delete=models.CASCADE,
        related_name='schedules',
    )
    account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='schedules',
    )
    weekday = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(6)]
    )  # 0=周一 … 6=周日
    start_minute = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(1439)]
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
        constraints = [
            models.UniqueConstraint(
                fields=['account', 'weekday', 'start_minute', 'end_minute'],
                name='uniq_account_schedule_slot',
            ),
            models.CheckConstraint(
                condition=models.Q(weekday__range=(0, 6)),
                name='escort_schedule_weekday_valid',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(start_minute__gte=0)
                    & models.Q(start_minute__lt=1440)
                    & models.Q(end_minute__gte=0)
                    & models.Q(end_minute__lte=1440)
                    & ~models.Q(start_minute=models.F('end_minute'))
                ),
                name='escort_schedule_period_valid',
            ),
        ]
        indexes = [
            models.Index(
                fields=['account', 'weekday', 'start_minute', 'end_minute'],
                name='schedule_account_slot_idx',
            ),
        ]

    @classmethod
    def provider_is_scheduled_now(cls, provider, now=None) -> bool:
        """未配置档期视为不限制；配置后仅允许在命中的周时段接单。"""
        from django.utils import timezone

        now = timezone.localtime(now or timezone.now())
        cached = getattr(provider, '_prefetched_objects_cache', {}).get('schedules')
        if cached is not None:
            minute = now.hour * 60 + now.minute
            return not cached or any(
                (row.weekday == now.weekday() and row.start_minute <= minute
                 and (row.end_minute > minute or row.end_minute < row.start_minute))
                or (row.weekday == (now.weekday() - 1) % 7
                    and row.end_minute < row.start_minute and minute < row.end_minute)
                for row in cached
            )
        schedules = cls.objects.filter(provider=provider)
        minute = now.hour * 60 + now.minute
        today = models.Q(weekday=now.weekday(), start_minute__lte=minute) & (
            models.Q(end_minute__gt=minute) | models.Q(end_minute__lt=models.F('start_minute')))
        previous = models.Q(weekday=(now.weekday() - 1) % 7,
            end_minute__lt=models.F('start_minute'), end_minute__gt=minute)
        # One database snapshot distinguishes "no schedule" from "no match";
        # two separate exists() calls can race a whole-schedule replacement.
        counts = schedules.aggregate(total=models.Count('pk'), matching=models.Count('pk', filter=today | previous))
        return counts['total'] == 0 or counts['matching'] > 0

    @classmethod
    def provider_covers_window(cls, provider, start, end, *, lock=False):
        """Whether weekly availability covers the full half-open booking slot."""
        from datetime import datetime, time, timedelta
        from django.utils import timezone
        if not start or not end or timezone.is_naive(start) or timezone.is_naive(end) or start >= end:
            raise ValueError('预约时段无效')
        query = cls.objects.filter(provider=provider).order_by('weekday', 'start_minute', 'pk')
        if lock:
            query = query.select_for_update()
        slots = list(query)
        if not slots:
            return True  # Match the existing unconfigured-schedule semantics.
        start, end = timezone.localtime(start), timezone.localtime(end)
        day = start.date() - timedelta(days=1)
        intervals = []
        while day <= end.date():
            midnight = timezone.make_aware(datetime.combine(day, time.min), timezone.get_current_timezone())
            for slot in slots:
                if slot.weekday != day.weekday():
                    continue
                begins = midnight + timedelta(minutes=slot.start_minute)
                finishes = midnight + timedelta(minutes=slot.end_minute)
                if slot.end_minute < slot.start_minute:
                    finishes += timedelta(days=1)
                if begins < end and finishes > start:
                    intervals.append((begins, finishes))
            day += timedelta(days=1)
        cursor = start
        for begins, finishes in sorted(intervals):
            if begins > cursor:
                break
            if finishes > cursor:
                cursor = finishes
            if cursor >= end:
                return True
        return False

    def __str__(self) -> str:
        return f"{self.provider_id} - w{self.weekday} {self.start_minute}-{self.end_minute}"


class WechatPhoneLock(models.Model):
    """Stable lock even before a phone has an owner; use only inside atomic()."""

    phone = models.CharField(max_length=20, primary_key=True)

    class Meta:
        verbose_name = '微信手机号绑定锁'
        verbose_name_plural = '微信手机号绑定锁'


from .registration_models import ProviderRegistration  # noqa: E402,F401
from .boss_registration_models import BossRegistrationChallenge  # noqa: E402,F401
from .web_login_models import WebLoginSession, PasswordLoginChallenge  # noqa: E402,F401
from .voice_models import VoiceRecording  # noqa: E402,F401
from .receipt_usage_models import PassReceiptUsageEvent, PassReceiptUsageState  # noqa: E402,F401
