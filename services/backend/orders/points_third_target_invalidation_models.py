from django.db import models


class PointsThirdHandoverTargetInvalidation(models.Model):
    """A pending handover invalidated by the target's actual service event."""
    handover = models.OneToOneField('orders.OrderAddonHandover', on_delete=models.PROTECT, related_name='+')
    original_revision = models.ForeignKey('orders.PointsThirdTransferRevision', on_delete=models.PROTECT, related_name='+')
    transfer = models.ForeignKey('orders.OrderOriginalShareTransfer', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    posting = models.ForeignKey('orders.PointsNegotiationPosting', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    snapshot = models.JSONField()
    evidence_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=(
            models.Q(transfer__isnull=False, posting__isnull=True) |
            models.Q(transfer__isnull=True, posting__isnull=False)), name='pts_third_target_invalid_cause')]
