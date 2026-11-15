"""Immutable evidence of a wallet-funded deposit against a store target."""
from django.db import models


class DepositPaymentSnapshot(models.Model):
    profile = models.ForeignKey('users.EscortProfile', on_delete=models.PROTECT, related_name='deposit_payment_snapshots')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    account_business_cycle = models.PositiveIntegerField(null=True, blank=True, editable=False)
    provider_transaction = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT, related_name='deposit_payment_snapshot')
    platform_transaction = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT, related_name='+')
    amount = models.PositiveIntegerField()
    paid_before = models.PositiveIntegerField()
    paid_after = models.PositiveIntegerField()
    target_snapshot = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0, target_snapshot__gt=0), name='dep_pay_snapshot_positive'),
            models.CheckConstraint(condition=models.Q(paid_after=models.F('paid_before')+models.F('amount')),
                                   name='dep_pay_snapshot_balanced'),
            models.CheckConstraint(condition=models.Q(paid_after__lte=models.F('target_snapshot')),
                                   name='dep_pay_snapshot_within_target'),
        ]
