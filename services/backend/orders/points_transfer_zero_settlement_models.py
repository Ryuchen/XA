from django.db import models


class PointsTransferZeroSettlementRevision(models.Model):
    """One auditable manifest change caused by a zero-refund termination."""
    termination = models.OneToOneField('orders.PointsZeroFollowupTermination', on_delete=models.PROTECT,
        related_name='transfer_settlement_revision')
    manifest = models.OneToOneField('orders.OrderSegmentSettlementManifest', on_delete=models.PROTECT,
        related_name='points_zero_revision')
    before_plan = models.JSONField()
    before_plan_hash = models.CharField(max_length=64)
    after_plan = models.JSONField()
    after_plan_hash = models.CharField(max_length=64)
    paid_snapshot = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
