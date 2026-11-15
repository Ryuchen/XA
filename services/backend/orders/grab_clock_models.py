"""Store-local evidence for diagnosing grab clock continuity, not authorization."""
from django.db import models


class GrabClockDiagnosticState(models.Model):
    class Status(models.TextChoices):
        BASELINE = 'BASELINE', '仅有基线'
        OBSERVING = 'OBSERVING', '持续观察'
        FROZEN = 'FROZEN', '待核对'

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    status = models.CharField(max_length=12, choices=Status.choices)
    process_epoch = models.CharField(max_length=32, blank=True, default='')
    process_id = models.PositiveBigIntegerField(null=True, blank=True)
    database_uuid = models.CharField(max_length=64, blank=True, default='')
    connection_id = models.PositiveBigIntegerField(null=True, blank=True)
    server_uptime_seconds = models.PositiveBigIntegerField(null=True, blank=True)
    db_time = models.DateTimeField(null=True, blank=True)
    monotonic_before_ns = models.PositiveBigIntegerField(null=True, blank=True)
    monotonic_after_ns = models.PositiveBigIntegerField(null=True, blank=True)
    frozen_reason = models.CharField(max_length=64, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)


class GrabClockDiagnosticObservation(models.Model):
    state = models.ForeignKey(GrabClockDiagnosticState, on_delete=models.PROTECT, related_name='observations')
    process_epoch = models.CharField(max_length=32)
    process_id = models.PositiveBigIntegerField()
    database_uuid = models.CharField(max_length=64)
    connection_id = models.PositiveBigIntegerField()
    server_uptime_seconds = models.PositiveBigIntegerField()
    db_time = models.DateTimeField()
    monotonic_before_ns = models.PositiveBigIntegerField()
    monotonic_after_ns = models.PositiveBigIntegerField()
    verdict = models.CharField(max_length=12, choices=GrabClockDiagnosticState.Status.choices)
    reason = models.CharField(max_length=64, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
