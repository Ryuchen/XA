import secrets

from django.conf import settings
from django.db import models


def _gen_token() -> str:
    """生成免登录分享 token（URL 安全，足够随机以防枚举）。"""
    return secrets.token_urlsafe(24)


class AuditionLink(models.Model):
    """试音链接：客服为某次陪玩试音活动生成的一组免登录入口。

    一条记录产出两个分享 token：boss_token（老板观看/邀约入口）与
    provider_token（陪玩报名/试音入口）。后台据此拼出可复制的 URL。
    每个 token 可绑定一个真实用户（boss_user / provider_user），访客打开
    链接后凭 token 换发该用户的真正 JWT 登录态。
    """

    title = models.CharField(max_length=100, verbose_name='标题')
    remark = models.CharField(max_length=255, blank=True, default='', verbose_name='备注')
    expire_at = models.DateTimeField(blank=True, null=True, verbose_name='截止时间')
    is_active = models.BooleanField(default=True, db_index=True, verbose_name='是否启用')
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audition_links',
        verbose_name='创建客服',
    )
    boss_token = models.CharField(max_length=64, unique=True, db_index=True, verbose_name='老板入口token')
    provider_token = models.CharField(max_length=64, unique=True, db_index=True, verbose_name='陪玩入口token')
    boss_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audition_boss_links',
        verbose_name='老板入口绑定用户',
    )
    provider_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audition_provider_links',
        verbose_name='陪玩入口绑定用户',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = '试音链接'
        verbose_name_plural = '试音链接'

    def save(self, *args, **kwargs):
        if not self.boss_token:
            self.boss_token = _gen_token()
        if not self.provider_token:
            self.provider_token = _gen_token()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.title


class AuditionSignup(models.Model):
    """陪玩报名：访客凭 provider_token 换发登录态后，向某条试音链接提交的报名。

    报名人来自登录态（applicant），同一链接同一报名人只允许一条记录。
    客服在后台审核（通过/驳回），审核仅改状态，不涉及任何资金动作。
    """

    class Status(models.TextChoices):
        PENDING = 'PENDING', '待审核'
        APPROVED = 'APPROVED', '已通过'
        REJECTED = 'REJECTED', '已驳回'

    link = models.ForeignKey(
        AuditionLink,
        on_delete=models.CASCADE,
        related_name='signups',
        verbose_name='试音链接',
    )
    applicant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='audition_signups',
        verbose_name='报名人',
    )
    contact = models.CharField(max_length=100, blank=True, default='', verbose_name='联系方式')
    game = models.CharField(max_length=100, blank=True, default='', verbose_name='擅长游戏')
    remark = models.CharField(max_length=255, blank=True, default='', verbose_name='备注')
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True,
        verbose_name='审核状态',
    )
    auditor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audited_audition_signups',
        verbose_name='审核客服',
    )
    audit_remark = models.CharField(max_length=255, blank=True, default='', verbose_name='审核备注')
    audited_at = models.DateTimeField(null=True, blank=True, verbose_name='审核时间')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ('link', 'applicant')
        verbose_name = '试音报名'
        verbose_name_plural = '试音报名'

    def __str__(self) -> str:
        return f"报名#{self.pk} link={self.link_id} user={self.applicant_id} [{self.status}]"
