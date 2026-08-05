from django.conf import settings
from django.db import models


class AdminRole(models.Model):
    """后台角色：持有一组权限点 code。"""

    name = models.CharField(max_length=50, unique=True, verbose_name='角色名')
    code = models.CharField(max_length=50, unique=True, verbose_name='角色标识')
    description = models.CharField(max_length=255, blank=True, default='', verbose_name='描述')
    permissions = models.JSONField(default=list, blank=True, verbose_name='权限点')
    is_active = models.BooleanField(default=True, db_index=True, verbose_name='是否启用')
    sort_order = models.PositiveIntegerField(default=0, verbose_name='排序')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '后台角色'
        verbose_name_plural = '后台角色'
        ordering = ['sort_order', 'id']

    def __str__(self):
        return self.name


class AdminMembership(models.Model):
    """管理员身份：将用户关联到后台角色。"""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='admin_membership',
        verbose_name='用户',
    )
    account = models.OneToOneField(
        'club_accounts.ClubAccount',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='admin_membership',
        verbose_name='业务账户',
    )
    roles = models.ManyToManyField(
        AdminRole,
        blank=True,
        related_name='members',
        verbose_name='角色',
    )
    remark = models.CharField(max_length=255, blank=True, default='', verbose_name='备注')
    is_active = models.BooleanField(default=True, db_index=True, verbose_name='是否启用')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '管理员'
        verbose_name_plural = '管理员'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.account_id or self.user_id}'


class AdminAuditLog(models.Model):
    """后台操作审计日志：记录管理员对后台的写操作。"""

    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs',
        verbose_name='操作人',
    )
    operator_account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs',
        verbose_name='业务操作人',
    )
    operator_name = models.CharField(max_length=150, blank=True, default='', verbose_name='操作人快照')
    method = models.CharField(max_length=10, verbose_name='请求方法')
    path = models.CharField(max_length=255, db_index=True, verbose_name='请求路径')
    resource = models.CharField(max_length=100, blank=True, default='', db_index=True, verbose_name='资源')
    object_id = models.CharField(max_length=64, blank=True, default='', verbose_name='对象ID')
    request_body = models.JSONField(default=dict, blank=True, verbose_name='请求参数(脱敏)')
    status_code = models.PositiveIntegerField(default=0, verbose_name='响应状态码')
    ip = models.GenericIPAddressField(null=True, blank=True, verbose_name='来源IP')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = '操作审计日志'
        verbose_name_plural = '操作审计日志'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['operator', '-created_at']),
        ]

    def __str__(self):
        return f'{self.operator_name} {self.method} {self.path}'


class AccountBan(models.Model):
    """封禁记录：替代直接 PATCH 三个布尔开关（is_active/can_login/can_view）的无语义封禁。

    - 封禁时强制记录原因与操作人，并翻转目标 ClubAccount 的开关（锁定登录与数据可见性）；
    - 支持到期自动解封（expires_at 非空时由 beat/命令扫描回滚）；
    - 提供可追溯的专用封禁审计表，配合 AdminAuditLog 的 HTTP 层日志形成业务语义审计。
    """

    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', '封禁中'
        LIFTED = 'LIFTED', '已手动解封'
        EXPIRED = 'EXPIRED', '已到期自动解封'

    class Scope(models.TextChoices):
        FULL = 'FULL', '全量封禁（锁定 is_active/can_login/can_view）'

    account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.PROTECT,
        related_name='bans',
        verbose_name='被封禁账户',
    )
    reason = models.TextField(verbose_name='封禁原因')
    scope = models.CharField(
        max_length=10,
        choices=Scope.choices,
        default=Scope.FULL,
        verbose_name='封禁范围',
    )
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='issued_bans',
        verbose_name='操作人',
    )
    operator_account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='issued_bans_by_account',
        verbose_name='业务操作人',
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
        verbose_name='状态',
    )
    expires_at = models.DateTimeField(
        null=True, blank=True, verbose_name='自动解封时间（null=永久）',
    )
    banned_at = models.DateTimeField(auto_now_add=True, verbose_name='封禁时间')
    lifted_at = models.DateTimeField(null=True, blank=True, verbose_name='解封时间')
    lifted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lifted_bans',
        verbose_name='解封人',
    )
    lifted_by_account = models.ForeignKey(
        'club_accounts.ClubAccount',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lifted_bans_by_account',
        verbose_name='业务解封人',
    )
    lift_reason = models.TextField(blank=True, default='', verbose_name='解封原因')

    class Meta:
        verbose_name = '封禁记录'
        verbose_name_plural = '封禁记录'
        ordering = ['-banned_at']
        indexes = [
            models.Index(fields=['account', 'status']),
            models.Index(fields=['status', 'expires_at']),
        ]

    def __str__(self):
        return f'封禁#{self.pk} 账户{self.account_id} [{self.status}]'
