from django.conf import settings
from django.db import models


class Message(models.Model):
    class Type(models.TextChoices):
        SYSTEM = 'SYSTEM', '系统通知'
        ORDER = 'ORDER', '订单消息'
        SUPPORT = 'SUPPORT', '客服消息'
        PROMOTION = 'PROMOTION', '活动通知'

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='messages',
    )
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.SYSTEM, db_index=True)
    title = models.CharField(max_length=100)
    preview = models.CharField(max_length=255, blank=True, default='')
    detail = models.TextField(blank=True, default='')
    action_url = models.CharField(max_length=255, blank=True, default='')
    is_read = models.BooleanField(default=False, db_index=True)
    related_order_id = models.PositiveIntegerField(blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', 'is_read', '-created_at']),
        ]
        verbose_name = '站内消息'
        verbose_name_plural = '站内消息'

    def __str__(self):
        return f"Message<{self.recipient_id} {self.title}>"
