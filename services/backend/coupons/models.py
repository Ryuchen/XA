from django.conf import settings
from django.db import models
from django.utils import timezone


class Coupon(models.Model):
    """优惠券模板（俱乐部发放的券种）。"""

    class DiscountType(models.TextChoices):
        THRESHOLD = 'THRESHOLD', '满减'
        DIRECT = 'DIRECT', '无门槛'

    name = models.CharField(max_length=100, verbose_name='券名称')
    discount_type = models.CharField(
        max_length=20,
        choices=DiscountType.choices,
        default=DiscountType.THRESHOLD,
        verbose_name='类型',
    )
    threshold = models.IntegerField(default=0, verbose_name='使用门槛（兴安币账务值）')
    amount = models.IntegerField(verbose_name='面额（兴安币账务值）')
    valid_to = models.DateTimeField(verbose_name='有效期至')
    total_qty = models.PositiveIntegerField(default=0, verbose_name='发放总量（0为不限）')
    claimed_qty = models.PositiveIntegerField(default=0, verbose_name='已领取数量')
    is_active = models.BooleanField(default=True, db_index=True, verbose_name='是否启用')
    sort_order = models.PositiveIntegerField(default=0, verbose_name='排序')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = '优惠券'
        verbose_name_plural = '优惠券'
        ordering = ['sort_order', '-created_at']

    def __str__(self):
        return self.name

    @property
    def is_claimable(self):
        if not self.is_active:
            return False
        if self.valid_to <= timezone.now():
            return False
        if self.total_qty and self.claimed_qty >= self.total_qty:
            return False
        return True


class UserCoupon(models.Model):
    """用户领取的优惠券记录。"""

    class Status(models.TextChoices):
        UNUSED = 'UNUSED', '未使用'
        USED = 'USED', '已使用'
        EXPIRED = 'EXPIRED', '已过期'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='coupons',
    )
    coupon = models.ForeignKey(
        Coupon,
        on_delete=models.CASCADE,
        related_name='user_coupons',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.UNUSED,
        db_index=True,
        verbose_name='状态',
    )
    order = models.ForeignKey(
        'orders.Order',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='used_coupons',
    )
    claimed_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = '用户优惠券'
        verbose_name_plural = '用户优惠券'
        unique_together = ('user', 'coupon')
        ordering = ['-claimed_at']

    def __str__(self):
        return f'{self.user_id} - {self.coupon.name}'

    @property
    def is_usable(self):
        if self.status != self.Status.UNUSED:
            return False
        return self.coupon.valid_to > timezone.now()
