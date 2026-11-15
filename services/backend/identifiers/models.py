from django.core.validators import RegexValidator
from django.db import models


class EntityNumber(models.Model):
    """Stable public identifier for one business entity in the current store database."""

    code = models.CharField(max_length=14, unique=True, validators=[
        RegexValidator(r'^[A-Z0-9]{2,4}[A-Z0-9]{10}$', '实体编号格式不正确'),
    ])
    entity_type = models.CharField(max_length=100, db_index=True)
    entity_id = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['entity_type', 'entity_id'], name='uniq_entity_number_type_id'),
        ]
        indexes = [
            models.Index(fields=['entity_type', 'entity_id'], name='entity_number_lookup_idx'),
        ]
        verbose_name = '实体编号'
        verbose_name_plural = '实体编号'

    def __str__(self):
        return self.code
