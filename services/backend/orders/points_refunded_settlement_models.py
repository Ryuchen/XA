"""Separate settlement evidence for points orders after negotiated refunds."""
from django.db import models
from django.db.models.lookups import Exact


class OrderPointsRefundSettlement(models.Model):
    posting = models.OneToOneField('orders.PointsNegotiationPosting', on_delete=models.PROTECT,
                                   related_name='refunded_settlement')
    paid_units = models.PositiveBigIntegerField()
    principal_units = models.PositiveBigIntegerField()
    gift_units = models.PositiveBigIntegerField()
    compensation_units = models.PositiveBigIntegerField()
    addon_paid_units = models.PositiveBigIntegerField(default=0)
    addon_principal_units = models.PositiveBigIntegerField(default=0)
    addon_gift_units = models.PositiveBigIntegerField(default=0)
    addon_compensation_units = models.PositiveBigIntegerField(default=0)
    addon_recovered_units = models.PositiveBigIntegerField(default=0)
    addon_provider_units = models.PositiveBigIntegerField(default=0)
    recovered_units = models.PositiveBigIntegerField()
    funding_units = models.PositiveBigIntegerField()
    provider_units = models.PositiveBigIntegerField()
    inviter_units = models.PositiveBigIntegerField()
    store_units = models.PositiveBigIntegerField()
    inviter_transaction = models.OneToOneField('wallet.Transaction', null=True, blank=True,
                                                on_delete=models.PROTECT, related_name='+')
    store_transaction = models.OneToOneField('wallet.Transaction', null=True, blank=True,
                                              on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    finalized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(
            paid_units=models.F('principal_units') + models.F('gift_units') + models.F('compensation_units'),
            addon_paid_units=models.F('addon_principal_units') + models.F('addon_gift_units') + models.F('addon_compensation_units'),
            principal_units=models.F('provider_units') + models.F('addon_provider_units') + models.F('inviter_units') + models.F('store_units')
                            - models.F('addon_principal_units') - models.F('recovered_units')
                            - models.F('addon_recovered_units') - models.F('funding_units')),
            name='pts_ref_settlement_balanced')]


class OrderPointsRefundSettlementFunding(models.Model):
    settlement = models.ForeignKey(OrderPointsRefundSettlement, on_delete=models.PROTECT, related_name='funding_uses')
    allocation = models.ForeignKey('orders.OrderPointsFundingAllocation', on_delete=models.PROTECT, related_name='+')
    amount = models.PositiveBigIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['settlement', 'allocation'], name='pts_ref_settle_fund_uniq'),
                       models.CheckConstraint(condition=models.Q(amount__gt=0), name='pts_ref_settle_fund_positive')]


class OrderPointsRefundSettlementPerson(models.Model):
    settlement = models.ForeignKey(OrderPointsRefundSettlement, on_delete=models.PROTECT, related_name='persons')
    segment = models.OneToOneField('orders.OrderParticipationSegment', on_delete=models.PROTECT, related_name='+')
    report = models.OneToOneField('wallet.ProviderReport', on_delete=models.PROTECT, related_name='+')
    income_transaction = models.OneToOneField('wallet.Transaction', null=True, blank=True,
                                               on_delete=models.PROTECT, related_name='+')
    amount = models.PositiveBigIntegerField()
    settled_at = models.DateTimeField()

    class Meta:
        constraints = [models.CheckConstraint(condition=(models.Q(amount=0, income_transaction__isnull=True)
            | models.Q(amount__gt=0, income_transaction__isnull=False)), name='pts_ref_person_tx_match')]


class OrderPointsFollowupFundingRevision(models.Model):
    """Immutable funding facts after a second, actually posted refund."""
    posting = models.OneToOneField('orders.PointsNegotiationPosting', on_delete=models.PROTECT,
                                  related_name='followup_funding_revision')
    prior_settlement = models.ForeignKey(OrderPointsRefundSettlement, on_delete=models.PROTECT, related_name='+',
                                         null=True, blank=True)
    paid_person = models.ForeignKey(OrderPointsRefundSettlementPerson, on_delete=models.PROTECT, related_name='+',
                                    null=True, blank=True)
    allocations = models.JSONField()
    releases = models.JSONField()
    addon_snapshot = models.JSONField(null=True, blank=True)
    net_paid_units = models.PositiveBigIntegerField()
    principal_units = models.PositiveBigIntegerField()
    gift_units = models.PositiveBigIntegerField()
    compensation_units = models.PositiveBigIntegerField()
    recovered_units = models.PositiveBigIntegerField()
    provider_units = models.PositiveBigIntegerField()
    paid_provider_units = models.PositiveBigIntegerField()
    remaining_provider_units = models.PositiveBigIntegerField()
    inviter_units = models.PositiveBigIntegerField()
    store_units = models.PositiveBigIntegerField()
    prior_required_units = models.PositiveBigIntegerField()
    allocated_units = models.PositiveBigIntegerField()
    required_units = models.PositiveBigIntegerField()
    release_units = models.PositiveBigIntegerField()
    shortfall_units = models.PositiveBigIntegerField()
    evidence_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(
            net_paid_units=models.F('principal_units') + models.F('gift_units') + models.F('compensation_units'),
            provider_units=models.F('paid_provider_units') + models.F('remaining_provider_units'))
            & Exact(models.F('principal_units') + models.F('recovered_units') + models.F('allocated_units')
                    + models.F('shortfall_units'), models.F('provider_units') + models.F('inviter_units')
                    + models.F('store_units') + models.F('release_units')),
            name='pts_followup_revision_balanced'), models.CheckConstraint(
                condition=(models.Q(paid_person__isnull=True, paid_provider_units=0)
                           | models.Q(paid_person__isnull=False, prior_settlement__isnull=False)),
                name='pts_followup_prior_person_valid')]


class OrderPointsFollowupFundingReleaseEvidence(models.Model):
    """Marks completion of the revision's exact receipt releases, even zero."""
    revision = models.OneToOneField(OrderPointsFollowupFundingRevision, on_delete=models.PROTECT,
                                    related_name='release_evidence')
    created_at = models.DateTimeField(auto_now_add=True)


class OrderPointsTransferFollowupFundingRevision(models.Model):
    """A second refund's funding and prior paid-segment evidence."""
    posting = models.OneToOneField('orders.PointsNegotiationPosting', on_delete=models.PROTECT,
                                  related_name='transfer_followup_funding_revision')
    first_posting = models.ForeignKey('orders.PointsNegotiationPosting', on_delete=models.PROTECT, related_name='+')
    prior_manifest = models.JSONField(null=True, blank=True)
    allocations = models.JSONField()
    releases = models.JSONField()
    funding = models.JSONField()
    revision = models.JSONField()
    pending_handover_snapshot = models.JSONField(default=list)
    prior_required_units = models.PositiveBigIntegerField()
    evidence_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
