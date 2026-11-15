from django.db import models


class ReservationPointsCancelFundingEvidence(models.Model):
    """Frozen pre-cancellation allocation and participation facts."""

    points_refund = models.OneToOneField('orders.OrderPointsRefund', on_delete=models.PROTECT,
                                         related_name='reservation_cancel_funding')
    store_slug = models.CharField(max_length=48)
    facts = models.JSONField()
    evidence_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)


class ReservationPointsCancelFundingRelease(models.Model):
    """Each original allocation is returned to its own receipt in full."""

    evidence = models.ForeignKey(ReservationPointsCancelFundingEvidence, on_delete=models.PROTECT,
                                 related_name='releases')
    allocation = models.OneToOneField('orders.OrderPointsFundingAllocation', on_delete=models.PROTECT,
                                      related_name='reservation_cancel_release')
    amount = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0),
                                               name='reservation_funding_release_positive')]
