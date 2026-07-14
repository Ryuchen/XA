from django.core.exceptions import ValidationError
from django.db import models

# 首页轮播图标准尺寸（像素）
BANNER_IMAGE_WIDTH = 750
BANNER_IMAGE_HEIGHT = 320


class Banner(models.Model):
    class LinkType(models.TextChoices):
        NONE = 'NONE', '无跳转'
        PRODUCT = 'PRODUCT', '商品详情'
        ANNOUNCEMENT = 'ANNOUNCEMENT', '公告详情'
        URL = 'URL', '外部链接'

    image = models.ImageField(
        upload_to='banners/',
        verbose_name='图片',
        help_text=f'请上传 {BANNER_IMAGE_WIDTH}×{BANNER_IMAGE_HEIGHT} 像素的图片',
    )
    title = models.CharField(max_length=100, blank=True, default='', verbose_name='标题')
    link_type = models.CharField(
        max_length=20,
        choices=LinkType.choices,
        default=LinkType.NONE,
        verbose_name='跳转类型',
    )
    link_value = models.CharField(max_length=255, blank=True, default='', verbose_name='跳转目标')
    sort_order = models.PositiveIntegerField(default=0, verbose_name='排序')
    is_active = models.BooleanField(default=True, db_index=True, verbose_name='是否启用')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '首页轮播'
        verbose_name_plural = '首页轮播'
        ordering = ['sort_order', '-created_at']

    def clean(self):
        super().clean()
        if not self.image:
            return
        width = getattr(self.image, 'width', None)
        height = getattr(self.image, 'height', None)
        if width != BANNER_IMAGE_WIDTH or height != BANNER_IMAGE_HEIGHT:
            raise ValidationError({
                'image': f'图片尺寸必须为 {BANNER_IMAGE_WIDTH}×{BANNER_IMAGE_HEIGHT} 像素，'
                         f'当前为 {width}×{height}。'
            })

    def __str__(self):
        return self.title or f'Banner #{self.pk}'
