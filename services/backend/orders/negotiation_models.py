"""Versioned negotiation proposals and participant decisions for a store order."""
from django.db import models


class NegotiationProposal(models.Model):
    case = models.ForeignKey('orders.AfterSale', on_delete=models.PROTECT, related_name='negotiation_proposals')
    case_opened_at = models.DateTimeField()
    case_request_id = models.CharField(max_length=64)
    version = models.PositiveIntegerField()
    request_id = models.CharField(max_length=64)
    created_by = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    affected_accounts = models.JSONField()
    affected_segment_ids = models.JSONField(null=True, blank=True, default=None)
    refund_units = models.PositiveIntegerField()
    provider_terms = models.JSONField()
    ordinary_segment_pending_plan = models.JSONField(null=True, blank=True)
    ordinary_paid_settlement_snapshot = models.JSONField(null=True, blank=True)
    explanation = models.TextField()
    terms_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=12, default='PENDING', choices=[
        ('PENDING', '等待确认'), ('ACCEPTED', '双方已确认'), ('REJECTED', '有异议'), ('SUPERSEDED', '已替换')])
    created_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['case', 'version'], name='neg_proposal_case_version_uniq'),
            models.UniqueConstraint(fields=['case', 'request_id'], name='neg_proposal_case_request_uniq'),
            models.CheckConstraint(condition=models.Q(version__gte=1), name='neg_proposal_version_positive'),
            models.CheckConstraint(condition=models.Q(status__in=['PENDING', 'ACCEPTED', 'REJECTED', 'SUPERSEDED']),
                                   name='neg_proposal_status_valid'),
        ]


class NegotiationDecision(models.Model):
    proposal = models.ForeignKey(NegotiationProposal, on_delete=models.PROTECT, related_name='decisions')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    role = models.CharField(max_length=8, choices=[('BOSS', '老板'), ('PROVIDER', '陪玩')])
    accepted = models.BooleanField(null=True, default=None)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['proposal', 'account'], name='neg_decision_account_uniq'),
            models.CheckConstraint(condition=models.Q(role__in=['BOSS', 'PROVIDER']), name='neg_decision_role_valid'),
            models.CheckConstraint(condition=(models.Q(accepted__isnull=True, decided_at__isnull=True)
                | models.Q(accepted__isnull=False, decided_at__isnull=False)), name='neg_decision_time_consistent'),
        ]


class PointsNegotiationTerms(models.Model):
    """Payment composition agreed for a points-refund proposal."""
    proposal = models.OneToOneField(NegotiationProposal, on_delete=models.PROTECT, related_name='points_terms')
    redemption = models.ForeignKey('orders.OrderPointsRedemption', on_delete=models.PROTECT, related_name='+')
    prior_refund = models.ForeignKey('orders.OrderPointsRefund', null=True, blank=True,
                                     on_delete=models.PROTECT, related_name='+')
    coin_units = models.PositiveIntegerField()
    points_hundredths = models.PositiveBigIntegerField()
    prepaid_units = models.PositiveIntegerField()
    gift_units = models.PositiveIntegerField()
    compensation_units = models.PositiveIntegerField()
    is_final = models.BooleanField(default=False)
    segment_pending_plan = models.JSONField(null=True, blank=True)
    addon_pending_plan = models.JSONField(null=True, blank=True)
    round_context = models.JSONField(null=True, blank=True)
    evidence_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(
            condition=models.Q(coin_units=models.F('prepaid_units') + models.F('gift_units') + models.F('compensation_units')),
            name='points_neg_terms_sources_match')]


