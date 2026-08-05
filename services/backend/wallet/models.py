import uuid
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import IntegrityError, models, transaction

# 抽成率配置在 SystemConfig 中的键名
COMMISSION_RATE_KEY = 'platform_commission_rate'
# 最低提现金额配置在 SystemConfig 中的键名
MIN_WITHDRAW_AMOUNT_KEY = 'min_withdraw_amount'
# 提现税率配置在 SystemConfig 中的键名
WITHDRAW_TAX_RATE_KEY = 'withdraw_tax_rate'

# 陪玩通行证配置在 SystemConfig 中的键名前缀。
# 每档位两条记录，便于后台单独调价而不必编辑 JSON：
#   provider_pass_daily_price_<TIER>    该档位日价（内部账务单位）
#   provider_pass_delay_seconds_<TIER>  该档位公共单池可见延迟（秒）
# 无通行证档位用 NONE 作为 TIER 占位（对应 active_pass_tier == ''）。
PASS_DAILY_PRICE_KEY_PREFIX = 'provider_pass_daily_price_'
PASS_DELAY_SECONDS_KEY_PREFIX = 'provider_pass_delay_seconds_'
PASS_TIER_NONE = 'NONE'

# 通行证出厂默认值：SystemConfig 未配置或配置非法时回落此处。
# 与历史硬编码口径完全一致，保证本次下沉配置不改变既有行为。
DEFAULT_PASS_DAILY_PRICES = {
    'BLACK': 5000,
    'GOLD': 3000,
    'SILVER': 1000,
    'BRONZE': 200,
}
DEFAULT_PASS_DELAY_SECONDS = {
    'BLACK': 0,
    'GOLD': 30,
    'SILVER': 60,
    'BRONZE': 120,
    '': 300,
}


class Wallet(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='wallet')
    account = models.OneToOneField(
        'club_accounts.ClubAccount',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='wallet',
    )
    balance = models.IntegerField(default=0)  # 余额，内部账务单位；10 单位 = 1 兴安币
    frozen_amount = models.PositiveIntegerField(default=0)
    total_recharge = models.IntegerField(default=0)  # 累计充值兴安币（内部账务单位）
    total_gift = models.IntegerField(default=0)  # 累计赠送兴安币（内部账务单位）
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Wallet'
        verbose_name_plural = 'Wallets'
        constraints = [
            models.CheckConstraint(
                condition=models.Q(balance__gte=0),
                name='wallet_balance_nonnegative',
            ),
            models.CheckConstraint(
                condition=models.Q(total_recharge__gte=0),
                name='wallet_recharge_nonnegative',
            ),
            models.CheckConstraint(
                condition=models.Q(total_gift__gte=0),
                name='wallet_gift_nonnegative',
            ),
        ]

    def __str__(self):
        return f"{self.user.username} - 余额: {self.balance / 10:g}兴安币"


