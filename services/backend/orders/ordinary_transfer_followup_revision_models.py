from django.db import models


class OrdinaryTransferFollowupSettlementRevision(models.Model):
    """Settlement state preserved when a later agreement is executed."""
    round = models.OneToOneField('orders.OrdinaryTransferFollowupRound', on_delete=models.PROTECT,
                                 related_name='settlement_revision')
    manifest = models.ForeignKey('orders.OrderSegmentSettlementManifest', on_delete=models.PROTECT,
                                 null=True, blank=True, related_name='ordinary_followup_revisions')
    previous_plan = models.JSONField(null=True, blank=True)
    previous_plan_hash = models.CharField(max_length=64, blank=True, default='')
    settled_people = models.JSONField(default=list)
    rounding_transaction = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT,
                                               null=True, blank=True, related_name='+')
    facts_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
