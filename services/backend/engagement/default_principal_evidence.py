"""Verify a first-release default payment before considering a funding bridge.

This verifier does not create formal allocations, refund money or authorize
settlement. The caller holds the locked order in a store transaction.
"""
from dataclasses import dataclass

from rest_framework.exceptions import ValidationError
from stores.db import transaction
from wallet.models import Transaction, Wallet

from .default_funding_refunds import verified_sources
from .models import DefaultPaymentPointsSnapshot


@dataclass(frozen=True)
class PrincipalPaymentEvidence:
    order_id: int
    payment_id: int
    wallet_id: int
    principal: int
    sources: tuple
    membership_terms: dict


def verify_locked(order):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('本金付款凭证核验须在订单事务内执行')
    from orders.financial_lock import lock_order_money
    from orders.models import Order, OrderOriginalShareTransfer
    from orders.points_service_payment import needs_integrated_processing

    if (not order.pk or order.is_gift_snapshot or order.source_order_id or not order.customer_account_id
            or order.payment_status != 'PAID' or order.amount <= 0
            or order.funding_terms is not None or order.funding_allocations.exists()
            or order.refund_records.exists() or order.addon_intents.filter(payment__isnull=False).exists()
            or Order.objects.filter(source_order=order, is_gift_snapshot=False).exists()
            or order.status_logs.filter(action='TRANSFER').exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
            or needs_integrated_processing(order)
            or any(value is None for value in (order.service_clock_version, order.service_aftersale_hours,
                                                order.service_freeze_hours, order.service_deposit_percent))):
        raise ValidationError('该订单不属于可核验的纯本金付款范围')
    lock_order_money()
    payments = list(Transaction.objects.select_for_update().filter(
        order=order, tx_type='PAY', status='SUCCESS', reversal_of__isnull=True).order_by('pk'))
    if len(payments) != 1:
        raise ValidationError('订单付款流水待核对')
    payment = payments[0]
    wallet = Wallet.objects.select_for_update().filter(pk=payment.wallet_id).first()
    if (not wallet or wallet.account_id != order.customer_account_id or wallet.user_id != order.customer_id
            or payment.amount != -order.amount or payment.balance_after != payment.balance_before + payment.amount
            or payment.reversals.exists()):
        raise ValidationError('订单付款钱包或流水待核对')
    snapshot = DefaultPaymentPointsSnapshot.objects.select_for_update().filter(order=order).first()
    if (not snapshot or type(snapshot.vip_enabled) is not bool or type(snapshot.points_enabled) is not bool
            or (snapshot.vip_tier_id_snapshot is None) != (snapshot.vip_level_snapshot is None)
            or snapshot.awarded_refunded_principal is not None):
        raise ValidationError('订单付款时会员快照待核对')
    sources = verified_sources(order, snapshot)
    if not sources or any(row.kind != 'PREPAID' or row.refunded_amount for row in sources):
        raise ValidationError('订单付款不是完整纯本金来源')
    terms = {'vip_enabled': snapshot.vip_enabled, 'points_enabled': snapshot.points_enabled,
             'vip_tier_id': snapshot.vip_tier_id_snapshot, 'vip_level': snapshot.vip_level_snapshot,
             'points_rate': snapshot.points_rate, 'birthday_multiplier': snapshot.birthday_multiplier}
    return PrincipalPaymentEvidence(order.pk, payment.pk, wallet.pk, sum(row.amount for row in sources),
        tuple((row.allocation.lot_id, row.amount) for row in sources), terms)
