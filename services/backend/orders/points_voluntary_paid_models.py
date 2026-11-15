"""Refund-time evidence for already paid participants in a points order."""
from django.db import models


class PointsVoluntaryPaidRefundEvidence(models.Model):
    refund = models.OneToOneField('orders.OrderAddonRefund', on_delete=models.PROTECT,
                                  related_name='voluntary_paid_evidence')
    manifest = models.ForeignKey('orders.OrderSegmentSettlementManifest', on_delete=models.PROTECT,
                                 related_name='voluntary_paid_refunds')
    funding_run = models.OneToOneField('orders.OrderAddonFundingReleaseRun', on_delete=models.PROTECT,
                                      related_name='voluntary_paid_evidence')
    snapshot = models.JSONField()
    evidence_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)


class PointsVoluntaryPaidPaymentEvidence(models.Model):
    """Paid settlement and funding facts immediately before an add-on payment."""
    payment = models.OneToOneField('orders.OrderAddonPayment', on_delete=models.PROTECT,
                                   related_name='voluntary_paid_evidence')
    manifest = models.ForeignKey('orders.OrderSegmentSettlementManifest', on_delete=models.PROTECT,
                                 related_name='voluntary_paid_payments')
    funding_run = models.OneToOneField('orders.OrderAddonFundingReleaseRun', on_delete=models.PROTECT,
                                      related_name='voluntary_paid_payment_evidence')
    snapshot = models.JSONField()
    evidence_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)


class PointsVoluntaryPaidPaymentSettlementRevision(models.Model):
    manifest = models.ForeignKey('orders.OrderSegmentSettlementManifest', on_delete=models.PROTECT,
                                 related_name='voluntary_payment_revisions')
    evidence = models.OneToOneField(PointsVoluntaryPaidPaymentEvidence, on_delete=models.PROTECT,
                                    related_name='settlement_revision')
    payment_ids = models.JSONField()
    refund_ids = models.JSONField(default=list)
    before_plan = models.JSONField()
    before_plan_hash = models.CharField(max_length=64)
    after_plan = models.JSONField()
    after_plan_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
