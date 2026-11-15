from django.db import models


class PointsThirdTransferRevision(models.Model):
    """Funding and unpaid add-on liability frozen by an actual transfer."""
    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, related_name='+')
    transfer = models.OneToOneField('orders.OrderOriginalShareTransfer', on_delete=models.PROTECT, related_name='+')
    third = models.ForeignKey('orders.PointsRefundRoundEvidence', on_delete=models.PROTECT, related_name='+')
    previous_addon = models.ForeignKey('orders.PointsThirdAddonFundingRevision', null=True, blank=True,
                                      on_delete=models.PROTECT, related_name='+')
    previous_transfer = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    previous_round = models.ForeignKey('orders.PointsRefundRoundEvidence', null=True, blank=True,
                                      on_delete=models.PROTECT, related_name='+')
    snapshot = models.JSONField()
    evidence_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)


class PointsThirdTransferRelease(models.Model):
    revision = models.ForeignKey(PointsThirdTransferRevision, on_delete=models.PROTECT, related_name='releases')
    allocation = models.ForeignKey('orders.OrderPointsFundingAllocation', on_delete=models.PROTECT, related_name='+')
    amount = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['revision', 'allocation'], name='pts_third_transfer_release_unique')]
