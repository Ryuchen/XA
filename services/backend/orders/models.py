import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


def default_settlement_mode():
    return getattr(settings, 'ORDER_SETTLEMENT_MODE', 'REPORT_APPROVAL')


class ProviderDispatchLock(models.Model):
    """每名打手一个派单互斥行，生命周期与业务账号一致。"""

    account = models.OneToOneField(
        'club_accounts.ClubAccount', on_delete=models.CASCADE,
        primary_key=True, related_name='dispatch_lock',
    )


class GameCategory(models.Model):
    """游戏类目字典：具体游戏（王者荣耀、和平精英等），供服务项下拉选择。"""

    name = models.CharField(max_length=50, unique=True)
    icon = models.ImageField(
        upload_to='game_categories/',
        blank=True,
        null=True,
        verbose_name='图标',
        help_text='游戏图标，建议使用正方形图片；未上传时前端展示默认图标',
    )
    remark = models.CharField(max_length=255, blank=True, default='')
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self):
        return self.name


class ServiceCategory(models.Model):
    """分类字典：服务性质（陪玩服务/礼品套餐等），is_gift 标记是否礼物类。"""

    name = models.CharField(max_length=50, unique=True)
    order_kind = models.CharField(max_length=16, blank=True, default='', choices=[('', '其他'), ('GUARD', '护航'), ('COMPANION', '陪玩'), ('GRIND', '代肝'), ('RANK', '上分')])
    is_gift = models.BooleanField(default=False, help_text='是否礼物类：影响看板礼物/游戏流水拆分')
    remark = models.CharField(max_length=255, blank=True, default='')
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.order_kind and not self.is_gift:
            self.order_kind = {'护航':'GUARD','陪玩':'COMPANION','陪玩单':'COMPANION','代肝':'GRIND','上分':'RANK'}.get(self.name, '')
        super().save(*args, **kwargs)


