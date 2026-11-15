"""Audited refund-caused deposit deficits and income-funded repayments."""
from django.db import models


class RefundDepositDeficit(models.Model):
    refund = models.ForeignKey('orders.OrderRefund', on_delete=models.PROTECT, related_name='deposit_deficits',
                               null=True, blank=True)
    addon_refund = models.ForeignKey('orders.OrderAddonRefund', on_delete=models.PROTECT, related_name='deposit_deficits',
                                     null=True, blank=True)
    profile = models.ForeignKey('users.EscortProfile', on_delete=models.PROTECT, related_name='refund_deposit_deficits')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    account_business_cycle = models.PositiveIntegerField(null=True, blank=True, editable=False)
    amount = models.PositiveIntegerField()
    remaining = models.PositiveIntegerField()
    source_provider_transaction = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT, related_name='+',
                                                       null=True, blank=True)
    source_store_transaction = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT, related_name='+')
    source_kind = models.CharField(max_length=12, default='PAIRED', choices=[('PAIRED', '成对冲正'), ('DIRECT', '押金直接承担')])
    direct_key = models.CharField(max_length=64, null=True, blank=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=['account', 'remaining', 'id'], name='refund_deposit_due_idx')]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name='refund_deposit_amount_positive'),
            models.CheckConstraint(condition=models.Q(remaining__lte=models.F('amount')), name='refund_deposit_remaining_valid'),
            models.CheckConstraint(condition=(models.Q(refund__isnull=False, addon_refund__isnull=True) |
                models.Q(refund__isnull=True, addon_refund__isnull=False)), name='refund_deposit_refund_kind_valid'),
            models.CheckConstraint(condition=(models.Q(source_kind='PAIRED', source_provider_transaction__isnull=False, direct_key__isnull=True) |
                models.Q(source_kind='DIRECT', source_provider_transaction__isnull=True, direct_key__isnull=False)), name='refund_deposit_source_valid')]


class RefundDepositIncomeApplication(models.Model):
    """One income event is considered at most once, including an empty result."""
    source_transaction = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT, related_name='refund_deposit_application')
    profile = models.ForeignKey('users.EscortProfile', on_delete=models.PROTECT, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    available_amount = models.PositiveIntegerField()
    applied_amount = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(applied_amount__lte=models.F('available_amount')),
                                               name='refund_deposit_income_within_available')]


class RefundDepositRepayment(models.Model):
    application = models.ForeignKey(RefundDepositIncomeApplication, on_delete=models.PROTECT, related_name='repayments')
    deficit = models.ForeignKey(RefundDepositDeficit, on_delete=models.PROTECT, related_name='repayments')
    amount = models.PositiveIntegerField()
    provider_transaction = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT, related_name='+')
    store_transaction = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['application', 'deficit'], name='refund_deposit_one_repayment'),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='refund_deposit_repayment_positive')]


class RefundDepositCashRepayment(models.Model):
    """Allocation of an audited offline top-up to an existing refund deficit."""
    topup = models.ForeignKey('wallet.CashDepositTopup', on_delete=models.PROTECT, related_name='refund_gap_repayments')
    deficit = models.ForeignKey(RefundDepositDeficit, on_delete=models.PROTECT, related_name='cash_repayments')
    amount = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['topup', 'deficit'], name='refund_deposit_cash_once'),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='refund_deposit_cash_positive')]


