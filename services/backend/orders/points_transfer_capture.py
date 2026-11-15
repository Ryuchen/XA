"""Isolated capture gate for points-paid service transfers.

This is only the first step of transfer integration: it does not allocate,
release or pay funds. The real endpoint stays closed outside the explicitly
disposable test databases until the rest of that workflow is complete.
"""
from django.conf import settings
from rest_framework.exceptions import ValidationError

from .points_checkout_test import require_isolated_checkout


def enabled():
    from .points_transfer_refund_access import permits
    if any(permits(phase=phase) for phase in ('transfer', 'report', 'refund', 'settlement')):
        return True
    if not getattr(settings, 'SERVICE_POINTS_TRANSFER_CAPTURE_TESTING', False):
        return False
    try:
        require_isolated_checkout()
    except ValidationError:
        return False
    return True


def locked_deal(order, coverage=None, *, allow_existing_allocations=False, allow_segment_records=False):
    """Prove the original payment, and return its pre-points deal amount."""
    if not enabled():
        raise ValidationError('积分订单转单尚未开放')
    from .points_transfer_refund_access import permits
    if any(permits(phase=phase) for phase in ('transfer', 'report', 'refund', 'settlement')) and not permits(order):
        raise ValidationError('积分转单上下文不属于当前订单')
    from .models import (OrderPlatformSubsidy, OrderPointsFundingAllocation,
                         OrderPointsRedemption, OrderPointsSettlement,
                         OrderSegmentSettlementManifest)
    from .direct_payment_models import DirectPaymentIntent
    from .points_service_payment import MARKER
    from .points_service_refund import _plan
    from wallet.models import Transaction
    receipt = OrderPointsRedemption.objects.select_for_update().filter(order=order).first()
    if (not receipt or not isinstance(order.funding_terms, dict)
            or order.funding_terms.get(MARKER) != {'receipt_id': receipt.pk}
            or receipt.deal_units != order.original_amount - order.boss_discount - order.promo_discount - order.coupon_discount
            or receipt.paid_units != order.amount or order.payment_status != order.PaymentStatus.PAID
            or order.refund_records.exists() or (order.settled_at and not allow_segment_records)
            or (order.providers.filter(settled_at__isnull=False).exists()
                and not (allow_segment_records and permits(order, phase='settlement')))
            or OrderPointsSettlement.objects.filter(order=order).exists()
            or (not allow_segment_records and OrderSegmentSettlementManifest.objects.filter(order=order).exists())
            or OrderPlatformSubsidy.objects.filter(order=order).exists()
            or DirectPaymentIntent.objects.filter(order=order).exists()
            # Allocation release/reallocation on transfer is not integrated
            # yet. Do not strand existing earmarked money in the meantime.
            or (not allow_existing_allocations and (
                OrderPointsFundingAllocation.objects.filter(redemption__order=order).exists()
                or OrderPointsFundingAllocation.objects.filter(service_order=order).exists()))
            or order.transactions.filter(status=Transaction.Status.SUCCESS, tx_type=Transaction.TxType.GIFT).exists()
            or (not allow_segment_records and order.transactions.filter(status=Transaction.Status.SUCCESS,
                tx_type__in=[Transaction.TxType.INCOME, Transaction.TxType.SHOP_INCOME]).exists())
            or (coverage and (coverage.source != 'WALLET_POINTS' or coverage.deal_amount != receipt.deal_units))):
        raise ValidationError('积分转单付款或现有收益凭证待核对')
    _plan(order, None, receipt.deal_units)
    return receipt.deal_units
