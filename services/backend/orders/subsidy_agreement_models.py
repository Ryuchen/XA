"""Durable, versioned execution of an accepted subsidy agreement."""
from django.db import models


class SubsidyAgreementExecution(models.Model):
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='subsidy_agreement_execution')
    proposal = models.OneToOneField('orders.NegotiationProposal', on_delete=models.PROTECT, related_name='subsidy_execution')
    operator = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    request_id = models.CharField(max_length=64)
    terms_hash = models.CharField(max_length=64)
    shares = models.JSONField()
    shares_hash = models.CharField(max_length=64)
    accepted_at = models.DateTimeField()
    completion_at = models.DateTimeField()
    completed_before_agreement = models.BooleanField(default=False)
    processed_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)


class SubsidyTransferAgreementTerms(models.Model):
    """Frozen segment economics for one proposed safety-box termination."""
    proposal = models.OneToOneField('orders.NegotiationProposal', on_delete=models.PROTECT, related_name='subsidy_transfer_terms')
    segment = models.ForeignKey('orders.OrderParticipationSegment', on_delete=models.PROTECT, related_name='+')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    prior_income = models.PositiveBigIntegerField()
    original_income = models.PositiveBigIntegerField()
    retained_income = models.PositiveBigIntegerField()
    evidence = models.JSONField()
    evidence_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(retained_income__lte=models.F('original_income')),
                                               name='subsidy_transfer_retained_lte_original')]
