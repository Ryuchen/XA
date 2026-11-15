from django.db import models


class OrderAddonFundingReleaseRun(models.Model):
    """Funding rebalance triggered atomically by a posted add-on refund."""

    refund = models.OneToOneField('orders.OrderAddonRefund', on_delete=models.PROTECT,
                                   related_name='funding_release_run', null=True, blank=True)
    handover = models.OneToOneField('orders.OrderAddonHandover', on_delete=models.PROTECT,
                                     related_name='funding_release_run', null=True, blank=True)
    payment = models.OneToOneField('orders.OrderAddonPayment', on_delete=models.PROTECT,
                                   related_name='funding_release_run', null=True, blank=True)
    prestart_handoff = models.OneToOneField('orders.OrderPrestartHandoff', on_delete=models.PROTECT,
                                           related_name='funding_release_run', null=True, blank=True)
    negotiation = models.OneToOneField('orders.NegotiationExecution', on_delete=models.PROTECT,
                                       related_name='addon_funding_release_run', null=True, blank=True)
    ordinary_transfer_revision = models.OneToOneField('orders.OrdinaryPostRefundTransferRevision', on_delete=models.PROTECT,
                                        related_name='funding_release_run', null=True, blank=True)
    required_amount = models.PositiveIntegerField()
    allocated_before = models.PositiveIntegerField()
    released_amount = models.PositiveIntegerField()
    allocation_snapshot = models.JSONField()
    refunded_main_snapshot = models.JSONField(null=True, blank=True)
    refunded_main_hash = models.CharField(max_length=64, blank=True, default='')
    prestart_snapshot = models.JSONField(null=True, blank=True)
    prestart_hash = models.CharField(max_length=64, blank=True, default='')
    ordinary_refund_snapshot = models.JSONField(null=True, blank=True)
    ordinary_refund_hash = models.CharField(max_length=64, blank=True, default='')
    ordinary_prestart_snapshot = models.JSONField(null=True, blank=True)
    ordinary_prestart_hash = models.CharField(max_length=64, blank=True, default='')
    points_addon_snapshot = models.JSONField(null=True, blank=True)
    points_addon_hash = models.CharField(max_length=64, blank=True, default='')
    points_payment_snapshot = models.JSONField(null=True, blank=True)
    points_payment_hash = models.CharField(max_length=64, blank=True, default='')
    points_handover_snapshot = models.JSONField(null=True, blank=True)
    points_handover_hash = models.CharField(max_length=64, blank=True, default='')
    points_followup_snapshot = models.JSONField(null=True, blank=True)
    points_followup_hash = models.CharField(max_length=64, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=(
            models.Q(refund__isnull=False, handover__isnull=True, payment__isnull=True, prestart_handoff__isnull=True, negotiation__isnull=True, ordinary_transfer_revision__isnull=True) |
            models.Q(refund__isnull=True, handover__isnull=False, payment__isnull=True, prestart_handoff__isnull=True, negotiation__isnull=True, ordinary_transfer_revision__isnull=True) |
            models.Q(refund__isnull=True, handover__isnull=True, payment__isnull=False, prestart_handoff__isnull=True, negotiation__isnull=True, ordinary_transfer_revision__isnull=True) |
            models.Q(refund__isnull=True, handover__isnull=True, payment__isnull=True, prestart_handoff__isnull=False, negotiation__isnull=True, ordinary_transfer_revision__isnull=True) |
            models.Q(refund__isnull=True, handover__isnull=True, payment__isnull=True, prestart_handoff__isnull=True, negotiation__isnull=False, ordinary_transfer_revision__isnull=True) |
            models.Q(refund__isnull=True, handover__isnull=True, payment__isnull=True, prestart_handoff__isnull=True, negotiation__isnull=True, ordinary_transfer_revision__isnull=False)), name='addon_release_single_cause')]


class OrderAddonFundingRelease(models.Model):
    run = models.ForeignKey(OrderAddonFundingReleaseRun, on_delete=models.PROTECT, related_name='releases')
    allocation = models.ForeignKey('orders.OrderPointsFundingAllocation', on_delete=models.PROTECT,
                                   related_name='addon_releases')
    amount = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['run', 'allocation'], name='addon_funding_release_unique'),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='addon_funding_release_positive'),
        ]


