"""Private simulated clearing records: neither balances nor channel cash."""
from django.db import models


class DirectClearingBatch(models.Model):
    """One immutable-by-service simulation for one fully paid service order."""
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='test_clearing')
    intent = models.OneToOneField('orders.DirectPaymentIntent', on_delete=models.PROTECT, related_name='test_clearing')
    version = models.CharField(max_length=16, default='SIM_V1')
    transaction_id = models.CharField(max_length=64)
    quote_hash = models.CharField(max_length=64)
    business_cycle = models.PositiveIntegerField()
    service_units = models.PositiveBigIntegerField()
    service_fen = models.PositiveBigIntegerField()
    fee_fen = models.PositiveBigIntegerField()
    total_fen = models.PositiveBigIntegerField()
    conversion_version = models.CharField(max_length=64)
    units_per_yuan = models.PositiveBigIntegerField()
    fen_per_yuan = models.PositiveBigIntegerField()
    snapshot = models.JSONField()
    snapshot_hash = models.CharField(max_length=64)
    lines_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(version='SIM_V1'), name='direct_clearing_version_valid'),
            models.CheckConstraint(condition=models.Q(service_units__gt=0, service_fen__gt=0), name='direct_clearing_service_positive'),
            models.CheckConstraint(condition=models.Q(total_fen=models.F('service_fen') + models.F('fee_fen')), name='direct_clearing_fen_conserved'),
            models.CheckConstraint(condition=models.Q(units_per_yuan__gt=0, fen_per_yuan=100), name='direct_clearing_conversion_valid'),
        ]


class DirectClearingLine(models.Model):
    """A positive debit or credit in one unit system, not a wallet entry."""
    batch = models.ForeignKey(DirectClearingBatch, on_delete=models.PROTECT, related_name='lines')
    line_key = models.CharField(max_length=96)
    book = models.CharField(max_length=8)
    side = models.CharField(max_length=8)
    account_code = models.CharField(max_length=32)
    recipient_user_id = models.PositiveBigIntegerField(null=True, blank=True)
    recipient_account_id = models.PositiveBigIntegerField(null=True, blank=True)
    amount = models.PositiveBigIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['batch', 'line_key'], name='direct_clearing_line_key_uniq'),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='direct_clearing_line_positive'),
            models.CheckConstraint(condition=models.Q(book__in=['FEN', 'UNIT']), name='direct_clearing_line_book_valid'),
            models.CheckConstraint(condition=models.Q(side__in=['DEBIT', 'CREDIT']), name='direct_clearing_line_side_valid'),
            models.CheckConstraint(condition=models.Q(account_code__in=['CHANNEL_RECEIVABLE', 'SERVICE_PENDING', 'FEE_HELD',
                'SERVICE_CLEARING', 'PROVIDER_DUE', 'INVITER_DUE', 'PLATFORM_SERVICE']), name='direct_clearing_line_account_valid'),
        ]
