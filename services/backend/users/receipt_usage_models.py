"""Store-local, append-only accounting facts for a shared platform receipt."""
from django.db import models


class PassReceiptUsageState(models.Model):
    receipt = models.OneToOneField('users.PassRefundRepaymentReceipt', primary_key=True,
                                   on_delete=models.PROTECT, related_name='usage_state')
    version = models.PositiveBigIntegerField(default=0)
    pass_units = models.PositiveBigIntegerField(default=0)
    order_units = models.PositiveBigIntegerField(default=0)
    referral_units = models.PositiveBigIntegerField(default=0)
    referral_released_units = models.PositiveBigIntegerField(default=0)
    released_units = models.PositiveBigIntegerField(default=0)
    chain_hash = models.CharField(max_length=64, default='0' * 64)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(released_units__lte=models.F('order_units')),
                                               name='receipt_usage_release_lte_order')]


class PassReceiptUsageEvent(models.Model):
    state = models.ForeignKey(PassReceiptUsageState, on_delete=models.PROTECT, related_name='events')
    version = models.PositiveBigIntegerField()
    request_key = models.CharField(max_length=160)
    kind = models.CharField(max_length=16)
    allocation_key = models.CharField(max_length=160)
    amount = models.PositiveBigIntegerField()
    source_digest = models.CharField(max_length=64)
    previous_hash = models.CharField(max_length=64)
    event_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['state', 'version'], name='receipt_usage_event_version_uniq'),
            models.UniqueConstraint(fields=['state', 'request_key'], name='receipt_usage_event_request_uniq'),
            models.CheckConstraint(condition=models.Q(version__gt=0, amount__gt=0), name='receipt_usage_event_positive'),
            models.CheckConstraint(condition=models.Q(kind__in=['PASS', 'ORDER', 'RELEASE', 'REFERRAL', 'REFERRAL_RELEASE']), name='receipt_usage_event_kind'),
        ]