class PointsNegotiationPosting(models.Model):
    """Test-only transactional evidence for returned points-order assets."""
    proposal = models.OneToOneField(NegotiationProposal, on_delete=models.PROTECT, related_name='points_posting')
    points_refund = models.OneToOneField('orders.OrderPointsRefund', on_delete=models.PROTECT, related_name='negotiation_posting')
    operator = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    terms_hash = models.CharField(max_length=64)
    accepted_at = models.DateTimeField()
    rounding_to_store_units = models.PositiveIntegerField(default=0)
    rounding_transaction = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT,
                                                 null=True, blank=True, related_name='+')
    participants = models.JSONField(default=list)
    participants_hash = models.CharField(max_length=64, blank=True, default='')
    termination_processed_at = models.DateTimeField(null=True, blank=True)
    termination_in_service = models.BooleanField(default=False)
    completed_by_negotiation = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class PointsNegotiationPostingShare(models.Model):
    posting = models.ForeignKey(PointsNegotiationPosting, on_delete=models.PROTECT, related_name='shares')
    assignment = models.ForeignKey('orders.OrderProvider', on_delete=models.PROTECT, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    original_income = models.PositiveIntegerField()
    burden = models.PositiveIntegerField()
    pending_deduction = models.PositiveIntegerField()
    wallet_available_before = models.PositiveIntegerField(default=0)
    deposit_paid_before = models.PositiveIntegerField(default=0)
    wallet_deduction = models.PositiveIntegerField()
    deposit_deduction = models.PositiveIntegerField()
    retained_income = models.PositiveIntegerField()
    wallet_transaction = models.OneToOneField('wallet.Transaction', null=True, blank=True,
                                               on_delete=models.PROTECT, related_name='+')
    deposit_deficit = models.OneToOneField('wallet.RefundDepositDeficit', null=True, blank=True,
                                          on_delete=models.PROTECT, related_name='+')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['posting', 'account'], name='pts_neg_post_account_uniq'),
            models.CheckConstraint(condition=models.Q(burden=models.F('pending_deduction') + models.F('wallet_deduction') + models.F('deposit_deduction')),
                                   name='pts_neg_post_burden_match'),
            models.CheckConstraint(condition=models.Q(original_income=models.F('pending_deduction') + models.F('retained_income')),
                                   name='pts_neg_post_income_match'),
            models.CheckConstraint(condition=(models.Q(wallet_deduction=0, wallet_transaction__isnull=True) |
                models.Q(wallet_deduction__gt=0, wallet_transaction__isnull=False)), name='pts_neg_post_wallet_match'),
            models.CheckConstraint(condition=(models.Q(deposit_deduction=0, deposit_deficit__isnull=True) |
                models.Q(deposit_deduction__gt=0, deposit_deficit__isnull=False)), name='pts_neg_post_deposit_match'),
        ]


class PointsNegotiationSegmentDeduction(models.Model):
    """A confirmed provider deduction tied to one actual service segment."""
    share = models.ForeignKey(PointsNegotiationPostingShare, on_delete=models.PROTECT,
                              related_name='segment_deductions')
    segment = models.ForeignKey('orders.OrderParticipationSegment', on_delete=models.PROTECT,
                                related_name='points_negotiation_deductions')
    amount = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['share', 'segment'], name='points_neg_share_segment_uniq'),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='points_neg_segment_amount_pos'),
        ]


class PointsNegotiationAddonDeduction(models.Model):
    """Actual unpaid add-on share consumed by a confirmed main refund."""
    share = models.ForeignKey(PointsNegotiationPostingShare, on_delete=models.PROTECT,
                              related_name='addon_deductions')
    payment = models.ForeignKey('orders.OrderAddonPayment', on_delete=models.PROTECT, related_name='+')
    segment = models.ForeignKey('orders.OrderParticipationSegment', on_delete=models.PROTECT, related_name='+')
    addon_share = models.ForeignKey('orders.OrderAddonHandoverShare', null=True, blank=True,
                                    on_delete=models.PROTECT, related_name='+')
    position = models.PositiveIntegerField()
    income_before = models.PositiveBigIntegerField()
    amount = models.PositiveBigIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['share', 'position'], name='pts_neg_addon_deduct_position'),
            models.CheckConstraint(condition=models.Q(amount__gt=0, income_before__gte=models.F('amount')),
                                   name='pts_neg_addon_deduct_amount'),
        ]


class NegotiationExecution(models.Model):
    """Accepted proposal execution; zero-refund termination has no money row."""
    proposal = models.OneToOneField(NegotiationProposal, on_delete=models.PROTECT, related_name='execution')
    refund = models.OneToOneField('orders.OrderRefund', on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='negotiation_execution')
    customer_transaction = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT, null=True, blank=True,
                                                related_name='+')
    zero_order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    zero_request_id = models.CharField(max_length=64, null=True, blank=True)
    zero_allocation = models.JSONField(default=list, blank=True)
    operator = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    terms_hash = models.CharField(max_length=64)
    allocation_hash = models.CharField(max_length=64)
    accepted_at = models.DateTimeField()
    rounding_to_store_units = models.PositiveIntegerField(default=0)
    termination_processed_at = models.DateTimeField(null=True, blank=True)
    completed_by_negotiation = models.BooleanField(default=False)
    unaffected_report_evidence = models.JSONField(default=dict)
    finalized_at = models.DateTimeField(null=True, blank=True)
    shop_transaction = models.OneToOneField('wallet.Transaction', null=True, blank=True,
                                            on_delete=models.PROTECT, related_name='+')
    rounding_transaction = models.OneToOneField('wallet.Transaction', null=True, blank=True,
                                                on_delete=models.PROTECT, related_name='+')
    inviter_transaction = models.OneToOneField('wallet.Transaction', null=True, blank=True,
                                               on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['zero_order', 'zero_request_id'], name='neg_zero_order_request_uniq'),
            models.CheckConstraint(condition=(
                models.Q(refund__isnull=False, customer_transaction__isnull=False,
                         zero_order__isnull=True, zero_request_id__isnull=True)
                | (models.Q(refund__isnull=True, customer_transaction__isnull=True,
                            zero_order__isnull=False, zero_request_id__isnull=False)
                   & ~models.Q(zero_request_id=''))), name='neg_exec_money_or_zero'),
        ]


