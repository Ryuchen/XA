from django.db import models


class DepositRefundApplication(models.Model):
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    profile = models.ForeignKey('users.EscortProfile', on_delete=models.PROTECT, related_name='+')
    business_cycle = models.PositiveIntegerField()
    request_id = models.CharField(max_length=64)
    amount = models.PositiveBigIntegerField()
    note = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=12, default='PENDING')
    eligible_at = models.DateTimeField(null=True, blank=True)
    cleared_at = models.DateTimeField(null=True, blank=True)
    refund_transaction = models.OneToOneField('wallet.Transaction', null=True, blank=True,
        on_delete=models.PROTECT, related_name='+')
    operator = models.ForeignKey('club_accounts.ClubAccount', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    resolution = models.CharField(max_length=255, blank=True, default='')
    processed_at = models.DateTimeField(null=True, blank=True)
    notification_state = models.CharField(max_length=12, default='PENDING')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'request_id'], name='deposit_application_request_unique'),
                       models.CheckConstraint(condition=models.Q(amount__gt=0), name='deposit_application_amount_positive'),
                       models.CheckConstraint(condition=models.Q(status__in=['PENDING', 'REFUNDED', 'REJECTED', 'CANCELLED']),
                                              name='deposit_application_status_valid')]
