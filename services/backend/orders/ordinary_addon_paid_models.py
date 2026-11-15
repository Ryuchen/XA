from django.db import models


class OrdinaryAddonPaidRefundEvidence(models.Model):
    """Original paid people bound to a later ordinary add-on refund."""
    settlement = models.ForeignKey('orders.OrderPointsSettlement', on_delete=models.PROTECT, related_name='+')
    refund = models.OneToOneField('orders.OrderAddonRefund', on_delete=models.PROTECT, related_name='+')
    run = models.OneToOneField('orders.OrderAddonFundingReleaseRun', on_delete=models.PROTECT, related_name='+')
    snapshot = models.JSONField()
    evidence_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)


class OrdinaryRefundedAddonPaidRevision(models.Model):
    """Immutable before/after facts for a refund following paid ordinary income."""
    settlement = models.ForeignKey('orders.OrderPointsSettlement', on_delete=models.PROTECT, related_name='+')
    refund = models.OneToOneField('orders.OrderAddonRefund', on_delete=models.PROTECT, related_name='+')
    run = models.OneToOneField('orders.OrderAddonFundingReleaseRun', on_delete=models.PROTECT, related_name='+')
    snapshot = models.JSONField()
    evidence_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
