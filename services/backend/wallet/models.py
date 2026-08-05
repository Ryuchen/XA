import uuid

from django.conf import settings
from django.db import models

# 抽成率配置在 SystemConfig 中的键名
COMMISSION_RATE_KEY = 'platform_commission_rate'
# 最低提现金额配置在 SystemConfig 中的键名
MIN_WITHDRAW_AMOUNT_KEY = 'min_withdraw_amount'
# 提现税率配置在 SystemConfig 中的键名
WITHDRAW_TAX_RATE_KEY = 'withdraw_tax_rate'


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
    """按税率计算提现税额（内部账务单位，四舍五入取整）。"""
    return round(amount * tax_rate / 100)


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
