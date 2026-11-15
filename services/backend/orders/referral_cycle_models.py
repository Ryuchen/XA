from django.db import models


class OrderReferralCycleSnapshot(models.Model):
    """The provider inviter's store membership when the order was paid."""
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='referral_cycle_snapshot')
    inviter = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    business_cycle = models.PositiveIntegerField()
