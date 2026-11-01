"""Immutable contract facts for the private direct-payment test domain."""
from django.db import models


class DirectPaymentQuote(models.Model):
    intent = models.OneToOneField('orders.DirectPaymentIntent', on_delete=models.PROTECT, related_name='fee_quote')
    contract_version = models.CharField(max_length=24, default='FEE_QUOTE_V2')
    policy_id = models.CharField(max_length=64)
    service_units = models.PositiveBigIntegerField(null=True, blank=True)
    conversion_version = models.CharField(max_length=64, blank=True, default='')
    units_per_yuan = models.PositiveBigIntegerField(null=True, blank=True)
    fen_per_yuan = models.PositiveBigIntegerField(null=True, blank=True)
    conversion_rounding = models.CharField(max_length=12, blank=True, default='')
    service_fen = models.PositiveBigIntegerField()
    fee_fen = models.PositiveBigIntegerField()
    total_fen = models.PositiveBigIntegerField()
    rate_numerator = models.PositiveBigIntegerField()
    rate_denominator = models.PositiveBigIntegerField()
    fee_basis = models.CharField(max_length=20)
    rounding = models.CharField(max_length=12)
    quote_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(service_fen__gt=0), name='direct_quote_service_positive'),
            models.CheckConstraint(condition=models.Q(total_fen=models.F('service_fen') + models.F('fee_fen')), name='direct_quote_total_conserved'),
            models.CheckConstraint(condition=models.Q(rate_denominator__gt=0, rate_numerator__lte=models.F('rate_denominator')), name='direct_quote_rate_valid'),
            models.CheckConstraint(condition=(~models.Q(contract_version='FEE_QUOTE_V2') | (
                models.Q(service_units__isnull=False, service_units__gt=0,
                         units_per_yuan__isnull=False, units_per_yuan__gt=0,
                         fen_per_yuan__isnull=False, fen_per_yuan=100, conversion_rounding='EXACT')
                & ~models.Q(conversion_version='')
            )), name='direct_quote_conversion_present'),
        ]
