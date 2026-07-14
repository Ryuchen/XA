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
    role = models.ForeignKey(
        AdminRole,
        on_delete=models.SET_NULL,
        null=True,
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
        return f'{self.user_id} - {self.role_id}'
