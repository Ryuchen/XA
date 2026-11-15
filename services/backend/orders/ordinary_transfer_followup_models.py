from django.db import models


class OrdinaryTransferFollowupRound(models.Model):
    """Immutable evidence for one additional transferred-order agreement."""
    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, related_name='ordinary_transfer_followups')
    proposal = models.OneToOneField('orders.NegotiationProposal', on_delete=models.PROTECT,
                                    related_name='ordinary_transfer_followup')
    previous = models.ForeignKey('orders.NegotiationExecution', on_delete=models.PROTECT,
                                 related_name='ordinary_transfer_followup_successors')
    execution = models.OneToOneField('orders.NegotiationExecution', null=True, blank=True,
                                     on_delete=models.PROTECT, related_name='ordinary_transfer_followup_round')
    sequence = models.PositiveIntegerField()
    facts = models.JSONField()
    facts_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