class RefundDepositRecoveryPermit(models.Model):
    """One extra service order while a refund-related deposit deficit remains."""
    profile = models.ForeignKey('users.EscortProfile', on_delete=models.PROTECT, related_name='refund_recovery_permits')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, related_name='refund_recovery_permits')
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    close_reason = models.CharField(max_length=24, blank=True, default='')
    income_application = models.ForeignKey('wallet.RefundDepositIncomeApplication', on_delete=models.PROTECT,
                                           null=True, blank=True, related_name='closed_recovery_permits')
    cash_topup = models.ForeignKey('wallet.CashDepositTopup', on_delete=models.PROTECT,
                                   null=True, blank=True, related_name='closed_recovery_permits')
    zero_income_settlement = models.OneToOneField('orders.OrderSegmentPersonSettlement', on_delete=models.PROTECT,
                                                   null=True, blank=True, related_name='refund_recovery_permit')
    zero_negotiated_share = models.OneToOneField('orders.NegotiationExecutionShare', on_delete=models.PROTECT,
                                                  null=True, blank=True, related_name='refund_recovery_permit')
    zero_points_refund_person = models.OneToOneField('orders.OrderPointsRefundSettlementPerson', on_delete=models.PROTECT,
                                                      null=True, blank=True, related_name='refund_recovery_permit')
    zero_ordinary_addon_person = models.OneToOneField('orders.OrderPointsSettlementPerson', on_delete=models.PROTECT,
                                                      null=True, blank=True, related_name='zero_refund_recovery_permit')
    zero_service_assignment = models.OneToOneField('orders.OrderProvider', on_delete=models.PROTECT,
                                                    null=True, blank=True, related_name='zero_refund_recovery_permit')
    zero_initial_points_person = models.OneToOneField('orders.OrderPointsSettlementPerson', on_delete=models.PROTECT,
                                                       null=True, blank=True, related_name='zero_initial_recovery_permit')
    zero_unaffected_assignment = models.OneToOneField('orders.OrderProvider', on_delete=models.PROTECT,
                                                       null=True, blank=True, related_name='zero_negotiation_recovery_permit')
    zero_unaffected_execution = models.ForeignKey('orders.NegotiationExecution', on_delete=models.PROTECT,
                                                   null=True, blank=True, related_name='zero_unaffected_recovery_permits')

    class Meta:
        indexes = [models.Index(fields=['account', 'closed_at', 'id'], name='refund_recovery_active_idx')]
        constraints = [models.CheckConstraint(condition=(
            models.Q(zero_unaffected_assignment__isnull=True, zero_unaffected_execution__isnull=True) |
            models.Q(zero_unaffected_assignment__isnull=False, zero_unaffected_execution__isnull=False,
                closed_at__isnull=False, close_reason='ZERO_UNAFFECTED_SETTLED', income_application__isnull=True,
                cash_topup__isnull=True, zero_income_settlement__isnull=True, zero_negotiated_share__isnull=True,
                zero_points_refund_person__isnull=True, zero_ordinary_addon_person__isnull=True,
                zero_service_assignment__isnull=True, zero_initial_points_person__isnull=True)),
            name='refund_recovery_unaffected_valid'),
            models.CheckConstraint(condition=(models.Q(zero_initial_points_person__isnull=True) |
            models.Q(closed_at__isnull=False, close_reason='ZERO_INITIAL_PTS_SETTLED', income_application__isnull=True,
                cash_topup__isnull=True, zero_income_settlement__isnull=True, zero_negotiated_share__isnull=True,
                zero_points_refund_person__isnull=True, zero_ordinary_addon_person__isnull=True,
                zero_service_assignment__isnull=True)), name='refund_recovery_init_points_valid'),
            models.CheckConstraint(condition=(models.Q(zero_service_assignment__isnull=True) |
            models.Q(closed_at__isnull=False, close_reason='ZERO_SERVICE_SETTLED', income_application__isnull=True,
                cash_topup__isnull=True, zero_income_settlement__isnull=True, zero_negotiated_share__isnull=True,
                zero_points_refund_person__isnull=True, zero_ordinary_addon_person__isnull=True)),
            name='refund_recovery_service_zero_valid'),
            models.CheckConstraint(condition=(models.Q(closed_at__isnull=True, close_reason='') |
            (models.Q(closed_at__isnull=False) & ~models.Q(close_reason=''))), name='refund_recovery_close_valid'),
            models.CheckConstraint(condition=(models.Q(cash_topup__isnull=True) |
                models.Q(closed_at__isnull=False, close_reason='CASH_GAP_PAID', income_application__isnull=True,
                    zero_income_settlement__isnull=True, zero_negotiated_share__isnull=True,
                    zero_points_refund_person__isnull=True, zero_ordinary_addon_person__isnull=True)),
                name='refund_recovery_cash_valid'),
            models.CheckConstraint(condition=(models.Q(zero_negotiated_share__isnull=True) |
                models.Q(closed_at__isnull=False, close_reason='ZERO_NEGOTIATED_SETTLED',
                         income_application__isnull=True, zero_income_settlement__isnull=True)),
                name='refund_recovery_neg_zero_valid'),
            models.CheckConstraint(condition=(models.Q(zero_points_refund_person__isnull=True) |
                models.Q(closed_at__isnull=False, close_reason='ZERO_POINTS_REF_SETTLED', income_application__isnull=True,
                         zero_income_settlement__isnull=True, zero_negotiated_share__isnull=True)),
                name='refund_recovery_pts_zero_valid'),
            models.CheckConstraint(condition=(models.Q(zero_ordinary_addon_person__isnull=True) |
                models.Q(closed_at__isnull=False, close_reason='ZERO_ADDON_SETTLED', income_application__isnull=True,
                         zero_income_settlement__isnull=True, zero_negotiated_share__isnull=True,
                         zero_points_refund_person__isnull=True)), name='refund_recovery_addon_zero_valid')]
