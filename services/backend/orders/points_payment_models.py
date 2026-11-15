"""Immutable payment facts for a future service-order points checkout."""
from django.db import models


class OrderPointsRedemption(models.Model):
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='points_redemption')
    payment = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT, related_name='points_redemption')
    ledger = models.OneToOneField('engagement.PointLedger', on_delete=models.PROTECT, related_name='service_redemption')
    deal_units = models.PositiveIntegerField()
    paid_units = models.PositiveIntegerField()
    discount_units = models.PositiveIntegerField()
    points_hundredths = models.PositiveBigIntegerField()
    group_points_hundredths = models.PositiveIntegerField()
    group_coin_units = models.PositiveIntegerField()
    groups = models.PositiveIntegerField()
    max_ratio_basis_points = models.PositiveSmallIntegerField()
    config_version = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(paid_units__gte=10), name='order_pts_paid_positive'),
            models.CheckConstraint(condition=models.Q(discount_units__gte=10), name='order_pts_discount_positive'),
            models.CheckConstraint(condition=models.Q(deal_units=models.F('paid_units') + models.F('discount_units')),
                                   name='order_pts_deal_balanced'),
            models.CheckConstraint(condition=models.Q(groups__gt=0, group_points_hundredths__gt=0,
                                                        group_coin_units__gte=10, config_version__gt=0),
                                   name='order_pts_terms_positive'),
            models.CheckConstraint(condition=models.Q(points_hundredths=models.F('groups') * models.F('group_points_hundredths')),
                                   name='order_pts_points_balanced'),
            models.CheckConstraint(condition=models.Q(discount_units=models.F('groups') * models.F('group_coin_units')),
                                   name='order_pts_discount_balanced'),
            models.CheckConstraint(condition=models.Q(max_ratio_basis_points__lt=10000), name='order_pts_cap_below_100'),
        ]


class OrderPointsBenefitSnapshot(models.Model):
    """Payment-time membership terms, independently bound to the receipt."""
    redemption = models.OneToOneField(OrderPointsRedemption, on_delete=models.PROTECT, related_name='benefit_snapshot')
    terms = models.JSONField()
    evidence_hash = models.CharField(max_length=64)
    captured_at = models.DateTimeField(auto_now_add=True)


class OrderPointsRefund(models.Model):
    """Actual assets returned for one authorized order refund."""
    redemption = models.ForeignKey(OrderPointsRedemption, on_delete=models.PROTECT, related_name='refunds')
    refund = models.OneToOneField('orders.OrderRefund', on_delete=models.PROTECT, related_name='points_refund')
    coin_transaction = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT, null=True,
                                              related_name='points_refund')
    points_ledger = models.OneToOneField('engagement.PointLedger', on_delete=models.PROTECT, null=True,
                                          related_name='service_points_refund')
    equivalent_units = models.PositiveIntegerField()
    coin_units = models.PositiveIntegerField()
    points_hundredths = models.PositiveBigIntegerField()
    prepaid_units = models.PositiveIntegerField()
    gift_units = models.PositiveIntegerField()
    compensation_units = models.PositiveIntegerField()
    is_final = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(equivalent_units__gte=10), name='order_pts_refund_value_positive'),
            models.CheckConstraint(condition=models.Q(coin_units=models.F('prepaid_units') + models.F('gift_units')
                                                       + models.F('compensation_units')),
                                   name='order_pts_refund_sources_match'),
            models.CheckConstraint(condition=(models.Q(coin_units=0, coin_transaction__isnull=True)
                                               | models.Q(coin_units__gt=0, coin_transaction__isnull=False)),
                                   name='order_pts_refund_coin_tx_match'),
            models.CheckConstraint(condition=(models.Q(points_hundredths=0, points_ledger__isnull=True)
                                               | models.Q(points_hundredths__gt=0, points_ledger__isnull=False)),
                                   name='order_pts_refund_ledger_match'),
        ]


class OrderPointsFundingAllocation(models.Model):
    """A portion of one verified offline receipt earmarked for an order.

    Creation and settlement are exposed only in isolated tests. Ordinary and
    points orders share the receipt with pass-refund repayments.
    """
    request_key = models.CharField(max_length=64, unique=True)
    receipt = models.ForeignKey('users.PassRefundRepaymentReceipt', on_delete=models.PROTECT,
                                related_name='points_funding_allocations')
    redemption = models.ForeignKey(OrderPointsRedemption, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='funding_allocations')
    service_order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, null=True, blank=True,
                                      related_name='service_funding_allocations')
    amount = models.PositiveIntegerField()
    allocated_by = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0),
                                               name='order_points_funding_positive'),
                       models.CheckConstraint(condition=(models.Q(redemption__isnull=False, service_order__isnull=True)
                           | models.Q(redemption__isnull=True, service_order__isnull=False)), name='service_funding_one_order')]

    @property
    def funding_order_id(self):
        return self.redemption.order_id if self.redemption_id else self.service_order_id


