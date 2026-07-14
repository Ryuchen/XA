from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Promotion(models.Model):
    """促销活动：统一实体，双作用面。

    - discount_rate：对老板实付的活动折扣率(%)，100=不打折，90=九折。
      null 表示本活动不影响实付（纯抽成活动）。命中折扣率型活动时与优惠券互斥。
    - commission_rate：覆盖该单平台抽成率(%)。null 表示不覆盖抽成。
      抽成最终来源优先级：活动 > 陪玩等级 > 店铺(商品级/全局)。
    - 适用范围 scope：全场 / 指定分类 / 指定商品(M2M)。
    - 时间窗 start_at/end_at + priority：并发多活动时取优先级最高的一条。
    """

    class Scope(models.TextChoices):
        ALL = 'ALL', '全场'
        CATEGORY = 'CATEGORY', '指定分类'
        ITEMS = 'ITEMS', '指定商品'

    title = models.CharField(max_length=100, verbose_name='活动名称')
    remark = models.CharField(max_length=255, blank=True, default='', verbose_name='备注')
    discount_rate = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(100)],
        verbose_name='活动折扣率(%)',
    )
    commission_rate = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        verbose_name='覆盖抽成率(%)',
    )
    scope = models.CharField(
        max_length=16, choices=Scope.choices, default=Scope.ALL,
        db_index=True, verbose_name='适用范围',
    )
    category = models.ForeignKey(
        'orders.ServiceCategory', on_delete=models.PROTECT,
        null=True, blank=True, related_name='promotions',
        verbose_name='适用分类',
    )  # scope=CATEGORY 时对应的分类字典
    items = models.ManyToManyField(
        'orders.ServiceItem', blank=True, related_name='promotions',
        verbose_name='指定商品',
    )
    start_at = models.DateTimeField(verbose_name='开始时间')
    end_at = models.DateTimeField(verbose_name='结束时间')
    priority = models.IntegerField(default=0, db_index=True, verbose_name='优先级')  # 越大越优先
    is_active = models.BooleanField(default=True, db_index=True, verbose_name='是否启用')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-priority', '-start_at', 'id']
        verbose_name = '促销活动'
        verbose_name_plural = '促销活动'

    def __str__(self) -> str:
        return self.title

    def applies_to(self, service) -> bool:
        """判断该活动是否适用于指定商品（不含时间/启用判断，由查询层负责）。"""
        if self.scope == self.Scope.ALL:
            return True
        if self.scope == self.Scope.CATEGORY:
            return bool(self.category_id) and service.service_category_id == self.category_id
        if self.scope == self.Scope.ITEMS:
            return self.items.filter(id=service.id).exists()
        return False
