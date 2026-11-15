"""Membership confirmation evidence for a refunded points-paid order."""
from django.db import models


class RefundedPointsConsumptionEvidence(models.Model):
    settlement = models.OneToOneField('orders.OrderPointsRefundSettlement', on_delete=models.PROTECT,
                                      related_name='consumption_evidence')
    benefit_snapshot = models.ForeignKey('orders.OrderPointsBenefitSnapshot', on_delete=models.PROTECT, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT)
    business_cycle = models.PositiveIntegerField()
    paid_units = models.PositiveBigIntegerField()
    refunded_coin_units = models.PositiveBigIntegerField()
    net_paid_units = models.PositiveBigIntegerField()
    net_principal_units = models.PositiveBigIntegerField()
    refunded_principal_units = models.PositiveBigIntegerField()
    points_rate = models.PositiveIntegerField()
    birthday_multiplier = models.PositiveIntegerField()
    confirmation_vip_enabled = models.BooleanField()
    confirmation_points_enabled = models.BooleanField()
    vip_eligible = models.BooleanField()
    points_eligible = models.BooleanField()
    awarded_points = models.PositiveBigIntegerField()
    points_ledger = models.OneToOneField('engagement.PointLedger', on_delete=models.PROTECT,
                                         null=True, blank=True, related_name='+')
    confirmation = models.OneToOneField('engagement.ConsumptionConfirmation', on_delete=models.PROTECT,
                                        null=True, blank=True, related_name='+')
    resolved_at = models.DateTimeField()
    evidence_hash = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(paid_units=models.F('refunded_coin_units') + models.F('net_paid_units')),
                                   name='pts_ref_cons_paid_balanced'),
            models.CheckConstraint(condition=(models.Q(awarded_points=0, points_ledger__isnull=True)
                                             | models.Q(awarded_points__gt=0, points_ledger__isnull=False)),
                                   name='pts_ref_cons_ledger_match'),
            models.CheckConstraint(condition=(models.Q(points_eligible=False) | models.Q(vip_eligible=True)),
                                   name='pts_ref_cons_points_need_vip'),
            models.CheckConstraint(condition=(models.Q(confirmation__isnull=True, vip_eligible=False)
                                             | models.Q(confirmation__isnull=True, net_paid_units=0)
                                             | models.Q(confirmation__isnull=False, vip_eligible=True, net_paid_units__gt=0)),
                                   name='pts_ref_cons_confirmation_match'),
        ]


class TransferRefundPointsConsumptionEvidence(models.Model):
    """Net membership facts tied to a finalized transferred-order manifest."""
    manifest = models.OneToOneField('orders.OrderSegmentSettlementManifest', on_delete=models.PROTECT,
                                    related_name='refunded_consumption_evidence')
    posting = models.OneToOneField('orders.PointsNegotiationPosting', on_delete=models.PROTECT, related_name='+')
    benefit_snapshot = models.ForeignKey('orders.OrderPointsBenefitSnapshot', on_delete=models.PROTECT, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT)
    business_cycle = models.PositiveIntegerField()
    paid_units = models.PositiveBigIntegerField()
    refunded_coin_units = models.PositiveBigIntegerField()
    net_paid_units = models.PositiveBigIntegerField()
    net_principal_units = models.PositiveBigIntegerField()
    refunded_principal_units = models.PositiveBigIntegerField()
    points_rate = models.PositiveIntegerField()
    birthday_multiplier = models.PositiveIntegerField()
    confirmation_vip_enabled = models.BooleanField()
    confirmation_points_enabled = models.BooleanField()
    vip_eligible = models.BooleanField()
    points_eligible = models.BooleanField()
    awarded_points = models.PositiveBigIntegerField()
    points_ledger = models.OneToOneField('engagement.PointLedger', on_delete=models.PROTECT,
                                         null=True, blank=True, related_name='+')
    confirmation = models.OneToOneField('engagement.ConsumptionConfirmation', on_delete=models.PROTECT,
                                        null=True, blank=True, related_name='+')
    resolved_at = models.DateTimeField()
    evidence_hash = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(paid_units=models.F('refunded_coin_units') + models.F('net_paid_units')),
                                   name='tr_pts_cons_paid_balanced'),
            models.CheckConstraint(condition=(models.Q(awarded_points=0, points_ledger__isnull=True)
                                             | models.Q(awarded_points__gt=0, points_ledger__isnull=False)),
                                   name='tr_pts_cons_ledger_match'),
            models.CheckConstraint(condition=(models.Q(points_eligible=False) | models.Q(vip_eligible=True)),
                                   name='tr_pts_cons_points_need_vip'),
            models.CheckConstraint(condition=(models.Q(confirmation__isnull=True, vip_eligible=False)
                                             | models.Q(confirmation__isnull=True, net_paid_units=0)
                                             | models.Q(confirmation__isnull=False, vip_eligible=True, net_paid_units__gt=0)),
                                   name='tr_pts_cons_confirm_match'),
        ]
