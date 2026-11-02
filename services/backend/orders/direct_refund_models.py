"""Unexposed staging records for WeChat-channel refunds."""
from django.db import models


class DirectPaymentRefund(models.Model):
    class Status(models.TextChoices):
        RESERVED = 'RESERVED', '已预留'
        UNKNOWN = 'UNKNOWN', '渠道结果待核对'
        PROCESSING = 'PROCESSING', '渠道处理中'
        SUCCESS = 'SUCCESS', '渠道退款成功'
        CLOSED = 'CLOSED', '渠道退款关闭'
        ABNORMAL = 'ABNORMAL', '渠道退款异常'
        REVIEW = 'REVIEW', '退款结果待核对'

    intent = models.ForeignKey('orders.DirectPaymentIntent', on_delete=models.PROTECT,
                               related_name='channel_refunds')
    request_id = models.CharField(max_length=64)
    merchant_refund_no = models.CharField(max_length=64, unique=True)
    channel_refund_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    amount_fen = models.PositiveIntegerField()
    total_fen = models.PositiveIntegerField()
    payer_total_fen = models.PositiveIntegerField(null=True, blank=True)
    payer_refund_fen = models.PositiveIntegerField(null=True, blank=True)
    settlement_total_fen = models.PositiveIntegerField(null=True, blank=True)
    settlement_refund_fen = models.PositiveIntegerField(null=True, blank=True)
    discount_refund_fen = models.PositiveIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3, default='CNY')
    reason = models.CharField(max_length=80)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RESERVED)
    success_at = models.DateTimeField(null=True, blank=True)
    conflict_previous_status = models.CharField(max_length=12, blank=True, default='')
    conflict_status = models.CharField(max_length=12, blank=True, default='')
    conflict_channel_refund_id = models.CharField(max_length=64, blank=True, default='')
    conflict_success_at = models.DateTimeField(null=True, blank=True)
    conflict_payer_total_fen = models.PositiveIntegerField(null=True, blank=True)
    conflict_payer_refund_fen = models.PositiveIntegerField(null=True, blank=True)
    conflict_settlement_total_fen = models.PositiveIntegerField(null=True, blank=True)
    conflict_settlement_refund_fen = models.PositiveIntegerField(null=True, blank=True)
    conflict_discount_refund_fen = models.PositiveIntegerField(null=True, blank=True)
    conflict_observed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['intent', 'request_id'], name='direct_refund_intent_request_uniq'),
            models.CheckConstraint(condition=models.Q(amount_fen__gt=0), name='direct_refund_amount_positive'),
            models.CheckConstraint(condition=models.Q(total_fen__gte=models.F('amount_fen')), name='direct_refund_total_valid'),
            models.CheckConstraint(condition=models.Q(currency='CNY'), name='direct_refund_currency_cny'),
            models.CheckConstraint(condition=(
                models.Q(payer_total_fen__isnull=True, payer_refund_fen__isnull=True)
                | models.Q(payer_total_fen__isnull=False, payer_refund_fen__isnull=False,
                           payer_total_fen__lte=models.F('total_fen'),
                           payer_refund_fen__lte=models.F('payer_total_fen'))
                  & models.Q(payer_refund_fen__lte=models.F('amount_fen'))
            ), name='direct_refund_payer_amounts'),
            models.CheckConstraint(condition=(
                models.Q(settlement_total_fen__isnull=True, settlement_refund_fen__isnull=True,
                         discount_refund_fen__isnull=True)
                | models.Q(settlement_total_fen__isnull=False, settlement_refund_fen__isnull=False,
                           discount_refund_fen__isnull=False, settlement_total_fen__lte=models.F('total_fen'),
                           settlement_refund_fen__lte=models.F('amount_fen'), discount_refund_fen__lte=models.F('amount_fen'))
            ), name='direct_refund_settlement_amounts'),
            models.CheckConstraint(condition=models.Q(status__in=['RESERVED', 'UNKNOWN', 'PROCESSING', 'SUCCESS', 'CLOSED', 'ABNORMAL', 'REVIEW']),
                                   name='direct_refund_status_valid'),
            models.CheckConstraint(condition=(models.Q(status='SUCCESS', success_at__isnull=False, channel_refund_id__isnull=False)
                                  | models.Q(status='REVIEW')
                                  | ~models.Q(status__in=['SUCCESS', 'REVIEW']) & models.Q(success_at__isnull=True)),
                                   name='direct_refund_success_evidence'),
        ]