class Transaction(models.Model):
    class TxType(models.TextChoices):
        TOPUP = 'TOPUP', '充值'
        PAY = 'PAY', '支付'
        INCOME = 'INCOME', '收益'
        WITHDRAW = 'WITHDRAW', '提现'
        REWARD = 'REWARD', '奖励'
        GIFT = 'GIFT', '赠送'
        PENALTY = 'PENALTY', '罚款'
        DEPOSIT = 'DEPOSIT', '押金'
        SHOP_INCOME = 'SHOP_INCOME', '平台收入'
        REFUND = 'REFUND', '订单退款'
        PASS_PURCHASE = 'PASS_PURCHASE', '通行证购买'
        # 提现代扣税费：记在平台钱包侧，与陪玩侧的 WITHDRAW 流水配对，保证税额不脱账。
        WITHDRAW_TAX = 'WITHDRAW_TAX', '提现税费'
        # 后台人工调账：金额正负即调增/调减，原因见 remark；不再借用 REWARD/WITHDRAW 语义。
        ADJUST = 'ADJUST', '后台调账'

    class Status(models.TextChoices):
        PENDING = 'PENDING', '处理中'
        SUCCESS = 'SUCCESS', '成功'
        FAILED = 'FAILED', '失败'

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name='transactions')
    order = models.ForeignKey('orders.Order', on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions')
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='operated_transactions',
        help_text='后台调账等人工操作的操作人；系统自动流水为空',
    )
    operator_account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='operated_transactions',
    )
    tx_no = models.CharField(max_length=32, unique=True, blank=True, default='', db_index=True)
    amount = models.IntegerField()  # 正数代表增加，负数代表扣减
    tx_type = models.CharField(max_length=20, choices=TxType.choices, db_index=True)
    balance_before = models.IntegerField(default=0)
    balance_after = models.IntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUCCESS, db_index=True)
    remark = models.CharField(max_length=255, blank=True, default='')
    external_tx_id = models.CharField(max_length=64, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(
                fields=['wallet', 'status', '-created_at'],
                name='tx_wallet_status_time_idx',
            ),
            models.Index(
                fields=['wallet', 'tx_type', 'status', '-created_at'],
                name='tx_wallet_type_time_idx',
            ),
            models.Index(
                fields=['order', 'tx_type', 'status'],
                name='tx_order_type_status_idx',
            ),
            models.Index(
                fields=['external_tx_id'],
                name='tx_external_id_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    tx_type__in=[
                        'TOPUP', 'PAY', 'INCOME', 'WITHDRAW', 'REWARD', 'GIFT',
                        'PENALTY', 'DEPOSIT', 'SHOP_INCOME', 'REFUND', 'PASS_PURCHASE',
                        'WITHDRAW_TAX', 'ADJUST',
                    ]
                ),
                name='transaction_type_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=['PENDING', 'SUCCESS', 'FAILED']),
                name='transaction_status_valid',
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.tx_no:
            self.tx_no = f"TX{uuid.uuid4().hex[:22].upper()}"
        if self.balance_after == 0 and self.balance_before:
            self.balance_after = self.balance_before + self.amount
        super().save(*args, **kwargs)