class NegotiationExecutionShare(models.Model):
    """The affected person's actual burden and still-unsettled entitlement."""
    execution = models.ForeignKey(NegotiationExecution, on_delete=models.PROTECT, related_name='shares')
    assignment = models.ForeignKey('orders.OrderProvider', on_delete=models.PROTECT, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    original_income = models.PositiveIntegerField()
    burden = models.PositiveIntegerField()
    pending_deduction = models.PositiveIntegerField()
    wallet_deduction = models.PositiveIntegerField()
    deposit_deduction = models.PositiveIntegerField()
    retained_income = models.PositiveIntegerField()
    wallet_transaction = models.OneToOneField('wallet.Transaction', null=True, blank=True,
                                               on_delete=models.PROTECT, related_name='+')
    deposit_deficit = models.OneToOneField('wallet.RefundDepositDeficit', null=True, blank=True,
                                          on_delete=models.PROTECT, related_name='+')
    settled_at = models.DateTimeField(null=True, blank=True)
    income_transaction = models.OneToOneField('wallet.Transaction', null=True, blank=True,
                                             on_delete=models.PROTECT, related_name='+')
    settled_report = models.ForeignKey('wallet.ProviderReport', null=True, blank=True,
                                       on_delete=models.PROTECT, related_name='+')
    report_submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['execution', 'account'], name='neg_exec_share_account_uniq'),
            models.CheckConstraint(condition=models.Q(burden=models.F('pending_deduction') + models.F('wallet_deduction') + models.F('deposit_deduction')),
                                   name='neg_exec_burden_balanced'),
            models.CheckConstraint(condition=models.Q(original_income=models.F('pending_deduction') + models.F('retained_income')),
                                   name='neg_exec_income_balanced'),
            models.CheckConstraint(condition=(models.Q(wallet_deduction=0, wallet_transaction__isnull=True) |
                models.Q(wallet_deduction__gt=0, wallet_transaction__isnull=False)), name='neg_exec_wallet_evidence'),
            models.CheckConstraint(condition=(models.Q(deposit_deduction=0, deposit_deficit__isnull=True) |
                models.Q(deposit_deduction__gt=0, deposit_deficit__isnull=False)), name='neg_exec_deposit_evidence'),
            models.CheckConstraint(condition=(models.Q(settled_at__isnull=True, income_transaction__isnull=True) |
                models.Q(settled_at__isnull=False, retained_income=0, income_transaction__isnull=True) |
                models.Q(settled_at__isnull=False, retained_income__gt=0, income_transaction__isnull=False)),
                name='neg_exec_income_evidence'),
            models.CheckConstraint(condition=(models.Q(settled_at__isnull=True, settled_report__isnull=True, report_submitted_at__isnull=True) |
                models.Q(settled_at__isnull=False, settled_report__isnull=False, report_submitted_at__isnull=False)),
                name='neg_exec_report_evidence'),
        ]


class NegotiationFollowupLink(models.Model):
    """Auditable, linear link between two executed negotiation episodes."""
    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, related_name='negotiation_followups')
    previous = models.OneToOneField(NegotiationExecution, on_delete=models.PROTECT, related_name='next_followup')
    execution = models.OneToOneField(NegotiationExecution, on_delete=models.PROTECT, related_name='previous_followup')
    prior_refund_units = models.PositiveIntegerField()
    original_amount_units = models.PositiveIntegerField()
    previous_terms_hash = models.CharField(max_length=64)
    projected_ledger = models.JSONField()
    ledger_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=~models.Q(previous=models.F('execution')), name='neg_followup_distinct'),
            models.CheckConstraint(condition=models.Q(prior_refund_units__lte=models.F('original_amount_units')),
                                   name='neg_followup_refund_bounded'),
        ]
