from django.db import models


class OrderWorkStint(models.Model):
    """Provider's real service entry, independent of money allocation."""
    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, related_name='work_stints')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    incoming = models.OneToOneField('orders.OrderTransferSeatEvent', null=True, blank=True,
                                    on_delete=models.PROTECT, related_name='work_stint')
    started_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    entry_image = models.ImageField(upload_to='reports/work_stints/', null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=['order', 'account'], name='work_stint_order_account_idx')]
