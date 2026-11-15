"""Confirmed offline cash deposits; never credit the provider's wallet."""
import uuid
from pathlib import PurePath

from django.db import models


def cash_deposit_proof_path(instance, filename):
    return f'cash_deposit_proofs/{uuid.uuid4().hex}{PurePath(filename).suffix.lower()}'


class CashDepositTopup(models.Model):
    class Kind(models.TextChoices):
        INITIAL = 'INITIAL', '入驻现金押金'
        SUPPLEMENT = 'SUPPLEMENT', '现金押金补差'

    profile = models.ForeignKey('users.EscortProfile', on_delete=models.PROTECT, related_name='cash_deposit_topups')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='cash_deposit_topups')
    account_business_cycle = models.PositiveIntegerField(null=True, blank=True, editable=False)
    operator = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='recorded_cash_deposits')
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.SUPPLEMENT)
    source_registration = models.OneToOneField('users.ProviderRegistration', on_delete=models.PROTECT,
        null=True, blank=True, related_name='cash_deposit_record')
    amount = models.PositiveIntegerField()
    paid_before = models.PositiveIntegerField()
    paid_after = models.PositiveIntegerField()
    target_snapshot = models.PositiveIntegerField()
    reason = models.CharField(max_length=255)
    reference = models.CharField(max_length=100, blank=True, default='')
    reference_key = models.CharField(max_length=64, null=True, blank=True)
    proof_image = models.ImageField(upload_to=cash_deposit_proof_path, blank=True)
    proof_key = models.CharField(max_length=64, null=True, blank=True)
    client_request_id = models.CharField(max_length=64)
    platform_transaction = models.OneToOneField('wallet.Transaction', on_delete=models.PROTECT, related_name='cash_deposit_topup')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='cash_deposit_amount_positive'),
            models.UniqueConstraint(fields=['profile', 'reference_key'], name='unique_cash_deposit_profile_ref'),
            models.UniqueConstraint(fields=['operator', 'client_request_id'], name='unique_cash_deposit_operator_req'),
        ]
