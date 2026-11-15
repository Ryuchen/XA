from django.db import models


class PointsRefundRoundEvidence(models.Model):
    """Immutable source snapshot for a later real points refund posting."""
    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, related_name='+')
    posting = models.OneToOneField('orders.PointsNegotiationPosting', on_delete=models.PROTECT, related_name='+')
    transfer_revision = models.ForeignKey('orders.PointsFollowupTransferRevision', on_delete=models.PROTECT, related_name='+')
    latest_transfer_version = models.ForeignKey('orders.PointsThirdTransferRevision', on_delete=models.PROTECT,
                                                null=True, blank=True, related_name='+')
    previous = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    snapshot = models.JSONField()
    evidence_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
