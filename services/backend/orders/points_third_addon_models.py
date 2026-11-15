from django.db import models


class PointsThirdAddonFundingRevision(models.Model):
    """Immutable funding event for a payment, refund or handover after round three."""
    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, related_name='+')
    third = models.ForeignKey('orders.PointsRefundRoundEvidence', on_delete=models.PROTECT, related_name='+')
    payment = models.OneToOneField('orders.OrderAddonPayment', on_delete=models.PROTECT, related_name='+',
                                   null=True, blank=True)
    refund = models.OneToOneField('orders.OrderAddonRefund', on_delete=models.PROTECT, related_name='+',
                                  null=True, blank=True)
    handover = models.OneToOneField('orders.OrderAddonHandover', on_delete=models.PROTECT, related_name='+',
                                    null=True, blank=True)
    run = models.OneToOneField('orders.OrderAddonFundingReleaseRun', on_delete=models.PROTECT, related_name='+')
    previous = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    previous_transfer = models.ForeignKey('orders.PointsThirdTransferRevision', null=True, blank=True,
                                          on_delete=models.PROTECT, related_name='+')
    previous_round = models.ForeignKey('orders.PointsRefundRoundEvidence', null=True, blank=True,
                                       on_delete=models.PROTECT, related_name='+')
    snapshot = models.JSONField()
    evidence_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=(
            models.Q(payment__isnull=False, refund__isnull=True, handover__isnull=True) |
            models.Q(payment__isnull=True, refund__isnull=False, handover__isnull=True) |
            models.Q(payment__isnull=True, refund__isnull=True, handover__isnull=False)), name='pts_third_addon_event_cause')]
