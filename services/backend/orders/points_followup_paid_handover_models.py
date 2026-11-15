"""Immutable handover evidence for an already partially paid order."""
from django.db import models


class PointsPaidHandoverEvidence(models.Model):
    handover = models.OneToOneField('orders.OrderAddonHandover', on_delete=models.PROTECT,
                                    related_name='paid_settlement_evidence')
    manifest = models.ForeignKey('orders.OrderSegmentSettlementManifest', on_delete=models.PROTECT,
                                 related_name='paid_handover_events')
    funding_run = models.OneToOneField('orders.OrderAddonFundingReleaseRun', on_delete=models.PROTECT,
                                      related_name='paid_handover_evidence')
    snapshot = models.JSONField()
    evidence_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)


class PointsPaidHandoverSettlementRevision(models.Model):
    manifest = models.ForeignKey('orders.OrderSegmentSettlementManifest', on_delete=models.PROTECT,
                                 related_name='paid_handover_revisions')
    evidence = models.OneToOneField(PointsPaidHandoverEvidence, on_delete=models.PROTECT,
                                    related_name='settlement_revision')
    before_plan = models.JSONField()
    before_plan_hash = models.CharField(max_length=64)
    after_plan = models.JSONField()
    after_plan_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)


class PointsPaidCompletionSettlementRevision(models.Model):
    manifest = models.ForeignKey('orders.OrderSegmentSettlementManifest', on_delete=models.PROTECT,
                                 related_name='paid_completion_revisions')
    completion_log = models.OneToOneField('orders.OrderStatusLog', on_delete=models.PROTECT,
                                          related_name='paid_completion_revision')
    paid_snapshot = models.JSONField()
    paid_hash = models.CharField(max_length=64)
    before_plan = models.JSONField()
    before_plan_hash = models.CharField(max_length=64)
    after_plan = models.JSONField()
    after_plan_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
