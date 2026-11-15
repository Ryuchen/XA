"""Recorded zero-refund termination after an earlier points-order refund."""
from django.db import models


class PointsZeroFollowupTermination(models.Model):
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='points_zero_followup')
    proposal = models.OneToOneField('orders.NegotiationProposal', on_delete=models.PROTECT, related_name='points_zero_followup')
    prior_posting = models.ForeignKey('orders.PointsNegotiationPosting', on_delete=models.PROTECT, related_name='+')
    assignment = models.ForeignKey('orders.OrderProvider', on_delete=models.PROTECT, related_name='+')
    segment = models.ForeignKey('orders.OrderParticipationSegment', on_delete=models.PROTECT, related_name='+')
    operator = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    request_id = models.CharField(max_length=64)
    accepted_at = models.DateTimeField()
    funding_snapshot = models.JSONField()
    evidence_hash = models.CharField(max_length=64)
    terminated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
