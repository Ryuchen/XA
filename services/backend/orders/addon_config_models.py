from django.db import models


class ServiceAddonConfiguration(models.Model):
    """Store-local, explicitly reviewed relationship between two catalog items."""
    class ConditionMode(models.TextChoices):
        NONE = 'NONE', '无额外购买条件'
        STAFF_REVIEW = 'STAFF_REVIEW', '客服核实'

    service = models.ForeignKey('orders.ServiceItem', on_delete=models.PROTECT, related_name='addon_configurations')
    addon = models.ForeignKey('orders.ServiceItem', on_delete=models.PROTECT, related_name='main_service_configurations')
    enabled = models.BooleanField(default=False)
    conditions = models.CharField(max_length=500, blank=True, default='')
    condition_mode = models.CharField(max_length=16, choices=ConditionMode.choices,
                                      default=ConditionMode.STAFF_REVIEW)
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['service', 'addon'], name='service_addon_config_unique'),
            models.CheckConstraint(condition=~models.Q(service=models.F('addon')), name='service_addon_config_distinct'),
            models.CheckConstraint(condition=models.Q(version__gte=1), name='service_addon_config_version_positive'),
            models.CheckConstraint(condition=models.Q(condition_mode__in=['NONE', 'STAFF_REVIEW']),
                                   name='service_addon_condition_mode_valid'),
        ]


class ServiceAddonPriceOption(models.Model):
    class Unit(models.TextChoices):
        HOUR = 'HOUR', '小时'
        ROUND = 'ROUND', '局'
        ITEM = 'ITEM', '件'
        PACKAGE = 'PACKAGE', '份'

    configuration = models.ForeignKey(ServiceAddonConfiguration, on_delete=models.CASCADE, related_name='price_options')
    unit = models.CharField(max_length=12, choices=Unit.choices)
    price = models.PositiveIntegerField(help_text='每单位价格，按内部账务单位')
    enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['configuration', 'unit'], name='addon_option_unit_unique'),
            models.CheckConstraint(condition=models.Q(unit__in=['HOUR', 'ROUND', 'ITEM', 'PACKAGE']),
                                   name='addon_option_unit_valid'),
            models.CheckConstraint(condition=models.Q(price__gt=0), name='addon_option_price_positive'),
        ]
