"""Private test-only state for a recoverable simulated prepayment session."""
from django.db import models


class DirectPaymentSession(models.Model):
    class State(models.TextChoices):
        PREPARED = 'PREPARED', '未发送'
        SENDING = 'SENDING', '模拟发送中'
        UNKNOWN = 'UNKNOWN', '模拟结果待核对'
        PAYABLE = 'PAYABLE', '模拟会话可用'
        CANCEL_REQUESTED = 'CANCEL_REQUESTED', '已请求关闭'

    intent = models.OneToOneField('orders.DirectPaymentIntent', on_delete=models.PROTECT, related_name='test_session')
    appid = models.CharField(max_length=32)
    mchid = models.CharField(max_length=32)
    openid = models.CharField(max_length=100)
    merchant_order_no = models.CharField(max_length=32)
    amount_fen = models.PositiveIntegerField()
    currency = models.CharField(max_length=3)
    quote_hash = models.CharField(max_length=64)
    snapshot_hash = models.CharField(max_length=64)
    payment_deadline = models.DateTimeField()
    state = models.CharField(max_length=20, choices=State.choices, default=State.PREPARED)
    cancel_requested_at = models.DateTimeField(null=True, blank=True)
    cancel_token = models.CharField(max_length=32, blank=True, default='')
    fake_closed_seen_at = models.DateTimeField(null=True, blank=True)
    fake_payment_risk_at = models.DateTimeField(null=True, blank=True)
    attempt_token = models.CharField(max_length=32, blank=True, default='')
    lease_until = models.DateTimeField(null=True, blank=True)
    fake_prepay_id = models.CharField(max_length=64, blank=True, default='')
    recovery_failures = models.PositiveIntegerField(default=0)
    recovery_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    recovery_last_error = models.CharField(max_length=32, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(state__in=['PREPARED', 'SENDING', 'UNKNOWN', 'PAYABLE', 'CANCEL_REQUESTED']),
                                   name='direct_session_state_valid'),
            models.CheckConstraint(condition=models.Q(amount_fen__gt=0, currency='CNY'), name='direct_session_amount_valid'),
            models.CheckConstraint(condition=(models.Q(state='CANCEL_REQUESTED', cancel_requested_at__isnull=False)
                | ~models.Q(state='CANCEL_REQUESTED') & models.Q(cancel_requested_at__isnull=True)),
                name='direct_session_cancel_valid'),
        ]


class DirectPaymentSessionObservation(models.Model):
    """Bounded fake-only response recorded even if an owner's access changed."""
    session = models.ForeignKey(DirectPaymentSession, on_delete=models.PROTECT, related_name='observations')
    attempt_token = models.CharField(max_length=32)
    outcome = models.CharField(max_length=8)
    fake_prepay_id = models.CharField(max_length=64, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['session', 'attempt_token'], name='direct_session_observation_uniq'),
            models.CheckConstraint(condition=models.Q(outcome__in=['VALID', 'INVALID', 'ERROR']),
                                   name='direct_session_observation_outcome'),
        ]


class DirectPaymentTestQuery(models.Model):
    """One simulated query attempt; never a real provider response."""
    session = models.ForeignKey(DirectPaymentSession, on_delete=models.PROTECT, related_name='test_queries')
    request_id = models.CharField(max_length=64)
    attempt_token = models.CharField(max_length=32, unique=True)
    cancel_token = models.CharField(max_length=32, blank=True, default='')
    status = models.CharField(max_length=8, default='SENDING')
    lease_until = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=32, blank=True, default='')
    fake_evidence = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['session', 'request_id'], name='direct_test_query_request_uniq'),
            models.CheckConstraint(condition=models.Q(status__in=['SENDING', 'UNKNOWN', 'DONE', 'REVIEW']),
                                   name='direct_test_query_status_valid'),
        ]


class DirectPaymentTestClose(models.Model):
    """Single fake close attempt; ACK never proves a channel was closed."""
    session = models.OneToOneField(DirectPaymentSession, on_delete=models.PROTECT, related_name='test_close')
    source_query = models.ForeignKey(DirectPaymentTestQuery, on_delete=models.PROTECT, related_name='+')
    request_id = models.CharField(max_length=64)
    attempt_token = models.CharField(max_length=32, unique=True)
    cancel_token = models.CharField(max_length=32)
    snapshot_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=8, default='SENDING')
    lease_until = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=32, blank=True, default='')
    late_outcome = models.CharField(max_length=32, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(status__in=['SENDING', 'UNKNOWN', 'ACK', 'REVIEW']),
                                              name='direct_test_close_status_valid')]
