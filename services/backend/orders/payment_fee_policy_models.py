"""Administrative fee assumptions; saving these never enables payments."""
from django.db import models


class PaymentFeePolicy(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    revision = models.PositiveIntegerField(default=0)
    merchant_id = models.CharField(max_length=32, blank=True, default='')
    appid = models.CharField(max_length=32, blank=True, default='')
    rate_millionths = models.PositiveIntegerField(null=True, blank=True)
    fee_basis = models.CharField(max_length=20, blank=True, default='')
    rounding = models.CharField(max_length=10, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='payment_fee_policy_singleton')]


class PaymentFeePolicyHistory(models.Model):
    revision = models.PositiveIntegerField(unique=True)
    merchant_id = models.CharField(max_length=32)
    appid = models.CharField(max_length=32)
    rate_millionths = models.PositiveIntegerField()
    fee_basis = models.CharField(max_length=20)
    rounding = models.CharField(max_length=10)
    operator_account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    operator_name = models.CharField(max_length=150)
    created_at = models.DateTimeField(auto_now_add=True)
