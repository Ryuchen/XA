"""Read-only proof of default-principal transferred-service consumption."""
from django.db.models import Sum
from rest_framework.exceptions import ValidationError
from stores.db import transaction
from wallet.models import Transaction
from .default_funding_refunds import verified_sources
from .models import DefaultPaymentPointsSnapshot


def _verify_addons(order, decision, manifest):
    """Keep add-on payment, settlement and independent consumption aligned."""
    from orders.addon_payment import verify_order_debit_links, verify_receipt
    from orders.models import OrderAddonConsumption, OrderAddonPayment
    verify_order_debit_links(order, table_exists=True, lock=True)
    payments = list(OrderAddonPayment.objects.select_for_update().filter(intent__order=order)
                    .select_related('intent__segment__attribution', 'buyer_transaction__wallet').order_by('pk'))
    if (isinstance(decision.license_evidence, dict)
            and decision.license_evidence.get('unpaid_addon_intents') is True and payments):
        raise ValidationError('主单实销时未付款的加购出现付款凭证，请先核对')
    terms = manifest.plan if isinstance(manifest.plan, dict) else {}
    funding = terms.get('ordinary_transfer_funding') or terms.get('points_funding') or {}
    if not isinstance(funding, dict):
        raise ValidationError('转单加购结算资金快照待核对')
    active, history = funding.get('addons', []), funding.get('addon_history', [])
    if (not isinstance(active, list) or not isinstance(history, list)
            or any(not isinstance(row, dict) or type(row.get('payment_id')) is not int
                   for row in active + history)):
        raise ValidationError('转单加购结算清单待核对')
    recorded = {row['payment_id'] for row in active + history}
    if recorded != {payment.pk for payment in payments}:
        raise ValidationError('转单加购付款与逐段结算清单不一致')
    for payment in payments:
        try:
            verify_receipt(payment)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ValidationError('转单加购付款或退款凭证格式不完整') from exc
        consumption = OrderAddonConsumption.objects.select_for_update().filter(payment=payment).first()
        refunds = list(payment.refund_records.all())
        returned_principal = payment.allocations.filter(kind='PREPAID').aggregate(
            value=Sum('refunded_amount'))['value'] or 0
        if (payment.intent.buyer_id != decision.account_id
                or payment.intent.segment.attribution.order_id != order.pk
                or not consumption or consumption.account_id != decision.account_id
                or consumption.business_cycle != decision.business_cycle
                or consumption.paid_units != payment.intent.amount
                or consumption.refunded_units != sum(row.amount for row in refunds)
                or consumption.principal_units != payment.principal
                or consumption.refunded_principal_units != returned_principal
                or consumption.confirmed_at < payment.paid_at
                or consumption.confirmed_at > decision.decided_at):
            raise ValidationError('转单加购的付款、退款或独立实销凭证不一致')


def verify_locked(order, decision):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('转单会员核对须在门店事务中')
    from orders.financial_lock import lock_order_money
    from orders.models import Order, OrderOriginalShareTransfer, OrderSegmentSettlementManifest
    from orders.segment_settlement import verify_finalized
    from orders.settlement import SettlementError
    if (not order.pk or order.is_gift_snapshot or order.funding_terms is not None or order.source_order_id
            or Order.objects.filter(source_order=order, is_gift_snapshot=False).exists()
            or not order.completed_at or not order.settled_at or order.amount <= 0
            or order.payment_status not in ('PAID', 'PARTIALLY_REFUNDED', 'REFUNDED')
            or order.aftersales.filter(status='OPEN').exists()
            or not OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
            or hasattr(order, 'points_redemption')):
        raise ValidationError('默认本金转单范围或售后状态待核对')
    lock_order_money()
    manifests = list(OrderSegmentSettlementManifest.objects.select_for_update().filter(
        order=order, finalized_at=order.settled_at).order_by('pk')[:2])
    if len(manifests) != 1:
        raise ValidationError('默认本金转单的最终结算清单待核对')
    try:
        verify_finalized(order, manifests[0])
    except SettlementError as exc:
        raise ValidationError('转单逐段结算凭证不完整，请先核对') from exc
    _verify_addons(order, decision, manifests[0])
    snapshot = DefaultPaymentPointsSnapshot.objects.select_for_update().filter(order=order).first()
    if (not snapshot or snapshot.vip_enabled != decision.payment_vip_enabled
            or snapshot.awarded_refunded_principal != decision.refunded_units
            or order.funding_benefits_resolved_at != decision.decided_at):
        raise ValidationError('默认本金转单会员付款快照待核对')
    payments = list(Transaction.objects.select_for_update().filter(order=order, wallet__account_id=decision.account_id,
        tx_type='PAY', status='SUCCESS', amount=-decision.paid_units, reversal_of__isnull=True).order_by('pk')[:2])
    if (len(payments) != 1 or payments[0].wallet.user_id != order.customer_id
            or payments[0].balance_after != payments[0].balance_before + payments[0].amount
            or payments[0].reversals.exists()):
        raise ValidationError('默认本金转单付款流水待核对')
    sources = verified_sources(order, snapshot)
    refunds = list(order.refund_records.select_for_update().order_by('pk'))
    refunded = sum(item.amount for item in refunds)
    original = sum(item.amount for item in refunds if item.created_at <= decision.decided_at)
    if (sum(row.amount for row in sources) != decision.paid_units
            or any(row.kind != 'PREPAID' for row in sources)
            or sum(row.refunded_amount for row in sources) != refunded
            or original != decision.refunded_units or not original <= refunded <= decision.paid_units):
        raise ValidationError('默认本金转单的付款来源或退款快照待核对')
    returned = Transaction.objects.filter(order=order, wallet__account_id=decision.account_id,
        tx_type='REFUND', status='SUCCESS', amount__gt=0).aggregate(total=Sum('amount'))['total'] or 0
    if returned != refunded:
        raise ValidationError('默认本金转单退款钱包流水待核对')
    return refunded