class Gameplay(models.Model):
    class Kind(models.TextChoices):
        STANDARD = 'STANDARD', '普通玩法'
        ROULETTE = 'ROULETTE', '转盘单'

    name = models.CharField(max_length=40, unique=True)
    code = models.SlugField(max_length=40, unique=True)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.STANDARD)
    subtitle = models.CharField(max_length=100, blank=True, default='')
    cover_url = models.URLField(blank=True, default='')
    theme = models.CharField(max_length=20, default='roulette', choices=[
        (v, v) for v in ['roulette', 'experience', 'companion', 'fun', 'mission', 'extraction', 'prison', 'custom']
    ])
    rules = models.TextField(blank=True, default='')
    details_html = models.TextField(blank=True, default='', help_text='价目下方的玩法富文本；商品可单独覆盖')
    is_archived = models.BooleanField(default=False, db_index=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sort_order', 'id']

    def __str__(self):
        return self.name


class ServiceItem(models.Model):
    class CommissionPolicy(models.TextChoices):
        LEGACY = 'LEGACY', '原有抽成规则'
        EXPERIENCE = 'EXPERIENCE', '体验单：低于100元抽3元，100元及以上抽10%'
        COMPANION = 'COMPANION', '陪玩单：20%'
        REGULAR = 'REGULAR', '常规单：陪玩等级抽成'
        FUN = 'FUN', '趣味单：陪玩等级抽成'

    class ExperienceKind(models.TextChoices):
        NONE = '', '普通服务'
        NEW_FIRST = 'NEW_FIRST', '新客首单'
        NEW_VALUE = 'NEW_VALUE', '新客超值'
        DAILY = 'DAILY', '每日一单'
        DAILY_VALUE = 'DAILY_VALUE', '每日超值'

    experience_kind = models.CharField(max_length=16, choices=ExperienceKind.choices, blank=True, default='')
    allows_discount_coupons = models.BooleanField(default=False, help_text='仅开启的普通服务可使用折扣券')
    service_type = models.CharField(max_length=20, default='REGULAR', choices=[('REGULAR', '常规单'), ('ACTIVITY_LIMITED', '活动限定单'), ('PROMOTIONAL', '促销单'), ('COMPANION', '陪玩单'), ('EXPERIENCE', '体验单')])
    benefit_kind = models.CharField(max_length=20, blank=True, default='', choices=[('', '无'), ('COMPANION_HOUR', '小时陪玩'), ('SAFEBOX_33', '33安全箱代肝主套餐')])
    commission_policy = models.CharField(max_length=12, choices=CommissionPolicy.choices, default=CommissionPolicy.LEGACY)
    provider_kind = models.CharField(max_length=12, blank=True, default='', db_default='', choices=[('','未配置'),('GUARD','护航'),('COMPANION','陪玩'),('GRIND','代肝'),('RANK','上分')])
    provider_grade = models.CharField(max_length=16, blank=True, default='', db_default='', choices=[('','不限档位'),('ENTERTAINMENT','娱乐'),('TECHNICAL','技术'),('MASTER','大师'),('KING','王者'),('LEGEND','传奇'),('GLORY','荣耀'),('STAR','明星'),('OPEN','不分档')])
    class PurchaseMode(models.TextChoices):
        DIRECT = 'DIRECT', '直接下单'
        CONSULT = 'CONSULT', '客服确认'

    class Platform(models.TextChoices):
        MOBILE = 'MOBILE', '手游'
        PC = 'PC', '端游'

    class SecurityLevel(models.TextChoices):
        CONFIDENTIAL = 'CONFIDENTIAL', '机密'
        TOP_SECRET = 'TOP_SECRET', '绝密'
        ANY = 'ANY', '不限难度'

    catalog_key = models.CharField(max_length=100, unique=True, null=True, blank=True)
    catalog_source = models.TextField(blank=True, default='')
    catalog_family = models.CharField(max_length=100, blank=True, default='', help_text='同一游戏、目录下相同名称的玩法合并展示')
    catalog_variant = models.CharField(max_length=150, blank=True, default='', help_text='玩法详情内的套餐档位名称')
    price_unit = models.CharField(max_length=20, default='份')
    billing_provider_count = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(2)], help_text='每人计价的双陪套餐为2；已含全部陪玩的总价为1')
    gift_naming_days = models.PositiveIntegerField(default=0, validators=[MaxValueValidator(3650)], help_text='每件礼物累计的冠名天数，0 表示无冠名权益')
    gift_festival = models.CharField(max_length=40, blank=True, default='', help_text='节日限定标识；为空表示常规礼物')
    gift_starts_at = models.DateTimeField(null=True, blank=True)
    gift_ends_at = models.DateTimeField(null=True, blank=True)
    is_addon = models.BooleanField(default=False, help_text='须关联主订单的加仓、升级或其他附加费用')
    purchase_mode = models.CharField(max_length=10, choices=PurchaseMode.choices, default=PurchaseMode.DIRECT)
    purchase_note = models.CharField(max_length=255, blank=True, default='')

    gameplay = models.ForeignKey(Gameplay, null=True, blank=True, on_delete=models.PROTECT, related_name='services')
    platform = models.CharField(max_length=10, choices=Platform.choices, blank=True, default='')
    security_level = models.CharField(max_length=15, choices=SecurityLevel.choices, blank=True, default='')
    rules = models.TextField(blank=True, default='')
    details_html = models.TextField(blank=True, default='', help_text='当前价目玩法详情；留空使用所属玩法的富文本')
    rules_images = models.JSONField(default=list, blank=True)
    introduction_images = models.JSONField(default=list, blank=True, help_text='玩法简介广告，与完整履约规则分别展示')
    roulette_pool = models.JSONField(default=list, blank=True)
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True, default='')
    price = models.PositiveIntegerField()  # 兴安币价格（内部账务单位）
    game_category = models.ForeignKey(
        GameCategory, on_delete=models.PROTECT, null=True, blank=True,
        related_name='service_items',
    )
    service_category = models.ForeignKey(
        ServiceCategory, on_delete=models.PROTECT, null=True, blank=True,
        related_name='service_items',
    )
    # 要求陪玩档位：仅档位 >= 此档位的陪玩可接单/被派单；null 表示不限档位。
    # 档位高低以 EscortLevel.sort_order 比较（值越大档位越高）。
    required_level = models.ForeignKey(
        'users.EscortLevel', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='required_service_items',
    )
    # 平台抽成率(%)：null 表示未单独配置，下单时回落全局 SystemConfig
    commission_rate = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    cover_url = models.URLField(blank=True, default='')
    images = models.JSONField(default=list, blank=True)
    highlights = models.JSONField(default=list, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'id']
        indexes = [
            models.Index(
                fields=['is_active', 'sort_order', 'id'],
                name='svc_item_active_sort_idx',
            ),
            models.Index(
                fields=['game_category', 'is_active', 'sort_order'],
                name='svc_item_game_active_idx',
            ),
            models.Index(
                fields=['service_category', 'is_active', 'sort_order'],
                name='svc_item_type_active_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(price__gt=0),
                name='svc_item_price_positive',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(commission_rate__isnull=True)
                    | models.Q(commission_rate__range=(0, 100))
                ),
                name='svc_item_commission_valid',
            ),
        ]

    def __str__(self):
        return f"{self.name} - {self.price / 10:g}兴安币"


