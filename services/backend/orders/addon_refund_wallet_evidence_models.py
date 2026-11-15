"""Locked wallet state used when a refund must draw from deposit."""
from django.db import models


class AddonRefundZeroWalletEvidence(models.Model):
    refund = models.OneToOneField('orders.OrderAddonRefund', on_delete=models.PROTECT,
                                  related_name='zero_wallet_evidence')
    wallet = models.ForeignKey('wallet.Wallet', on_delete=models.PROTECT)
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT)
    business_cycle = models.PositiveIntegerField()
    balance = models.PositiveIntegerField()
    frozen_amount = models.PositiveIntegerField()
    is_active = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(balance=0, is_active=True),
                                               name='addon_refund_wallet_zero_active')]


class AddonRefundParticipantZeroWalletEvidence(models.Model):
    refund = models.ForeignKey('orders.OrderAddonRefund', on_delete=models.PROTECT,
                               related_name='participant_zero_wallet_evidence')
    participant = models.OneToOneField('orders.OrderAddonRefundParticipant', on_delete=models.PROTECT,
                                       related_name='zero_wallet_evidence')
    wallet = models.ForeignKey('wallet.Wallet', on_delete=models.PROTECT)
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT)
    business_cycle = models.PositiveIntegerField()
    balance = models.PositiveIntegerField()
    frozen_amount = models.PositiveIntegerField()
    is_active = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(balance=0, is_active=True),
                                               name='addon_participant_wallet_zero')]
