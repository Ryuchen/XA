from django.db import models


class PointsHandoverRefundInvalidation(models.Model):
    """Real refund event which made an unaccepted handover stale."""
    handover = models.OneToOneField('orders.OrderAddonHandover', on_delete=models.PROTECT,
        related_name='points_refund_invalidation')
    refund = models.ForeignKey('orders.OrderAddonRefund', on_delete=models.PROTECT,
        related_name='points_handover_invalidations')
    snapshot = models.JSONField()
    evidence_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
