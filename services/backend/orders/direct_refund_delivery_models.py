"""Test-only, durable observations of authenticated refund notifications."""
from django.db import models


class DirectRefundInbox(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', '等待渠道查询'
        DONE = 'DONE', '渠道状态已核对'
        REVIEW = 'REVIEW', '待人工核对'

    merchant_id = models.CharField(max_length=32)
    notification_id = models.CharField(max_length=36)
    event_type = models.CharField(max_length=32)
    resource_type = models.CharField(max_length=32)
    original_type = models.CharField(max_length=32)
    payload_hash = models.CharField(max_length=64)
    controlled_hash = models.CharField(max_length=64)
    signed_body_hash = models.CharField(max_length=64)
    controlled_payload = models.JSONField()
    refund = models.ForeignKey('orders.DirectPaymentRefund', on_delete=models.PROTECT, null=True, blank=True,
                               related_name='notification_inboxes')
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING, db_index=True)
    has_conflict = models.BooleanField(default=False)
    last_error = models.CharField(max_length=80, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['merchant_id', 'notification_id'], name='direct_refund_inbox_event_uniq'),
            models.CheckConstraint(condition=models.Q(status__in=['PENDING', 'DONE', 'REVIEW']), name='direct_refund_inbox_status'),
        ]


class DirectRefundInboxObservation(models.Model):
    inbox = models.ForeignKey(DirectRefundInbox, on_delete=models.PROTECT, related_name='observations')
    signed_body_hash = models.CharField(max_length=64)
    payload_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['inbox', 'signed_body_hash'], name='direct_refund_observation_uniq')]


class DirectRefundInboxConflict(models.Model):
    inbox = models.ForeignKey(DirectRefundInbox, on_delete=models.PROTECT, related_name='conflicts')
    payload_hash = models.CharField(max_length=64)
    controlled_hash = models.CharField(max_length=64)
    signed_body_hash = models.CharField(max_length=64)
    event_type = models.CharField(max_length=32)
    controlled_payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['inbox', 'payload_hash'], name='direct_refund_conflict_uniq')]
