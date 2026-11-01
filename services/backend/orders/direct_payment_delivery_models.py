"""Durable, test-only channel-notification inbox and paid-order outbox."""
from django.db import models


class DirectPaymentInbox(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', '待处理'
        DONE = 'DONE', '已处理'
        REVIEW = 'REVIEW', '待核对'

    merchant_id = models.CharField(max_length=32)
    notification_id = models.CharField(max_length=36)
    event_type = models.CharField(max_length=32)
    resource_type = models.CharField(max_length=32)
    original_type = models.CharField(max_length=32)
    payload_hash = models.CharField(max_length=64)
    controlled_hash = models.CharField(max_length=64)
    signed_body_hash = models.CharField(max_length=64)
    controlled_payload = models.JSONField()
    intent = models.ForeignKey('orders.DirectPaymentIntent', on_delete=models.PROTECT,
                               null=True, blank=True, related_name='payment_inboxes')
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING)
    has_conflict = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=80, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['merchant_id', 'notification_id'], name='direct_inbox_merchant_event_uniq'),
            models.CheckConstraint(condition=models.Q(status__in=['PENDING', 'DONE', 'REVIEW']), name='direct_inbox_status_valid'),
        ]


class DirectPaymentInboxConflict(models.Model):
    inbox = models.ForeignKey(DirectPaymentInbox, on_delete=models.PROTECT, related_name='conflicts')
    payload_hash = models.CharField(max_length=64)
    controlled_hash = models.CharField(max_length=64)
    signed_body_hash = models.CharField(max_length=64)
    controlled_payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['inbox', 'payload_hash'], name='direct_inbox_conflict_uniq')]


class DirectPaymentInboxObservation(models.Model):
    inbox = models.ForeignKey(DirectPaymentInbox, on_delete=models.PROTECT, related_name='observations')
    signed_body_hash = models.CharField(max_length=64)
    payload_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['inbox', 'signed_body_hash'], name='direct_inbox_signed_body_uniq')]


class DirectPaymentOutbox(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', '待处理'
        CLAIMED = 'CLAIMED', '处理中'
        DONE = 'DONE', '已处理'
        REVIEW = 'REVIEW', '待核对'

    intent = models.OneToOneField('orders.DirectPaymentIntent', on_delete=models.PROTECT, related_name='dispatch_outbox')
    pool_entered_at = models.DateTimeField()
    reject_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING, db_index=True)
    lease_token = models.CharField(max_length=32, blank=True, default='')
    lease_until = models.DateTimeField(null=True, blank=True, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=80, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(status__in=['PENDING', 'CLAIMED', 'DONE', 'REVIEW']), name='direct_outbox_status_valid')]
