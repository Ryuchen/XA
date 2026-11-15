from django.db import models


class ReferralRefundFunding(models.Model):
    """One verified external receipt reservation for a referral refund."""
    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, related_name='referral_refund_funding')
    receipt = models.ForeignKey('users.PassRefundRepaymentReceipt', on_delete=models.PROTECT,
                                related_name='referral_refund_allocations')
    amount = models.PositiveIntegerField()
    target_refund_amount = models.PositiveIntegerField()
    request_key = models.CharField(max_length=160, unique=True)
    allocated_by = models.CharField(max_length=64)
    refund = models.OneToOneField('orders.OrderRefund', on_delete=models.PROTECT,
                                  related_name='referral_funding', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name='referral_refund_funding_positive')]


class ReferralRefundFundingAction(models.Model):
    request_key = models.CharField(max_length=160, unique=True)
    allocation = models.OneToOneField(ReferralRefundFunding, on_delete=models.PROTECT, related_name='platform_action')
    actor = models.CharField(max_length=64)
    result = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)


class ReferralRefundFundingRelease(models.Model):
    allocation = models.OneToOneField(ReferralRefundFunding, on_delete=models.PROTECT, related_name='release')
    request_key = models.CharField(max_length=160, unique=True)
    actor = models.CharField(max_length=64)
    reason = models.CharField(max_length=200)
    result = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
