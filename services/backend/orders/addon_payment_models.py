from django.db import models


class OrderAddonPayment(models.Model):
    """Independent payment receipt; no earnings are released at payment time."""

    intent = models.OneToOneField('orders.OrderAddonIntent', on_delete=models.PROTECT, related_name='payment')
    buyer_transaction = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT, related_name='+')
    operator = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    inviter = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+',
                                null=True, blank=True)
    commission_fee = models.PositiveIntegerField()
    provider_income = models.PositiveIntegerField()
    inviter_income = models.PositiveIntegerField()
    inviter_rate = models.PositiveSmallIntegerField()
    store_income = models.PositiveIntegerField()
    principal = models.PositiveIntegerField()
    funding_required = models.PositiveIntegerField()
    benefit_terms = models.JSONField()
    paid_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(
                principal=models.F('provider_income') + models.F('inviter_income') + models.F('store_income')
                - models.F('funding_required')), name='addon_payment_funding_balance'),
            models.CheckConstraint(condition=models.Q(inviter_rate__range=(0, 100)), name='addon_payment_inviter_rate_valid'),
        ]


class OrderAddonFundingAllocation(models.Model):
    payment = models.ForeignKey(OrderAddonPayment, on_delete=models.PROTECT, related_name='allocations')
    lot = models.ForeignKey('engagement.PointFundingLot', on_delete=models.PROTECT, related_name='+')
    kind = models.CharField(max_length=16, choices=[('PREPAID', '本金'), ('GIFT', '赠送'),
                                                   ('COMPENSATION', '赔付')])
    amount = models.PositiveIntegerField()
    refunded_amount = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['payment', 'lot', 'kind'], name='addon_funding_source_unique'),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='addon_funding_amount_positive'),
            models.CheckConstraint(condition=models.Q(kind__in=['PREPAID', 'GIFT', 'COMPENSATION']),
                                   name='addon_funding_kind_valid'),
            models.CheckConstraint(condition=models.Q(refunded_amount__lte=models.F('amount')),
                                   name='addon_funding_refund_lte_amount'),
        ]