class IdempotencyKey(models.Model):
    """出账类写接口的幂等键（全站唯一的幂等落库位置）。

    为什么需要它
    ------------
    小程序端在弱网下会自动重试 POST；提现、充值、打赏、下单这些**出账**接口
    一旦被重复执行，就是实打实的资金损失。数据库唯一约束是唯一可靠的去重手段
    —— 应用层的「先查再写」在并发下必然漏。

    使用约定（不得绕过）
    --------------------
    1. 键为 ``(account, request_id)``，其中 ``request_id`` 由客户端生成并透传；
    2. 出账写入前**必须**先调用 ``wallet.services.claim_idempotency_key``
       抢占该键，抢不到即判定为重复请求，直接拒绝，不得继续执行业务；
    3. 抢占动作必须与业务写入处于**同一个** ``transaction.atomic`` 块内，
       业务回滚时幂等键一并回滚，避免「键占了但钱没动」导致用户永久无法重试。

    ``scope`` 仅作排障标注（如 ``withdraw`` / ``order.create``），
    **不参与**唯一约束 —— 同一个 request_id 在任何业务上都只允许用一次，
    这样客户端只要保证 UUID 不复用即可，无需理解后端的业务分区。
    """

    account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.CASCADE,
        related_name='idempotency_keys',
    )
    request_id = models.CharField(
        max_length=64,
        help_text='客户端生成的幂等键（client_request_id），建议 UUID hex',
    )
    scope = models.CharField(
        max_length=32, blank=True, default='',
        help_text='业务标注，仅用于排障，不参与唯一约束',
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'IdempotencyKey'
        verbose_name_plural = 'IdempotencyKeys'
        constraints = [
            models.UniqueConstraint(
                fields=['account', 'request_id'],
                name='uniq_idempotency_account_request',
            ),
        ]
        indexes = [
            models.Index(
                fields=['account', '-created_at'],
                name='idempotency_account_time_idx',
            ),
        ]

    def __str__(self):
        return f'{self.account_id}:{self.request_id}'


class SystemConfig(models.Model):
    """轻量键值配置表：后台可改的运行时参数（如平台抽成率）。"""

    key = models.CharField(max_length=64, unique=True, db_index=True)
    value = models.CharField(max_length=255, blank=True, default='')
    remark = models.CharField(max_length=255, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'SystemConfig'
        verbose_name_plural = 'SystemConfigs'

    def __str__(self):
        return f"{self.key}={self.value}"


def get_commission_rate():
    """返回当前平台抽成率（百分比，0-100 的整数）。

    优先读取 SystemConfig，未配置或非法时回落 settings.PLATFORM_COMMISSION_RATE。
    """
    default = getattr(settings, 'PLATFORM_COMMISSION_RATE', 20)
    config = SystemConfig.objects.filter(key=COMMISSION_RATE_KEY).first()
    if not config:
        return default
    try:
        rate = int(config.value)
    except (TypeError, ValueError):
        return default
    if rate < 0 or rate > 100:
        return default
    return rate


def get_min_withdraw_amount():
    """返回当前最低提现兴安币对应的内部账务值（非负整数）。

    优先读取 SystemConfig，未配置或非法时回落 settings.MIN_WITHDRAW_AMOUNT。
    """
    default = getattr(settings, 'MIN_WITHDRAW_AMOUNT', 10000)
    config = SystemConfig.objects.filter(key=MIN_WITHDRAW_AMOUNT_KEY).first()
    if not config:
        return default
    try:
        amount = int(config.value)
    except (TypeError, ValueError):
        return default
    if amount < 0:
        return default
    return amount


def get_withdraw_tax_rate():
    """返回当前提现税率（百分比，0-100 的整数）。

    优先读取 SystemConfig，未配置或非法时回落 settings.WITHDRAW_TAX_RATE（默认 2）。
    """
    default = getattr(settings, 'WITHDRAW_TAX_RATE', 2)
    config = SystemConfig.objects.filter(key=WITHDRAW_TAX_RATE_KEY).first()
    if not config:
        return default
    try:
        rate = int(config.value)
    except (TypeError, ValueError):
        return default
    if rate < 0 or rate > 100:
        return default
    return rate


def compute_withdraw_tax(amount, tax_rate):
    """按税率计算提现税额（内部账务单位，四舍五入取整）。

    刻意不用内建 ``round``：它是「银行家舍入」（round-half-to-even），
    ``round(2.5) == 2``、``round(0.5) == 0``，且中间量走二进制浮点还会引入
    ``0.1 + 0.2 != 0.3`` 这类误差。税额是要对外解释的钱，必须用十进制
    ROUND_HALF_UP，保证「.5 一律进位」且结果可被人工用计算器复算。
    """
    if amount <= 0 or tax_rate <= 0:
        return 0
    tax = (Decimal(int(amount)) * Decimal(int(tax_rate)) / Decimal(100)).quantize(
        Decimal('1'), rounding=ROUND_HALF_UP,
    )
    # 税额不允许超过本金，避免异常税率配置击穿 actual_amount 的非负约束。
    return min(int(tax), int(amount))


def _get_int_config(key, default, *, minimum=0, maximum=None):
    """读取一条整数型 SystemConfig，非法值一律回落默认值。

    Args:
        key: SystemConfig 主键名。
        default: 未配置或配置非法时的回落值。
        minimum: 允许的最小值（含）。
        maximum: 允许的最大值（含）；None 表示不设上限。
    """
    config = SystemConfig.objects.filter(key=key).only('value').first()
    if config is None:
        return default
    try:
        value = int(str(config.value).strip())
    except (TypeError, ValueError):
        return default
    if value < minimum:
        return default
    if maximum is not None and value > maximum:
        return default
    return value


def get_pass_daily_price(tier):
    """返回指定通行证档位的日价（内部账务单位整数）。

    档位取值同 ``users.EscortProfile.PassTier``；空串代表「无通行证」。
    未配置时回落 ``DEFAULT_PASS_DAILY_PRICES``，保持与历史硬编码一致。
    """
    normalized = (tier or PASS_TIER_NONE).upper()
    default = DEFAULT_PASS_DAILY_PRICES.get(normalized, 0)
    return _get_int_config(
        f'{PASS_DAILY_PRICE_KEY_PREFIX}{normalized}', default, minimum=0,
    )


def get_pass_daily_prices():
    """返回全部可售通行证档位的日价映射 ``{tier: price}``。"""
    return {
        tier: get_pass_daily_price(tier)
        for tier in DEFAULT_PASS_DAILY_PRICES
    }


def get_pass_delay_seconds(tier):
    """返回指定通行证档位在公共单池中的可见延迟（秒）。

    档位越高延迟越短；空串（无通行证）延迟最长。未配置时回落
    ``DEFAULT_PASS_DELAY_SECONDS``，保持与历史硬编码一致。
    """
    normalized = (tier or '').upper()
    default = DEFAULT_PASS_DELAY_SECONDS.get(normalized)
    if default is None:
        default = DEFAULT_PASS_DELAY_SECONDS.get('', 300)
    key_tier = normalized or PASS_TIER_NONE
    return _get_int_config(
        f'{PASS_DELAY_SECONDS_KEY_PREFIX}{key_tier}', default, minimum=0,
    )


def get_pass_delays():
    """返回全部档位的可见延迟映射 ``{tier: seconds}``（含空串档位）。"""
    return {
        tier: get_pass_delay_seconds(tier)
        for tier in DEFAULT_PASS_DELAY_SECONDS
    }


def get_platform_wallet(for_update=False):
    """返回平台系统账户的钱包，用于归集 shop_income（店铺/平台留存）。

    平台系统用户由数据迁移创建（禁止登录），其钱包由 post_save 信号自动建立。
    Args:
        for_update: 为 True 时对钱包行加锁（须在 transaction.atomic 内调用）。
    """
    from django.contrib.auth import get_user_model

    User = get_user_model()
    user, created = User.objects.get_or_create(
        username=settings.PLATFORM_SYSTEM_USERNAME,
        defaults={
            'is_active': False,
            'is_staff': False,
            'is_superuser': False,
            'role': 'ADMIN',
            'nickname': '平台账户',
        },
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=['password'])
    qs = Wallet.objects.all()
    if for_update:
        qs = qs.select_for_update()
    wallet, _ = qs.get_or_create(user=user)
    return wallet


class ProviderReport(models.Model):
    """陪玩报单：平台订单完成后上传过程截图，历史数据兼容手工报单。"""

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', '待提交'
        PENDING = 'PENDING', '待审核'
        APPROVED = 'APPROVED', '已通过'
        REJECTED = 'REJECTED', '已驳回'

    provider = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='provider_reports'
    )
    provider_account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='provider_reports',
    )
    order = models.ForeignKey(
        'orders.Order', on_delete=models.CASCADE, null=True, blank=True,
        related_name='provider_reports',
    )
    game_name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True, default='')
    amount = models.PositiveIntegerField()  # 报单兴安币金额（内部账务单位）
    proof_image = models.ImageField(upload_to='reports/', blank=True, null=True)
    entry_image = models.ImageField(upload_to='reports/entry/', blank=True, null=True)
    completion_image = models.ImageField(upload_to='reports/completion/', blank=True, null=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    commission_rate = models.PositiveSmallIntegerField(default=0)  # 审核时固化的抽成率快照(%)
    payout_amount = models.PositiveIntegerField(default=0)  # 审核通过陪玩实得兴安币（内部账务单位）
    remark = models.CharField(max_length=255, blank=True, default='')
    audit_remark = models.CharField(max_length=255, blank=True, default='')
    auditor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='audited_reports',
    )
    auditor_account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audited_reports',
    )
    transaction = models.ForeignKey(
        Transaction, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    audited_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'ProviderReport'
        verbose_name_plural = 'ProviderReports'
        constraints = [
            models.UniqueConstraint(
                fields=['provider', 'order'], name='uniq_provider_order_report',
            ),
            models.UniqueConstraint(
                fields=['provider_account', 'order'],
                name='uniq_provider_account_report',
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name='provider_report_amount_positive',
            ),
            models.CheckConstraint(
                condition=models.Q(commission_rate__range=(0, 100)),
                name='provider_report_rate_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(payout_amount__lte=models.F('amount')),
                name='provider_report_payout_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=['DRAFT', 'PENDING', 'APPROVED', 'REJECTED']),
                name='provider_report_status_valid',
            ),
        ]
        indexes = [
            models.Index(
                fields=['provider_account', 'status', '-created_at'],
                name='report_account_status_idx',
            ),
            models.Index(
                fields=['status', '-created_at'],
                name='report_status_time_idx',
            ),
        ]

    def __str__(self):
        return f"报单#{self.pk} {self.game_name} {self.amount / 10:g}兴安币 [{self.status}]"


class ProviderReportImage(models.Model):
    """报单战绩截图；一份报单允许上传多张。"""

    report = models.ForeignKey(ProviderReport, on_delete=models.CASCADE, related_name='result_images')
    image = models.ImageField(upload_to='reports/results/')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']


class WithdrawRequest(models.Model):
    """提现申请：陪玩发起，后台审核。

    申请时冻结资金（balance 减、frozen_amount 加），并创建 PENDING 流水。
    审核通过：扣除冻结（实际打款）、流水转 SUCCESS。
    审核驳回：解冻并退还可用余额、流水转 FAILED。
    """

    class Status(models.TextChoices):
        PENDING = 'PENDING', '待审核'
        APPROVED = 'APPROVED', '已通过'
        REJECTED = 'REJECTED', '已驳回'

    class PayeeMethod(models.TextChoices):
        WECHAT = 'WECHAT', '微信'
        ALIPAY = 'ALIPAY', '支付宝'
        BANK = 'BANK', '银行卡'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='withdraw_requests'
    )
    account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='withdraw_requests',
    )
    amount = models.PositiveIntegerField()  # 提现兴安币（内部账务单位）
    tax_rate = models.PositiveIntegerField(
        default=0, help_text='申请时快照的提现税率（百分比，0-100）',
    )
    tax_amount = models.PositiveIntegerField(
        default=0, help_text='按税率计算的税额（内部账务单位）',
    )
    actual_amount = models.PositiveIntegerField(
        default=0, help_text='扣税后实际打款给陪玩的金额（内部账务单位）',
    )
    payee_method = models.CharField(max_length=20, choices=PayeeMethod.choices)
    payee_account = models.CharField(max_length=100)  # 收款账号
    payee_name = models.CharField(max_length=50)  # 收款人姓名
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    remark = models.CharField(max_length=255, blank=True, default='')
    audit_remark = models.CharField(max_length=255, blank=True, default='')
    payout_reference = models.CharField(
        max_length=100, blank=True, default='',
        help_text='实际打款的渠道流水号或人工转账凭证编号',
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    auditor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='audited_withdrawals',
    )
    auditor_account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audited_withdrawals',
    )
    transaction = models.ForeignKey(
        Transaction, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    audited_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'WithdrawRequest'
        verbose_name_plural = 'WithdrawRequests'
        indexes = [
            models.Index(
                fields=['account', 'status', '-created_at'],
                name='withdraw_account_status_idx',
            ),
            models.Index(
                fields=['status', '-created_at'],
                name='withdraw_status_time_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name='withdraw_amount_positive',
            ),
            models.CheckConstraint(
                condition=models.Q(tax_rate__range=(0, 100)),
                name='withdraw_tax_rate_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(tax_amount__lte=models.F('amount')),
                name='withdraw_tax_amount_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    actual_amount=models.F('amount') - models.F('tax_amount')
                ),
                name='withdraw_actual_amount_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=['PENDING', 'APPROVED', 'REJECTED']),
                name='withdraw_status_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(payee_method__in=['WECHAT', 'ALIPAY', 'BANK']),
                name='withdraw_payee_method_valid',
            ),
        ]

    def save(self, *args, **kwargs):
        # actual_amount 是 amount 与 tax_amount 的派生快照，统一在模型层计算，
        # 避免后台脚本、管理命令或测试绕过 API serializer 时写入不一致数据。
        self.actual_amount = self.amount - self.tax_amount
        update_fields = kwargs.get('update_fields')
        if update_fields and {'amount', 'tax_amount'} & set(update_fields):
            kwargs['update_fields'] = set(update_fields) | {'actual_amount'}
        super().save(*args, **kwargs)

    def __str__(self):
        return f"提现#{self.pk} {self.user.username} {self.amount / 10:g}兴安币 [{self.status}]"


