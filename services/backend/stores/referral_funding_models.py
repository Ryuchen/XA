import uuid
from django.db import models
from .models import DirectoryModel


class PlatformReferralFundingOperation(DirectoryModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField(unique=True)
    claim = models.ForeignKey('stores.PlatformPassRepaymentClaim', on_delete=models.PROTECT)
    actor = models.ForeignKey('stores.PlatformAccount', on_delete=models.PROTECT)
    order_id = models.PositiveBigIntegerField()
    amount = models.PositiveIntegerField()
    target_refund_amount = models.PositiveIntegerField()
    payload_digest = models.CharField(max_length=64)
    tenant_action_id = models.PositiveBigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejection_message = models.CharField(max_length=200, blank=True, default='')

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name='referral_funding_op_positive'),
            models.CheckConstraint(condition=(
                models.Q(tenant_action_id__isnull=True, completed_at__isnull=True, rejected_at__isnull=True, rejection_message='')
                | models.Q(tenant_action_id__isnull=False, completed_at__isnull=False, rejected_at__isnull=True, rejection_message='')
                | (models.Q(tenant_action_id__isnull=True, completed_at__isnull=True, rejected_at__isnull=False)
                   & ~models.Q(rejection_message=''))), name='referral_funding_op_result')]


class PlatformReferralFundingReleaseOperation(DirectoryModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField(unique=True)
    funding_operation = models.ForeignKey(PlatformReferralFundingOperation, on_delete=models.PROTECT)
    actor = models.ForeignKey('stores.PlatformAccount', on_delete=models.PROTECT)
    reason = models.CharField(max_length=200)
    tenant_release_id = models.PositiveBigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=200, blank=True, default='')

    class Meta:
        constraints = [models.CheckConstraint(condition=(
            models.Q(tenant_release_id__isnull=True, completed_at__isnull=True, rejected_at__isnull=True, rejection_reason='')
            | models.Q(tenant_release_id__isnull=False, completed_at__isnull=False, rejected_at__isnull=True, rejection_reason='')
            | (models.Q(tenant_release_id__isnull=True, completed_at__isnull=True, rejected_at__isnull=False)
               & ~models.Q(rejection_reason=''))), name='referral_release_op_result')]