class OrderAddonSettlement(models.Model):
    """Add-on earnings posted alongside the responsible person's service settlement."""

    payment = models.ForeignKey('orders.OrderAddonPayment', on_delete=models.PROTECT,
                                related_name='settlements')
    segment = models.ForeignKey('orders.OrderParticipationSegment', on_delete=models.PROTECT, related_name='+')
    order_settlement = models.ForeignKey('orders.OrderPointsSettlement', on_delete=models.PROTECT,
                                         related_name='addon_settlements', null=True, blank=True)
    refund_settlement = models.ForeignKey('orders.OrderPointsRefundSettlement', on_delete=models.PROTECT,
                                          related_name='addon_settlements', null=True, blank=True)
    report = models.ForeignKey('wallet.ProviderReport', on_delete=models.PROTECT, related_name='+')
    amount = models.PositiveBigIntegerField()
    income_transaction = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT,
                                              related_name='+', null=True, blank=True)
    settled_at = models.DateTimeField()

    class Meta:
        constraints = [models.CheckConstraint(condition=(models.Q(order_settlement__isnull=False, refund_settlement__isnull=True) |
            models.Q(order_settlement__isnull=True, refund_settlement__isnull=False)), name='addon_settlement_one_parent'),
            models.CheckConstraint(condition=(models.Q(amount=0, income_transaction__isnull=True) |
            models.Q(amount__gt=0, income_transaction__isnull=False)), name='addon_settlement_income_valid'),
            models.UniqueConstraint(fields=['payment', 'segment'], name='addon_settlement_payment_segment_unique')]


class OrderOrdinaryRefundAddonSettlementEvidence(models.Model):
    """Verified participant and add-on economics for one funded reservation."""

    settlement = models.OneToOneField('orders.OrderPointsSettlement', on_delete=models.PROTECT,
                                      related_name='ordinary_refund_addon_evidence')
    execution = models.OneToOneField('orders.NegotiationExecution', on_delete=models.PROTECT,
                                     related_name='addon_settlement_evidence')
    facts = models.JSONField()
    facts_hash = models.CharField(max_length=64)
    rounding_transaction = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT,
                                                related_name='+', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class OrderSegmentAddonSettlement(models.Model):
    """An add-on share released with a transferred service participation."""

    payment = models.ForeignKey('orders.OrderAddonPayment', on_delete=models.PROTECT,
                                related_name='segment_settlements')
    segment = models.ForeignKey('orders.OrderParticipationSegment', on_delete=models.PROTECT, related_name='+')
    manifest = models.ForeignKey('orders.OrderSegmentSettlementManifest', on_delete=models.PROTECT,
                                 related_name='addon_settlements')
    report = models.ForeignKey('orders.OrderSegmentReport', on_delete=models.PROTECT, related_name='+')
    amount = models.PositiveBigIntegerField()
    income_transaction = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT,
                                              related_name='+', null=True, blank=True)
    settled_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['payment', 'segment'], name='segment_addon_payment_segment_unique'),
            models.CheckConstraint(condition=(models.Q(amount=0, income_transaction__isnull=True) |
                models.Q(amount__gt=0, income_transaction__isnull=False)), name='segment_addon_income_valid'),
        ]


class OrderSegmentAddonSettlementRevision(models.Model):
    """A posted add-on refund changes the unpaid portion of a segment plan."""

    manifest = models.ForeignKey('orders.OrderSegmentSettlementManifest', on_delete=models.PROTECT,
                                 related_name='addon_revisions')
    version = models.PositiveIntegerField()
    refund_ids = models.JSONField()
    before_plan = models.JSONField()
    before_plan_hash = models.CharField(max_length=64)
    after_plan = models.JSONField()
    after_plan_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['manifest', 'version'], name='segment_addon_revision_version_unique')]


class OrdinaryPaidAddonRefundEvidence(models.Model):
    """Original paid segment facts when another segment's add-on is refunded."""
    refund = models.OneToOneField('orders.OrderAddonRefund', on_delete=models.PROTECT, related_name='ordinary_paid_evidence')
    manifest = models.ForeignKey('orders.OrderSegmentSettlementManifest', on_delete=models.PROTECT,
                                 related_name='ordinary_paid_addon_refunds')
    snapshot = models.JSONField()
    snapshot_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)


class OrderAddonConsumption(models.Model):
    """The buyer's independently snapshotted VIP growth and points for an add-on."""

    payment = models.OneToOneField('orders.OrderAddonPayment', on_delete=models.PROTECT, related_name='consumption')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    business_cycle = models.PositiveIntegerField()
    paid_units = models.PositiveBigIntegerField()
    refunded_units = models.PositiveBigIntegerField(default=0)
    principal_units = models.PositiveBigIntegerField()
    refunded_principal_units = models.PositiveBigIntegerField(default=0)
    vip_eligible = models.BooleanField(default=False)
    awarded_points = models.PositiveBigIntegerField(default=0)
    reversed_points = models.PositiveBigIntegerField(default=0)
    points_ledger = models.OneToOneField('engagement.PointLedger', on_delete=models.PROTECT,
                                        related_name='+', null=True, blank=True)
    license_decisions = models.JSONField(default=dict)
    confirmed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(refunded_units__lte=models.F('paid_units')),
                                   name='addon_consumption_refund_valid'),
            models.CheckConstraint(condition=models.Q(refunded_principal_units__lte=models.F('principal_units')),
                                   name='addon_consumption_principal_valid'),
            models.CheckConstraint(condition=models.Q(reversed_points__lte=models.F('awarded_points')),
                                   name='addon_consumption_points_valid'),
        ]
