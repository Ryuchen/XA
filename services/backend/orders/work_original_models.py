from django.db import models


class OrderWorkOriginalShare(models.Model):
    """Original-price attribution for one independently evidenced work stint."""
    stint = models.OneToOneField('orders.OrderWorkStint', on_delete=models.PROTECT, related_name='original_share_fact')
    accepted_at = models.DateTimeField()
    amount = models.PositiveBigIntegerField()


class OrderWorkOriginalTransfer(models.Model):
    """Staff-agreed split, bound to the real seat transfer and both stints."""
    event = models.OneToOneField('orders.OrderTransferSeatEvent', on_delete=models.PROTECT, related_name='work_original_transfer')
    previous = models.OneToOneField(OrderWorkOriginalShare, on_delete=models.PROTECT, related_name='transfer_out')
    incoming = models.OneToOneField(OrderWorkOriginalShare, on_delete=models.PROTECT, related_name='transfer_in')
    original_before = models.PositiveBigIntegerField()
    retained = models.PositiveBigIntegerField()
    transferred = models.PositiveBigIntegerField()
    agreement = models.CharField(max_length=500)
    operator = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField()

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(original_before=models.F('retained') + models.F('transferred')),
                                               name='work_original_transfer_conserved')]
