from django.db import models


class OrderAddonCompletion(models.Model):
    """Two-party confirmation that a terminated provider completed an add-on."""

    payment = models.ForeignKey('orders.OrderAddonPayment', on_delete=models.PROTECT, related_name='completion_requests')
    execution = models.ForeignKey('orders.NegotiationExecution', on_delete=models.PROTECT, related_name='+', null=True, blank=True)
    points_posting = models.ForeignKey('orders.PointsNegotiationPosting', on_delete=models.PROTECT,
                                       related_name='+', null=True, blank=True)
    segment = models.ForeignKey('orders.OrderParticipationSegment', on_delete=models.PROTECT, related_name='+')
    provider = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    buyer = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    created_by = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    request_id = models.CharField(max_length=64)
    completed_work = models.CharField(max_length=500)
    snapshot = models.JSONField()
    snapshot_hash = models.CharField(max_length=64)
    buyer_accepted = models.BooleanField(null=True, default=None)
    buyer_decided_at = models.DateTimeField(null=True, blank=True)
    provider_accepted = models.BooleanField(null=True, default=None)
    provider_decided_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, default='PENDING', choices=[
        ('PENDING', '等待确认'), ('ACCEPTED', '已确认'), ('REJECTED', '有异议'), ('SUPERSEDED', '已替换')])
    created_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['payment', 'request_id'], name='addon_completion_request_unique'),
            models.CheckConstraint(condition=(models.Q(execution__isnull=False, points_posting__isnull=True)
                | models.Q(execution__isnull=True, points_posting__isnull=False)), name='addon_completion_one_origin')]
