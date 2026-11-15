"""Agreed, versioned changes to the people responsible for a paid add-on."""
from django.db import models


class OrderAddonHandover(models.Model):
    class Kind(models.TextChoices):
        STANDARD = 'STANDARD', '既有接替'
        VOLUNTARY = 'VOLUNTARY', '自愿接替'

    payment = models.ForeignKey('orders.OrderAddonPayment', on_delete=models.PROTECT, related_name='handovers')
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.STANDARD)
    previous = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    version = models.PositiveIntegerField()
    request_id = models.CharField(max_length=64)
    request_hash = models.CharField(max_length=64)
    created_by = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    source_segment = models.ForeignKey('orders.OrderParticipationSegment', on_delete=models.PROTECT, related_name='+')
    target_segment = models.ForeignKey('orders.OrderParticipationSegment', on_delete=models.PROTECT, related_name='+')
    source_basis_before = models.PositiveIntegerField()
    retained_basis = models.PositiveIntegerField()
    transferred_basis = models.PositiveIntegerField()
    completed_work = models.CharField(max_length=500, blank=True)
    remaining_work = models.CharField(max_length=500)
    status = models.CharField(max_length=12, default='PENDING', choices=[
        ('PENDING', '等待确认'), ('ACCEPTED', '已生效'), ('REJECTED', '有异议'), ('SUPERSEDED', '已替换')])
    created_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['payment', 'version'], name='addon_handover_version_unique'),
            models.UniqueConstraint(fields=['payment', 'request_id'], name='addon_handover_request_unique'),
            models.CheckConstraint(condition=models.Q(source_basis_before=models.F('retained_basis') +
                                                       models.F('transferred_basis')),
                                   name='addon_handover_basis_balanced'),
            models.CheckConstraint(condition=models.Q(transferred_basis__gt=0), name='addon_handover_transfer_positive'),
        ]


class OrderAddonHandoverShare(models.Model):
    handover = models.ForeignKey(OrderAddonHandover, on_delete=models.PROTECT, related_name='shares')
    segment = models.ForeignKey('orders.OrderParticipationSegment', on_delete=models.PROTECT, related_name='+')
    position = models.PositiveIntegerField()
    basis = models.PositiveIntegerField()
    income = models.PositiveIntegerField()
    commission_snapshot = models.JSONField()
    is_current = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['handover', 'position'], name='addon_handover_share_pos_unique'),
            models.CheckConstraint(condition=models.Q(basis__gt=0, income__lte=models.F('basis')),
                                   name='addon_handover_share_valid'),
        ]


class OrderAddonHandoverDecision(models.Model):
    handover = models.ForeignKey(OrderAddonHandover, on_delete=models.PROTECT, related_name='decisions')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    role = models.CharField(max_length=8, choices=[('BOSS', '老板'), ('PROVIDER', '陪玩')])
    accepted = models.BooleanField(null=True, default=None)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['handover', 'account'], name='addon_handover_decision_unique')]
