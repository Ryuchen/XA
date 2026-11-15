"""Store-local customer-submitted service continuation and reward receipts."""
from django.db import models


class OrderContinuation(models.Model):
    original_order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, related_name='continuations')
    continuation_order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='continuation_claim')
    customer = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    business_cycle = models.PositiveIntegerField()
    request_id = models.CharField(max_length=64)
    status = models.CharField(max_length=12, default='PENDING')
    submitted_at = models.DateTimeField(auto_now_add=True)
    verified_by = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+', null=True)
    verified_at = models.DateTimeField(null=True)
    verification_note = models.CharField(max_length=1000, blank=True, default='')
    verification_request_id = models.CharField(max_length=64, blank=True, default='')
    participants = models.JSONField(null=True)
    reward_state = models.CharField(max_length=16, default='PENDING')
    reward_review_reason = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['customer', 'business_cycle', 'request_id'], name='continuation_customer_request_unique'),
            models.CheckConstraint(condition=models.Q(status__in=['PENDING', 'VERIFIED', 'REJECTED']), name='continuation_status_valid'),
            models.CheckConstraint(condition=~models.Q(original_order=models.F('continuation_order')), name='continuation_orders_distinct'),
        ]


class OrderContinuationReward(models.Model):
    continuation = models.ForeignKey(OrderContinuation, on_delete=models.PROTECT, related_name='rewards')
    provider = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    provider_business_cycle = models.PositiveIntegerField()
    transaction = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT, related_name='+')
    amount = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    notice_message = models.OneToOneField('site_messages.Message', on_delete=models.PROTECT, null=True,
                                          blank=True, related_name='+')
    notice_completed_at = models.DateTimeField(null=True, blank=True)
    notice_error = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['continuation', 'provider'], name='continuation_reward_provider_unique')]
