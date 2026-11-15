from django.db import models


class OrderTransferNegotiationEvidence(models.Model):
    """Frozen segment economics for an ordinary transferred-order agreement."""
    execution = models.OneToOneField('orders.NegotiationExecution', on_delete=models.PROTECT,
                                     related_name='transfer_evidence')
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='transfer_negotiation_evidence')
    snapshot = models.JSONField()
    snapshot_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
