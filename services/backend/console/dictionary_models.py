"""Operational reference data; financial rules remain in their own models."""
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models


class BusinessDictionaryItem(models.Model):
    class Type(models.TextChoices):
        SALARY_POSITION = 'SALARY_POSITION', '薪资岗位'
        AD_CHANNEL = 'AD_CHANNEL', '投流渠道'
        COST_CATEGORY = 'COST_CATEGORY', '运营成本类别'

    type = models.CharField(max_length=24, choices=Type.choices, db_index=True)
    code = models.CharField(max_length=64, validators=[RegexValidator(
        r'^[A-Z][A-Z0-9_]{0,63}$', '编码须以大写字母开头，仅含大写字母、数字和下划线',
    )])
    name = models.CharField(max_length=80)
    remark = models.CharField(max_length=500, blank=True, default='')
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'id']
        constraints = [
            models.UniqueConstraint(fields=['type', 'code'], name='business_dict_type_code_unique'),
            models.CheckConstraint(
                condition=models.Q(type__in=['SALARY_POSITION', 'AD_CHANNEL', 'COST_CATEGORY']),
                name='business_dict_type_valid',
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous = type(self).objects.filter(pk=self.pk).values('type', 'code').first()
            if previous and (previous['type'] != self.type or previous['code'] != self.code):
                raise ValidationError('字典类型和编码创建后不可修改；请修改名称或停用旧项')
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.get_type_display()} · {self.name}'
