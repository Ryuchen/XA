"""Encrypted quarantine for verified but unusable test callbacks.

This model is not a production callback store or a historical replay service.
"""
from django.db import models


class DirectCallbackQuarantine(models.Model):
    merchant_id = models.CharField(max_length=32)
    body_hash = models.CharField(max_length=64)
    platform_serial = models.CharField(max_length=128)
    platform_key_hash = models.CharField(max_length=64)
    encryption_key_id = models.CharField(max_length=64)
    body_nonce = models.BinaryField()
    encrypted_body = models.BinaryField()
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['merchant_id', 'body_hash', 'platform_key_hash'], name='direct_callback_quarantine_uniq'),
        ]


class DirectCallbackQuarantineObservation(models.Model):
    quarantine = models.ForeignKey(DirectCallbackQuarantine, on_delete=models.PROTECT, related_name='observations')
    receipt_hash = models.CharField(max_length=64)
    encryption_key_id = models.CharField(max_length=64)
    headers_nonce = models.BinaryField()
    encrypted_headers = models.BinaryField()
    reason = models.CharField(max_length=48)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['quarantine', 'receipt_hash'], name='direct_callback_observation_uniq'),
            models.CheckConstraint(condition=models.Q(reason__in=['AUTHENTICATED_INVALID', 'AUTHENTICATED_INBOX_REJECTED', 'AUTHENTICATED_ID_CONFLICT']),
                                   name='direct_callback_observation_reason'),
        ]


class DirectCallbackEnvelope(models.Model):
    """Test-only lock/identity for authenticated, unambiguously parsed IDs."""
    merchant_id = models.CharField(max_length=32)
    notification_id = models.CharField(max_length=36)
    has_conflict = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['merchant_id', 'notification_id'], name='direct_callback_envelope_uniq')]


class DirectCallbackEnvelopeBody(models.Model):
    envelope = models.ForeignKey(DirectCallbackEnvelope, on_delete=models.PROTECT, related_name='bodies')
    body_hash = models.CharField(max_length=64)
    quarantined = models.BooleanField(default=False)
    accepted = models.BooleanField(default=False)
    event_kind = models.CharField(max_length=8, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['envelope', 'body_hash'], name='direct_callback_envelope_body_uniq'),
            models.CheckConstraint(condition=models.Q(event_kind__in=['', 'PAYMENT', 'REFUND']),
                                   name='direct_callback_envelope_kind_valid'),
        ]
