from django.db import models


class PointsPostRefundTransferRevision(models.Model):
    """Immutable points-funding version for one transfer after agreed refunds."""
    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, related_name='points_post_refund_transfers')
    transfer = models.OneToOneField('orders.OrderOriginalShareTransfer', on_delete=models.PROTECT, related_name='+')
    prior = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    snapshot = models.JSONField()
    evidence_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)


class PointsPostRefundTransferRelease(models.Model):
    revision = models.ForeignKey(PointsPostRefundTransferRevision, on_delete=models.PROTECT, related_name='releases')
    allocation = models.ForeignKey('orders.OrderPointsFundingAllocation', on_delete=models.PROTECT, related_name='+')
    amount = models.PositiveBigIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['revision', 'allocation'], name='pts_post_ref_trans_release_uniq'),
                       models.CheckConstraint(condition=models.Q(amount__gt=0), name='pts_post_ref_trans_release_positive')]


class PointsTransferAddonLiabilityRevision(models.Model):
    """A posted add-on refund changes one frozen transfer liability."""
    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, related_name='+')
    transfer_revision = models.ForeignKey(PointsPostRefundTransferRevision, on_delete=models.PROTECT, related_name='+')
    payment = models.ForeignKey('orders.OrderAddonPayment', on_delete=models.PROTECT, related_name='+')
    refund = models.ForeignKey('orders.OrderAddonRefund', on_delete=models.PROTECT, related_name='+')
    funding_run = models.ForeignKey('orders.OrderAddonFundingReleaseRun', on_delete=models.PROTECT, related_name='+')
    previous = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    snapshot = models.JSONField()
    evidence_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['refund', 'payment'], name='pts_addon_liability_ref_pay_uniq')]


class PointsFollowupTransferRevision(models.Model):
    """A new service-transfer funding fact following two points refunds."""
    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, related_name='+')
    transfer = models.OneToOneField('orders.OrderOriginalShareTransfer', on_delete=models.PROTECT, related_name='+')
    first_posting = models.ForeignKey('orders.PointsNegotiationPosting', on_delete=models.PROTECT, related_name='+')
    second_posting = models.ForeignKey('orders.PointsNegotiationPosting', on_delete=models.PROTECT, related_name='+')
    followup_revision = models.ForeignKey('orders.OrderPointsTransferFollowupFundingRevision', on_delete=models.PROTECT, related_name='+')
    snapshot = models.JSONField()
    evidence_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)



class PointsFollowupTransferRelease(models.Model):
    revision = models.ForeignKey(PointsFollowupTransferRevision, on_delete=models.PROTECT, related_name='releases')
    allocation = models.ForeignKey('orders.OrderPointsFundingAllocation', on_delete=models.PROTECT, related_name='+')
    amount = models.PositiveBigIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['revision', 'allocation'], name='pts_follow_trans_release_uniq'),
                       models.CheckConstraint(condition=models.Q(amount__gt=0), name='pts_follow_trans_release_pos')]