class ServiceFavorite(models.Model):
    """老板收藏的服务。"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='service_favorites'
    )
    account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='service_favorites',
    )
    service = models.ForeignKey(
        ServiceItem, on_delete=models.CASCADE, related_name='favorited_by'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'service')
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['account', 'service'],
                name='uniq_favorite_account_service',
            ),
        ]
        indexes = [
            models.Index(
                fields=['account', '-created_at'],
                name='favorite_account_time_idx',
            ),
        ]

    def __str__(self):
        return f"Favorite<{self.user_id} - {self.service_id}>"


class Order(models.Model):
    class SettlementMode(models.TextChoices):
        COMPLETION = 'COMPLETION', '完成服务结算'
        REPORT_APPROVAL = 'REPORT_APPROVAL', '报单审核后结算'

    class Status(models.TextChoices):
        PENDING = 'PENDING', '待接单'
        GRABBED = 'GRABBED', '已接单'
        IN_SERVICE = 'IN_SERVICE', '服务中'
        COMPLETED = 'COMPLETED', '已完成'
        CANCELLED = 'CANCELLED', '已取消'

    class PaymentStatus(models.TextChoices):
        UNPAID = 'UNPAID', '未支付'
        PAID = 'PAID', '已支付'
        REFUNDED = 'REFUNDED', '已退款'

    class EscortMode(models.TextChoices):
        SINGLE = 'SINGLE', '单陪'
        DOUBLE = 'DOUBLE', '双陪'

    # 终态：进入后不可再转换
    TERMINAL_STATUSES = {Status.COMPLETED, Status.CANCELLED}

    # 单个陪玩同时进行中订单（已接单 + 服务中）的数量上限
    MAX_CONCURRENT_ORDERS = 3

    business_cycle = models.PositiveIntegerField(default=1, editable=False)
    experience_kind = models.CharField(max_length=16, blank=True, default='', editable=False)
    is_gift_snapshot = models.BooleanField(default=False, editable=False)
    report_ready_at = models.DateTimeField(null=True, blank=True, editable=False)
    consumption_ready_at = models.DateTimeField(null=True, blank=True, db_index=True, editable=False)
    consumption_confirmed_at = models.DateTimeField(null=True, blank=True, editable=False)
    order_no = models.CharField(max_length=32, unique=True, blank=True, default='', db_index=True)
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='placed_orders')
    provider = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='received_orders')
    customer_account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='placed_orders',
    )
    provider_account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='received_orders',
    )
    service = models.ForeignKey(ServiceItem, on_delete=models.PROTECT)
    source_order = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='tip_orders',
    )
    support_contact = models.ForeignKey(
        'support.SupportContactCard', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='orders',
    )
    support_contact_name_snapshot = models.CharField(max_length=50, blank=True, default='')
    class ProviderGender(models.TextChoices):
        ANY = 'ANY', '不限'
        MALE = 'MALE', '男陪'
        FEMALE = 'FEMALE', '女陪'

    provider_gender = models.CharField(max_length=6, choices=ProviderGender.choices, default=ProviderGender.ANY, db_default=ProviderGender.ANY)
    amount = models.PositiveIntegerField()
    game_rounds = models.PositiveIntegerField(default=1)
    customer_phone_snapshot = models.CharField(max_length=20, blank=True, default='')
    requires_boss_selection = models.BooleanField(default=False)
    provider_name_snapshot = models.CharField(max_length=50, blank=True, default='')
    provider_requirement_snapshot = models.JSONField(default=dict, db_default={}, blank=True)
    gameplay_snapshot = models.JSONField(default=dict, blank=True)
    roulette_result = models.JSONField(default=dict, blank=True)
    service_name_snapshot = models.CharField(max_length=100, blank=True, default='')
    unit_price_snapshot = models.PositiveIntegerField(default=0)
    # 兴安币计价明细（下单时以内部账务单位落库）
    original_amount = models.PositiveIntegerField(default=0)  # 原价：单价×局数
    boss_discount = models.PositiveIntegerField(default=0)  # 老板VIP折扣额
    promo_discount = models.PositiveIntegerField(default=0)  # 活动折扣额
    coupon_discount = models.PositiveIntegerField(default=0)  # 优惠券抵扣额
    promotion = models.ForeignKey(
        'promotions.Promotion', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='orders',
    )  # 命中的促销活动快照
    # 兴安币拆账字段（下单时按服务抽成率计算并以内部账务单位落库）
    commission_rate = models.PositiveSmallIntegerField(default=0)  # 平台抽成率快照(%)
    commission_policy = models.CharField(max_length=12, choices=ServiceItem.CommissionPolicy.choices, default='LEGACY')
    income_hold_hours = models.PositiveSmallIntegerField(default=0)
    provider_income = models.PositiveIntegerField(default=0)  # 陪玩实得
    inviter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='referred_orders',
    )  # 下单老板的推荐人快照
    inviter_account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='referred_orders',
    )
    inviter_commission = models.PositiveIntegerField(default=0)  # 推荐人分佣
    inviter_commission_rate_snapshot = models.PositiveSmallIntegerField(default=0)
    shop_income = models.PositiveIntegerField(default=0)  # 店铺/平台留存
    # 老板下单时填写的游戏账号资料（结构化落库，供陪玩端与后台读取）
    game_region = models.CharField(max_length=50, blank=True, default='')
    game_nickname = models.CharField(max_length=50, blank=True, default='')
    game_uid = models.CharField(max_length=50, blank=True, default='')
    remark = models.CharField(max_length=255, blank=True, default='')
    payment_status = models.CharField(
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PAID,
        db_index=True,
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    escort_mode = models.CharField(
        max_length=10, choices=EscortMode.choices, default=EscortMode.SINGLE, db_index=True
    )  # 单陪/双陪
    grabbed_at = models.DateTimeField(blank=True, null=True)
    in_service_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    settlement_mode = models.CharField(max_length=20, choices=SettlementMode.choices, default=default_settlement_mode)
    settled_at = models.DateTimeField(blank=True, null=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)
    refunded_at = models.DateTimeField(blank=True, null=True)
    cancel_reason = models.CharField(max_length=255, blank=True, default='')
    auto_cancel_at = models.DateTimeField(blank=True, null=True, db_index=True)  # 超时自动取消时间
    reject_count = models.PositiveIntegerField(default=0)  # 累计被拒单次数
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(
                fields=['customer_account', 'status', '-created_at'],
                name='order_customer_status_idx',
            ),
            models.Index(
                fields=['provider_account', 'status', '-created_at'],
                name='order_provider_status_idx',
            ),
            models.Index(
                fields=['status', 'auto_cancel_at'],
                name='order_timeout_scan_idx',
            ),
            models.Index(
                fields=['service', '-created_at'],
                name='order_service_time_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(game_rounds__gte=1),
                name='order_rounds_positive',
            ),
            models.CheckConstraint(
                condition=models.Q(commission_rate__range=(0, 100)),
                name='order_commission_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=['PENDING', 'GRABBED', 'IN_SERVICE', 'COMPLETED', 'CANCELLED']
                ),
                name='order_status_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(payment_status__in=['UNPAID', 'PAID', 'REFUNDED']),
                name='order_payment_status_valid',
            ),
            # 资金守恒硬闸门：订单级三项分账之和不得超过实付金额。
            # 用 <= 而非 == 是为了兼容历史上分账字段全为 0 的旧订单。
            models.CheckConstraint(
                condition=models.Q(
                    provider_income__lte=(
                        models.F('amount')
                        - models.F('inviter_commission')
                        - models.F('shop_income')
                    )
                ),
                name='order_split_not_exceed_amount',
            ),
            models.CheckConstraint(
                condition=models.Q(escort_mode__in=['SINGLE', 'DOUBLE']),
                name='order_escort_mode_valid',
            ),
        ]

    def save(self, *args, **kwargs):
        if self._state.adding and self.service_id:
            from .gameplay import make_order_snapshot
            from wallet.club_rules import order_hold_hours
            self.business_cycle = self.customer_account.business_cycle if self.customer_account_id else 1
            self.experience_kind = self.service.experience_kind
            self.is_gift_snapshot = bool(self.service.service_category_id and self.service.service_category.is_gift)
            self.commission_policy = self.service.commission_policy
            from users.qualifications import service_requirement
            requirement=service_requirement(self.service)
            self.provider_requirement_snapshot = dict(requirement, commission_rate=self.commission_rate) if requirement else {}
            self.income_hold_hours = order_hold_hours()
            if self.inviter_id:
                self.inviter_commission_rate_snapshot = self.customer_account.inviter_commission_rate if self.customer_account_id else self.customer.inviter_commission_rate
            self.gameplay_snapshot, self.roulette_result = make_order_snapshot(self.service, self.game_rounds, companion_assignment=self.escort_mode == self.EscortMode.DOUBLE)
        if not self.order_no:
            self.order_no = f"ORD{uuid.uuid4().hex[:20].upper()}"
        if not self.service_name_snapshot and self.service_id:
            self.service_name_snapshot = self.service.name
        if not self.unit_price_snapshot and self.service_id:
            self.unit_price_snapshot = self.service.price
        if not self.customer_phone_snapshot and self.customer_id:
            self.customer_phone_snapshot = self.customer.phone or ''
        if not self.provider_name_snapshot and self.provider_id:
            self.provider_name_snapshot = self.provider.nickname or self.provider.username
        if not self.support_contact_name_snapshot and self.support_contact_id:
            self.support_contact_name_snapshot = self.support_contact.name
        super().save(*args, **kwargs)

    @property
    def fulfillment(self):
        from .gameplay import fulfillment_text
        return fulfillment_text(self)

    @property
    def is_terminal(self) -> bool:
        return self.status in self.TERMINAL_STATUSES


class KookDispatchRecord(models.Model):
    """订单推送到 KOOK 派单频道的可审计发送记录。"""

    class Trigger(models.TextChoices):
        NEW_ORDER = 'NEW_ORDER', '新订单待派单'
        PROVIDER_REJECTED = 'PROVIDER_REJECTED', '陪玩拒单后二次派单'

    class Status(models.TextChoices):
        PENDING = 'PENDING', '待发送'
        SENDING = 'SENDING', '发送中'
        SENT = 'SENT', '已发送'
        FAILED = 'FAILED', '发送失败'
        UNKNOWN = 'UNKNOWN', '结果待核对'
        SKIPPED = 'SKIPPED', '已跳过'

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='kook_dispatches')
    # 0 表示首次待派单，后续使用订单 reject_count，保证同一次事件只创建一条记录。
    sequence = models.PositiveIntegerField(default=0)
    trigger = models.CharField(max_length=30, choices=Trigger.choices)
    channel_id = models.CharField(max_length=64)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    message_id = models.CharField(max_length=100, blank=True, default='')
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    content = models.TextField(blank=True, default='')
    route_key = models.CharField(max_length=64, blank=True, default='')
    card_content = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['order', 'sequence'], name='uniq_kook_dispatch_order_sequence',
            ),
            models.CheckConstraint(
                condition=models.Q(trigger__in=['NEW_ORDER', 'PROVIDER_REJECTED']),
                name='kook_dispatch_trigger_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=['PENDING', 'SENDING', 'SENT', 'FAILED', 'UNKNOWN', 'SKIPPED']),
                name='kook_dispatch_status_valid',
            ),
        ]
        indexes = [
            models.Index(
                fields=['status', 'created_at'],
                name='kook_dispatch_status_idx',
            ),
        ]

    def __str__(self):
        return f'KookDispatch<{self.order_id}:{self.sequence} {self.status}>'


class WecomDispatchRecord(models.Model):
    """企微群投递记录；只保存目标指纹，Webhook 密钥留在环境配置中。"""

    Status = KookDispatchRecord.Status
    Trigger = KookDispatchRecord.Trigger
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='wecom_dispatches')
    sequence = models.PositiveIntegerField(default=0)
    trigger = models.CharField(max_length=30, choices=Trigger.choices)
    channel_id = models.CharField(max_length=64)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    message_id = models.CharField(max_length=100, blank=True, default='')
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True, default='')
    content = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['order', 'sequence'], name='uniq_wecom_dispatch_sequence'),
            models.CheckConstraint(
                condition=models.Q(status__in=['PENDING', 'SENDING', 'SENT', 'FAILED', 'UNKNOWN', 'SKIPPED']),
                name='wecom_dispatch_status_valid',
            ),
        ]


class OrderProvider(models.Model):
    """订单打手明细：一笔订单可关联一个（单陪）或两个（双陪）打手。

    **结算基数口径（资金守恒的关键）**：订单实付金额先在所有打手之间
    **均分**得到各自的 ``settlement_base``（余数给靠前打手），再由每个打手
    按**自己的**抽成规则从各自基数中算出实得：

        - 百分比抽成：provider_income = settlement_base * (100 - commission_rate) / 100
        - 固定金额抽成：provider_income = max(settlement_base - commission_fixed, 0)

    因此恒有 ``sum(settlement_base) == order.amount``，两个打手可以有
    完全不同的抽成比例，互不影响。严禁让每个打手都以订单全额为基数
    ——那会导致双陪订单总流出超过实付，平台净亏一份钱。

    计算逻辑统一收敛在 ``orders.settlement.build_provider_shares``，
    落库前请勿手写公式。订单完成时逐条给打手钱包入账并记 settled_at。
    """

    class CommissionType(models.TextChoices):
        PERCENT = 'PERCENT', '百分比抽成'
        FIXED = 'FIXED', '固定金额抽成'

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='providers')
    provider = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='order_assignments',
    )
    provider_account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='order_assignments',
    )
    provider_name_snapshot = models.CharField(max_length=50, blank=True, default='')
    settlement_base = models.PositiveIntegerField(default=0)  # 分配给该打手的结算基数（内部账务单位）
    commission_type = models.CharField(
        max_length=10, choices=CommissionType.choices, default=CommissionType.PERCENT
    )  # 抽成计算方式
    commission_rate = models.PositiveSmallIntegerField(
        default=0, validators=[MinValueValidator(0), MaxValueValidator(100)]
    )  # 该打手抽成率快照(%)，百分比方式生效
    commission_fixed = models.PositiveIntegerField(default=0)  # 固定抽成额快照（内部账务单位）
    provider_income = models.PositiveIntegerField(default=0)  # 该打手实得（内部账务单位）
    settled_at = models.DateTimeField(blank=True, null=True)  # 入账时间，空表示未结算
    released_at = models.DateTimeField(blank=True, null=True)  # 转出时间，与财务结算时间分开
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(
                fields=['order', 'provider'], name='uniq_order_provider'
            ),
            models.UniqueConstraint(
                fields=['order', 'provider_account'],
                name='uniq_order_provider_account',
            ),
            models.CheckConstraint(
                condition=models.Q(commission_type__in=['PERCENT', 'FIXED']),
                name='order_provider_comm_type_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(commission_rate__range=(0, 100)),
                name='order_provider_rate_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(provider_income__lte=models.F('settlement_base')),
                name='order_provider_income_lte_base',
            ),
        ]

    def __str__(self):
        return f"OrderProvider<order={self.order_id} provider={self.provider_id}>"


class OrderStatusLog(models.Model):
    """订单状态变更审计日志"""

    class Action(models.TextChoices):
        CREATE = 'CREATE', '创建'
        GRAB = 'GRAB', '接单'
        ASSIGN = 'ASSIGN', '派单'
        START = 'START', '开始服务'
        COMPLETE = 'COMPLETE', '完成'
        REJECT = 'REJECT', '拒单'
        CANCEL = 'CANCEL', '取消'
        REFUND = 'REFUND', '退款'
        TRANSFER = 'TRANSFER', '转单'
        AUTO_CANCEL = 'AUTO_CANCEL', '超时自动取消'

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='status_logs')
    action = models.CharField(max_length=20, choices=Action.choices, db_index=True)
    from_status = models.CharField(max_length=20, blank=True, default='')
    to_status = models.CharField(max_length=20, blank=True, default='')
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='order_status_logs',
    )
    operator_account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='order_status_logs',
    )
    reason = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['order', 'created_at']),
        ]

    def __str__(self):
        return f"OrderStatusLog<{self.order_id} {self.from_status}->{self.to_status}>"


class Evaluation(models.Model):
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name='evaluation')
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='given_evaluations')
    provider = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='received_evaluations')
    customer_account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='given_evaluations',
    )
    provider_account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='received_evaluations',
    )
    score = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])  # 综合评分
    skill_score = models.PositiveSmallIntegerField(
        default=5, validators=[MinValueValidator(1), MaxValueValidator(5)]
    )  # 技术
    attitude_score = models.PositiveSmallIntegerField(
        default=5, validators=[MinValueValidator(1), MaxValueValidator(5)]
    )  # 服务态度
    communication_score = models.PositiveSmallIntegerField(
        default=5, validators=[MinValueValidator(1), MaxValueValidator(5)]
    )  # 沟通
    content = models.TextField(blank=True, default='')
    is_anonymous = models.BooleanField(default=False)
    reply_content = models.TextField(blank=True, default='')
    replied_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(
                fields=['provider_account', '-created_at'],
                name='evaluation_provider_time_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(score__range=(1, 5)),
                name='evaluation_score_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(skill_score__range=(1, 5)),
                name='evaluation_skill_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(attitude_score__range=(1, 5)),
                name='evaluation_attitude_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(communication_score__range=(1, 5)),
                name='evaluation_communication_valid',
            ),
        ]

    @property
    def avg_score(self) -> float:
        """技术/服务/沟通三维均分，保留 1 位小数。"""
        return round((self.skill_score + self.attitude_score + self.communication_score) / 3, 1)

    def __str__(self):
        return f"Evaluation<{self.order_id}>"


class Reservation(models.Model):
    """预约单（ORD-1/2）：老板先锁陪玩的一段时间，到点再转成正式订单。

    与 ``Order`` 是两套独立的 ID 空间和生命周期 —— 预约只占「时间」，
    不占钱；只有 ``CONFIRMED -> CONVERTED`` 那一刻才走扣款建单。这样
    「约了没来」不会产生资金流水，也不会污染订单履约率统计。
    """

    class Status(models.TextChoices):
        PENDING = 'PENDING', '待确认'
        CONFIRMED = 'CONFIRMED', '已确认'
        REJECTED = 'REJECTED', '已拒绝'
        CANCELLED = 'CANCELLED', '已取消'
        CONVERTED = 'CONVERTED', '已转订单'
        EXPIRED = 'EXPIRED', '已过期'

    # 终态：进入后不可再流转
    TERMINAL_STATUSES = {
        Status.REJECTED, Status.CANCELLED, Status.CONVERTED, Status.EXPIRED,
    }
    # 占用陪玩时间片的状态：冲突检测只看这两种，被拒/取消/过期的档期立刻释放
    ACTIVE_STATUSES = {Status.PENDING, Status.CONFIRMED}

    reservation_no = models.CharField(
        max_length=32, unique=True, blank=True, default='', db_index=True,
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='placed_reservations',
    )
    customer_account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='placed_reservations',
    )
    provider = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='received_reservations',
    )
    provider_account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.CASCADE,
        related_name='received_reservations',
    )
    service = models.ForeignKey(ServiceItem, on_delete=models.PROTECT)
    # 转单产物：一条预约最多转出一张订单，转完即终态
    order = models.OneToOneField(
        Order,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reservation',
    )
    start_time = models.DateTimeField(db_index=True)
    end_time = models.DateTimeField(db_index=True)
    duration_minutes = models.PositiveIntegerField(default=0)
    game_rounds = models.PositiveIntegerField(default=1)
    # 预约时的报价快照（内部账务单位）。转单时以转单当刻重新计价为准，
    # 这里只用于列表展示与「价格变了吗」的对比，不参与任何账务计算。
    estimated_amount = models.PositiveIntegerField(default=0)
    service_name_snapshot = models.CharField(max_length=100, blank=True, default='')
    provider_name_snapshot = models.CharField(max_length=50, blank=True, default='')
    game_region = models.CharField(max_length=50, blank=True, default='')
    game_nickname = models.CharField(max_length=50, blank=True, default='')
    game_uid = models.CharField(max_length=50, blank=True, default='')
    remark = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    confirmed_at = models.DateTimeField(blank=True, null=True)
    rejected_at = models.DateTimeField(blank=True, null=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)
    converted_at = models.DateTimeField(blank=True, null=True)
    expired_at = models.DateTimeField(blank=True, null=True)
    cancel_reason = models.CharField(max_length=255, blank=True, default='')
    reject_reason = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-start_time', '-id']
        indexes = [
            # 冲突检测主路径：先按陪玩 + 占用态收窄，再按开始时间做范围扫描
            models.Index(
                fields=['provider_account', 'status', 'start_time'],
                name='reservation_provider_idx',
            ),
            models.Index(
                fields=['customer_account', 'status', '-start_time'],
                name='reservation_customer_idx',
            ),
            # 过期扫描：找「还占着时间片但已经过了结束时间」的预约
            models.Index(
                fields=['status', 'end_time'],
                name='reservation_expire_scan_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_time__gt=models.F('start_time')),
                name='reservation_time_window_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(duration_minutes__gte=1),
                name='reservation_duration_positive',
            ),
            models.CheckConstraint(
                condition=models.Q(game_rounds__gte=1),
                name='reservation_rounds_positive',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=[
                        'PENDING', 'CONFIRMED', 'REJECTED',
                        'CANCELLED', 'CONVERTED', 'EXPIRED',
                    ]
                ),
                name='reservation_status_valid',
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.reservation_no:
            self.reservation_no = f"RSV{uuid.uuid4().hex[:20].upper()}"
        if not self.service_name_snapshot and self.service_id:
            self.service_name_snapshot = self.service.name
        if not self.provider_name_snapshot and self.provider_account_id:
            account = self.provider_account
            profile = getattr(account, 'escort_profile', None)
            self.provider_name_snapshot = (
                (profile.display_name if profile else '')
                or account.nickname
                or account.username
            )
        if self.start_time and self.end_time and not self.duration_minutes:
            delta = self.end_time - self.start_time
            self.duration_minutes = max(int(delta.total_seconds() // 60), 0)
        super().save(*args, **kwargs)

    @property
    def is_terminal(self) -> bool:
        return self.status in self.TERMINAL_STATUSES

    def __str__(self):
        return f"Reservation<{self.reservation_no} {self.status}>"


class ReservationStatusLog(models.Model):
    """预约状态变更审计日志：与 ``OrderStatusLog`` 同构，但独立成表。

    合表会让两套生命周期的 action 枚举互相污染（订单没有「转单」，
    预约没有「接单」），查询时还得处处带 kind 过滤，得不偿失。
    """

    class Action(models.TextChoices):
        CREATE = 'CREATE', '创建预约'
        CONFIRM = 'CONFIRM', '确认预约'
        REJECT = 'REJECT', '拒绝预约'
        CANCEL = 'CANCEL', '取消预约'
        CONVERT = 'CONVERT', '转为订单'
        EXPIRE = 'EXPIRE', '过期失效'

    reservation = models.ForeignKey(
        Reservation, on_delete=models.CASCADE, related_name='status_logs',
    )
    action = models.CharField(max_length=20, choices=Action.choices, db_index=True)
    from_status = models.CharField(max_length=20, blank=True, default='')
    to_status = models.CharField(max_length=20, blank=True, default='')
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reservation_status_logs',
    )
    operator_account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reservation_status_logs',
    )
    reason = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['reservation', 'created_at']),
        ]

    def __str__(self):
        return (
            f"ReservationStatusLog<{self.reservation_id} "
            f"{self.from_status}->{self.to_status}>"
        )

from .applications_models import OrderApplication  # noqa: E402,F401

from .traffic_models import OrderTrafficConfig  # noqa: E402,F401


class ExperienceUse(models.Model):
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT)
    business_cycle = models.PositiveIntegerField()
    kind = models.CharField(max_length=16)
    platform = models.CharField(max_length=10, blank=True)
    period = models.CharField(max_length=10)
    order = models.OneToOneField(Order, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'business_cycle', 'kind', 'platform', 'period'], name='unique_experience_quota')]


class AfterSale(models.Model):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='aftersales')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT)
    reason = models.CharField(max_length=1000)
    status = models.CharField(max_length=16, default='OPEN', choices=[('OPEN', '处理中'), ('RESOLVED', '处理完成'), ('REFUNDED', '已退款')])
    resolution = models.CharField(max_length=1000, blank=True)
    operator = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['order'], name='one_aftersale_per_order')]
