"""Durable evidence for a points-order funding adjustment on transfer."""
from django.db import models


class OrderPointsTransferFundingRevision(models.Model):
    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, related_name='points_transfer_funding_revisions')
    last_transfer = models.OneToOneField('orders.OrderOriginalShareTransfer', on_delete=models.PROTECT,
                                         related_name='funding_revision')
    previous_transfer_id = models.PositiveBigIntegerField(default=0)
    allocations = models.JSONField()
    before_funding = models.JSONField()
    after_funding = models.JSONField()
    evidence_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)


class OrderPointsTransferFundingRelease(models.Model):
    revision = models.ForeignKey(OrderPointsTransferFundingRevision, on_delete=models.PROTECT, related_name='releases')
    allocation = models.ForeignKey('orders.OrderPointsFundingAllocation', on_delete=models.PROTECT,
                                    related_name='transfer_releases')
    amount = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['revision', 'allocation'], name='pts_transfer_release_alloc_uniq'),
                       models.CheckConstraint(condition=models.Q(amount__gt=0), name='pts_transfer_release_positive')]