class OrderPointsFundingAction(models.Model):
    """Durable tenant outcome used to recover one platform allocation intent."""
    request_key = models.CharField(max_length=64, unique=True)
    allocation = models.OneToOneField(OrderPointsFundingAllocation, on_delete=models.PROTECT, related_name='platform_action')
    actor = models.CharField(max_length=64)
    result = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)


class OrderPointsFundingRelease(models.Model):
    """An immutable release from an allocation after an actual refund."""
    posting = models.ForeignKey('orders.PointsNegotiationPosting', on_delete=models.PROTECT, related_name='funding_releases')
    allocation = models.ForeignKey(OrderPointsFundingAllocation, on_delete=models.PROTECT, related_name='releases')
    amount = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['posting', 'allocation'], name='pts_fund_release_post_alloc_uniq'),
                       models.CheckConstraint(condition=models.Q(amount__gt=0), name='pts_fund_release_positive')]


class OrderPointsFundingReleaseEvidence(models.Model):
    """Binds a release to the pre-refund reserved allocation snapshot."""
    posting = models.OneToOneField('orders.PointsNegotiationPosting', on_delete=models.PROTECT,
                                   related_name='funding_release_evidence')
    allocations = models.JSONField()
    original_provider_income = models.PositiveIntegerField()
    original_settlement_id = models.PositiveBigIntegerField(null=True, blank=True)
    addon_funding_snapshot = models.JSONField(null=True, blank=True)
    prior_addon_release_snapshot = models.JSONField(default=list, blank=True)
    expected_release = models.PositiveIntegerField()
    evidence_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)


class OrderPointsSettlement(models.Model):
    """Order-wide settlement sources and destination snapshots."""
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='points_settlement')
    redemption = models.OneToOneField(OrderPointsRedemption, on_delete=models.PROTECT, null=True, blank=True,
                                      related_name='settlement')
    paid_units = models.PositiveBigIntegerField()
    principal_units = models.PositiveBigIntegerField()
    recovery_units = models.PositiveBigIntegerField(default=0)
    gift_units = models.PositiveBigIntegerField()
    compensation_units = models.PositiveBigIntegerField()
    funding_units = models.PositiveBigIntegerField()
    provider_units = models.PositiveBigIntegerField()
    inviter_units = models.PositiveBigIntegerField()
    store_units = models.PositiveBigIntegerField()
    inviter_transaction = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT, null=True,
                                               related_name='points_settlement_inviter')
    store_transaction = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT, null=True,
                                             related_name='points_settlement_store')
    created_at = models.DateTimeField(auto_now_add=True)
    finalized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(
            condition=(models.Q(paid_units__gte=10) | models.Q(redemption__isnull=True, paid_units=0)) & models.Q(
                  paid_units=models.F('principal_units') + models.F('gift_units') + models.F('compensation_units'),
                  principal_units__exact=models.F('provider_units') + models.F('inviter_units') + models.F('store_units') - models.F('funding_units') - models.F('recovery_units')),
            name='points_settlement_balanced')]


class OrderPointsSettlementFunding(models.Model):
    settlement = models.ForeignKey(OrderPointsSettlement, on_delete=models.PROTECT, related_name='funding_uses')
    allocation = models.OneToOneField(OrderPointsFundingAllocation, on_delete=models.PROTECT, related_name='settlement_use')
    amount = models.PositiveBigIntegerField()

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name='points_settlement_funding_positive')]


class OrderPointsSettlementPerson(models.Model):
    settlement = models.ForeignKey(OrderPointsSettlement, on_delete=models.PROTECT, related_name='persons')
    segment = models.OneToOneField('orders.OrderParticipationSegment', on_delete=models.PROTECT, related_name='points_settlement_person')
    report = models.OneToOneField('wallet.ProviderReport', on_delete=models.PROTECT, related_name='points_settlement_person')
    income_transaction = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT, null=True,
                                               related_name='points_settlement_person')
    amount = models.PositiveBigIntegerField()
    settled_at = models.DateTimeField()

    class Meta:
        constraints = [models.CheckConstraint(condition=(models.Q(amount=0, income_transaction__isnull=True)
            | models.Q(amount__gt=0, income_transaction__isnull=False)), name='points_settlement_person_tx_match')]


class PointsFirstRefundPaidPersonEvidence(models.Model):
    """The already-paid original person and allocation state at first refund."""
    posting = models.OneToOneField('orders.PointsNegotiationPosting', on_delete=models.PROTECT,
                                   related_name='first_paid_person_evidence')
    person = models.OneToOneField(OrderPointsSettlementPerson, on_delete=models.PROTECT,
                                  related_name='first_refund_evidence')
    allocation_snapshot = models.JSONField()
    evidence_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