class RechargeRecord(models.Model):
    """企微充值记录：按 1:10 兑换规则录入兴安币，可附带赠送兴安币。

    实充与赠送各落一条对应流水（TOPUP / GIFT），并累加进钱包统计字段。
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='recharge_records'
    )
    account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='recharge_records',
    )
    amount = models.PositiveIntegerField()  # 充值兴安币（内部账务单位）
    gift_amount = models.PositiveIntegerField(default=0)  # 赠送兴安币（内部账务单位）
    trade_no = models.CharField(
        max_length=64, blank=True, default='',
        help_text='第三方交易/转账流水号，用于对账溯源',
    )
    proof_image = models.ImageField(
        upload_to='recharges/', blank=True, null=True,
        help_text='充值凭证截图（转账/收款截图）',
    )
    remark = models.CharField(max_length=255, blank=True, default='')
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='operated_recharges',
    )
    operator_account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='operated_recharges',
    )
    recharge_tx = models.ForeignKey(
        Transaction, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    gift_tx = models.ForeignKey(
        Transaction, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'RechargeRecord'
        verbose_name_plural = 'RechargeRecords'
        indexes = [
            models.Index(
                fields=['account', '-created_at'],
                name='recharge_account_time_idx',
            ),
            models.Index(
                fields=['trade_no'],
                name='recharge_trade_no_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name='recharge_amount_positive',
            ),
        ]

    @property
    def total_amount(self) -> int:
        return self.amount + self.gift_amount

    def __str__(self):
        return (
            f"企微充值#{self.pk} {self.user.username} "
            f"充值{self.amount / 10:g}兴安币，到账{self.total_amount / 10:g}兴安币"
        )


class DisposeRecord(models.Model):
    """奖励罚款记录：后台对陪玩进行奖励或罚款，直接影响其钱包余额。"""

    class DisposeType(models.TextChoices):
        REWARD = 'REWARD', '奖励'
        PENALTY = 'PENALTY', '罚款'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='dispose_records'
    )
    account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='dispose_records',
    )
    dispose_type = models.CharField(
        max_length=16, choices=DisposeType.choices, db_index=True
    )
    amount = models.PositiveIntegerField()  # 奖励/罚款兴安币（内部账务单位）
    reason = models.CharField(max_length=255, blank=True, default='')
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='operated_disposes',
    )
    operator_account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='operated_disposes',
    )
    transaction = models.ForeignKey(
        Transaction, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'DisposeRecord'
        verbose_name_plural = 'DisposeRecords'
        indexes = [
            models.Index(
                fields=['account', 'dispose_type', '-created_at'],
                name='dispose_account_type_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name='dispose_amount_positive',
            ),
            models.CheckConstraint(
                condition=models.Q(dispose_type__in=['REWARD', 'PENALTY']),
                name='dispose_type_valid',
            ),
        ]

    def __str__(self):
        return f"{self.get_dispose_type_display()}#{self.pk} {self.user.username} {self.amount / 10:g}兴安币"
