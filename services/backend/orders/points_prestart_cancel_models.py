from django.db import models


class PointsPrestartCancelFundingEvidence(models.Model):
    points_refund = models.OneToOneField('orders.OrderPointsRefund', on_delete=models.PROTECT,
                                         related_name='prestart_cancel_funding')
    store_slug = models.CharField(max_length=48)
    facts = models.JSONField()
    evidence_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)


class PointsPrestartCancelFundingRelease(models.Model):
    evidence = models.ForeignKey(PointsPrestartCancelFundingEvidence, on_delete=models.PROTECT,
                                 related_name='releases')
    allocation = models.OneToOneField('orders.OrderPointsFundingAllocation', on_delete=models.PROTECT,
                                      related_name='prestart_cancel_release')
    amount = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0),
                                               name='points_prestart_cancel_release_positive')]
