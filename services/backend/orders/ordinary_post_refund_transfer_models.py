from django.db import models


class OrdinaryPostRefundTransferRevision(models.Model):
    """Frozen order economics after an additional transfer following refund."""
    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, related_name='ordinary_post_refund_transfers')
    transfer = models.ForeignKey('orders.OrderOriginalShareTransfer', on_delete=models.PROTECT,
                                 related_name='ordinary_post_refund_revisions')
    source_kind = models.CharField(max_length=20)
    source_id = models.PositiveBigIntegerField()
    previous = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='next_versions')
    snapshot = models.JSONField()
    snapshot_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['order', 'source_kind', 'source_id'],
                                               name='ordinary_refund_transfer_source_uniq')]
