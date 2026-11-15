from django.db import models
from django.conf import settings


class OrderAddonRefundProposal(models.Model):
    """A separately agreed refund for one paid add-on."""

    payment = models.ForeignKey('orders.OrderAddonPayment', on_delete=models.PROTECT, related_name='refund_proposals')
    case = models.ForeignKey('orders.AfterSale', on_delete=models.PROTECT, related_name='addon_refund_proposals')
    case_opened_at = models.DateTimeField()
    case_request_id = models.CharField(max_length=64, blank=True)
    created_by = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    request_id = models.CharField(max_length=64)
    amount = models.PositiveIntegerField()
    source_plan = models.JSONField()
    cross_pending_plan = models.JSONField(null=True, blank=True)
    explanation = models.CharField(max_length=1000)
    status = models.CharField(max_length=12, default='PENDING', choices=[
        ('PENDING', '等待确认'), ('ACCEPTED', '双方已确认'),
        ('REJECTED', '有异议'), ('SUPERSEDED', '已替换')])
    created_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['payment', 'request_id'], name='addon_refund_request_unique')]


class OrderAddonRefundDecision(models.Model):
    proposal = models.ForeignKey(OrderAddonRefundProposal, on_delete=models.PROTECT, related_name='decisions')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    role = models.CharField(max_length=8, choices=[('BOSS', '老板'), ('PROVIDER', '陪玩')])
    accepted = models.BooleanField(null=True, default=None)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['proposal', 'account'], name='addon_refund_decision_unique')]


class OrderAddonRefundParticipant(models.Model):
    """Agreed responsibility and actual funding for one affected player."""
    proposal = models.ForeignKey(OrderAddonRefundProposal, on_delete=models.PROTECT, related_name='participants')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    basis = models.PositiveBigIntegerField()
    burden = models.PositiveBigIntegerField()
    excess_burden = models.PositiveBigIntegerField(default=0)
    share_plan = models.JSONField()
    addon_pending_deduction = models.PositiveBigIntegerField(default=0)
    main_pending_deduction = models.PositiveBigIntegerField(default=0)
    wallet_deduction = models.PositiveBigIntegerField(default=0)
    deposit_deduction = models.PositiveBigIntegerField(default=0)
    provider_transaction = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT,
        related_name='+', null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['proposal', 'account'], name='addon_refund_participant_unique'),
            models.CheckConstraint(condition=models.Q(basis__gt=0, burden__gt=0), name='addon_refund_participant_positive'),
        ]


class OrderAddonRefund(models.Model):
    """Posted refund and the agreed player's actual funding contributions."""

    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, related_name='addon_refunds')
    payment = models.ForeignKey('orders.OrderAddonPayment', on_delete=models.PROTECT, related_name='refund_records')
    proposal = models.OneToOneField(OrderAddonRefundProposal, on_delete=models.PROTECT, related_name='refund')
    operator = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    legacy_operator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True, blank=True)
    amount = models.PositiveIntegerField()
    addon_pending_deduction = models.PositiveIntegerField(default=0)
    main_pending_deduction = models.PositiveIntegerField(default=0)
    wallet_deduction = models.PositiveIntegerField(default=0)
    deposit_deduction = models.PositiveIntegerField(default=0)
    rounding_surplus = models.PositiveIntegerField(default=0)
    rounding_transaction = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT,
                                                 related_name='+', null=True, blank=True)
    buyer_transaction = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT, related_name='+')
    provider_transaction = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT,
                                                 related_name='+', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='addon_refund_amount_positive'),
            models.CheckConstraint(condition=models.Q(amount=models.F('addon_pending_deduction') +
                models.F('main_pending_deduction') + models.F('wallet_deduction') + models.F('deposit_deduction') -
                models.F('rounding_surplus')),
                name='addon_refund_burden_balanced'),
        ]


class OrderAddonRefundEarningDeduction(models.Model):
    """One refund can consume pending earnings from several add-ons of its order."""

    refund = models.ForeignKey(OrderAddonRefund, on_delete=models.PROTECT, related_name='earning_deductions')
    payment = models.ForeignKey('orders.OrderAddonPayment', on_delete=models.PROTECT,
                                related_name='refund_earning_deductions')
    amount = models.PositiveIntegerField()
    participant = models.ForeignKey(OrderAddonRefundParticipant, on_delete=models.PROTECT,
                                    related_name='earning_deductions')
    segment = models.ForeignKey('orders.OrderParticipationSegment', on_delete=models.PROTECT, related_name='+')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['refund', 'payment', 'participant', 'segment'], name='addon_refund_earning_person_unique'),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='addon_refund_earning_positive'),
        ]


class OrderAddonRefundShareAllocation(models.Model):
    """Refund basis and pending earnings assigned to a confirmed share snapshot."""
    refund = models.ForeignKey(OrderAddonRefund, on_delete=models.PROTECT, related_name='share_allocations')
    payment = models.ForeignKey('orders.OrderAddonPayment', on_delete=models.PROTECT, related_name='+')
    participant = models.ForeignKey(OrderAddonRefundParticipant, on_delete=models.PROTECT, related_name='+')
    share = models.ForeignKey('orders.OrderAddonHandoverShare', on_delete=models.PROTECT, related_name='+')
    refund_basis = models.PositiveBigIntegerField(default=0)
    earning_deduction = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['refund', 'participant', 'share'], name='addon_refund_share_person_unique'),
            models.CheckConstraint(condition=models.Q(refund_basis__gt=0) | models.Q(earning_deduction__gt=0),
                                   name='addon_refund_share_nonzero'),
        ]
