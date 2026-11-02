"""Private staging records for a future direct WeChat order payment.

No public endpoints or real merchant calls are enabled by this model.
"""
from django.db import models


class DirectPaymentIntent(models.Model):
    class Status(models.TextChoices):
        AWAITING = 'AWAITING', '待支付'
        PAID = 'PAID', '已确认支付'
        REVIEW = 'REVIEW', '支付结果待处理'

    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='direct_payment')
    account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    client_request_id = models.CharField(max_length=64)
    request_fingerprint = models.CharField(max_length=64)
    merchant_order_no = models.CharField(max_length=32, unique=True)
    appid = models.CharField(max_length=32)
    mchid = models.CharField(max_length=32)
    openid = models.CharField(max_length=100)
    amount_fen = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default='CNY')
    # Channel evidence is separate from the merchant's order amount. None is
    # unknown, not zero and not an assertion that no coupon was used.
    payer_total_fen = models.BigIntegerField(null=True, blank=True)
    payer_currency = models.CharField(max_length=16, null=True, blank=True)
    promotion_detail = models.JSONField(null=True, blank=True)
    evidence_issue = models.CharField(max_length=255, blank=True, default='')
    evidence_risk_at = models.DateTimeField(null=True, blank=True)
    transaction_id = models.CharField(max_length=64, null=True, blank=True, unique=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.AWAITING)
    paid_at = models.DateTimeField(null=True, blank=True)
    review_reason = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['account', 'client_request_id'], name='direct_pay_account_request_uniq'),
            models.CheckConstraint(condition=models.Q(amount_fen__gt=0), name='direct_pay_positive_amount'),
            models.CheckConstraint(condition=models.Q(currency='CNY'), name='direct_pay_currency_cny'),
            models.CheckConstraint(condition=models.Q(status__in=['AWAITING', 'PAID', 'REVIEW']), name='direct_pay_status_valid'),
            models.CheckConstraint(condition=(~models.Q(status='PAID') | (
                models.Q(payer_total_fen__isnull=False, payer_total_fen__gte=0,
                         payer_total_fen__lte=models.F('amount_fen'), payer_currency__isnull=False, payer_currency='CNY')
            )), name='direct_pay_paid_payer_valid'),
            models.CheckConstraint(condition=(
                models.Q(status='AWAITING', transaction_id__isnull=True, paid_at__isnull=True)
                | models.Q(status__in=['PAID', 'REVIEW'], transaction_id__isnull=False, paid_at__isnull=False)
            ), name='direct_pay_receipt_state_valid'),
        ]
