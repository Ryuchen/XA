from django.db import models


class SupportContactCard(models.Model):
    """客服名片：运营在 admin 后台维护，前端拉取展示。"""

    name = models.CharField(max_length=50, verbose_name='客服名')
    company = models.CharField(max_length=100, blank=True, default='', verbose_name='公司')
    wechat_id = models.CharField(max_length=50, blank=True, default='', verbose_name='微信号')
    wecom_corp_id = models.CharField(
        max_length=64,
        blank=True,
        default='',
        verbose_name='企业微信企业ID',
        help_text='企业微信「我的企业」中的企业ID，用于小程序打开微信客服',
    )
    wecom_service_url = models.URLField(
        blank=True,
        default='',
        verbose_name='微信客服接入链接',
        help_text='企业微信微信客服后台生成的 https://work.weixin.qq.com/kfid/... 链接',
    )
    avatar_url = models.URLField(blank=True, default='', verbose_name='头像')
    qrcode_url = models.URLField(blank=True, default='', verbose_name='二维码')
    tips = models.TextField(blank=True, default='', verbose_name='提示语')
    is_active = models.BooleanField(default=True, db_index=True, verbose_name='是否启用')
    sort_order = models.PositiveIntegerField(default=0, verbose_name='排序')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', '-updated_at']
        verbose_name = '客服名片'
        verbose_name_plural = '客服名片'

    def __str__(self):
        return self.name
