"""Opt-in capture of the existing segment facts for principal-only checkout."""
from django.conf import settings


def enabled():
    return bool(getattr(settings, 'DEFAULT_PRINCIPAL_SEGMENTS_ENABLED', False)
        and getattr(settings, 'DEFAULT_PRINCIPAL_SERVICE_FUNDING_ENABLED', False)
        and getattr(settings, 'ORDINARY_SERVICE_FUNDING_ENABLED', False)
        and not getattr(settings, 'BOSS_FUNDING_SELECTION_ENABLED', False))


def enabled_for_order(order):
    """Recognize a previously paid, narrowly scoped principal-only service."""
    if (not order or not order.pk or order.is_gift_snapshot or order.source_order_id
            or not order.customer_account_id or order.amount <= 0
            or order.payment_status != order.PaymentStatus.PAID or order.funding_terms is None
            or order.refund_records.exists() or order.addon_intents.filter(payment__isnull=False).exists()):
        return False
    from .models import OrderOriginalAttribution, OrderPaidServiceScope, OrderSegmentFinancialCoverage
    from .points_service_payment import needs_integrated_processing
    from wallet.models import Transaction
    if needs_integrated_processing(order) or not OrderPaidServiceScope.objects.filter(order=order).exists():
        return False
    attribution = OrderOriginalAttribution.objects.filter(order=order).first()
    if not attribution or attribution.status not in ('PENDING', 'ACTIVE'):
        return False
    if not OrderSegmentFinancialCoverage.objects.filter(attribution=attribution,
            source='WALLET_NO_POINTS', deal_amount=order.amount, status__in=['PENDING', 'ACTIVE', 'REVIEW']).exists():
        return False
    sources = list(order.funding_allocations.select_related('lot'))
    if (not sources or sum(row.amount for row in sources) != order.amount
            or any(row.kind != 'PREPAID' or row.refunded_amount or row.amount <= 0
                   or row.lot.account_id != order.customer_account_id for row in sources)):
        return False
    from engagement.funding_wallet import validate_order_funding_sources
    from rest_framework.exceptions import ValidationError
    try:
        validate_order_funding_sources(order, sources)
    except ValidationError:
        return False
    payments = list(Transaction.objects.filter(order=order, tx_type='PAY', status='SUCCESS',
                    reversal_of__isnull=True).select_related('wallet')[:2])
    if len(payments) != 1:
        return False
    payment = payments[0]
    return bool(payment.wallet.account_id == order.customer_account_id
        and payment.wallet.user_id == order.customer_id and payment.amount == -order.amount
        and payment.balance_after == payment.balance_before + payment.amount and not payment.reversals.exists())
