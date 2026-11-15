"""Evidence for a default-principal main order with settled add-ons."""
from django.db.models import Sum
from rest_framework.exceptions import ValidationError
from stores.db import transaction
from wallet.models import Transaction
from .default_funding_refunds import verified_sources
from .models import DefaultPaymentPointsSnapshot


def verify_locked(order, *, after_decision=False, baseline=0, unpaid_addon_only=False):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('主单会员证据核对须在门店事务中')
    from orders.financial_lock import lock_order_money
    from orders.models import OrderOriginalShareTransfer
    from orders.refunds import refunded_amount
    if (order.is_gift_snapshot or order.funding_terms is not None or order.source_order_id
            or not order.settled_at
            or order.status_logs.filter(action='TRANSFER').exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        raise ValidationError('默认本金加购主单结算范围待核对')
    lock_order_money()
    if unpaid_addon_only:
        from orders.addon_payment import verify_order_debit_links
        from orders.models import OrderAddonPayment
        verify_order_debit_links(order, table_exists=True, lock=True)
        if OrderAddonPayment.objects.filter(intent__order=order).exists():
            raise ValidationError('主单实销时未付款的加购出现付款凭证，请先核对')
    snapshot = DefaultPaymentPointsSnapshot.objects.select_for_update().filter(order=order).first()
    if not snapshot or snapshot.awarded_refunded_principal != (baseline if after_decision else None):
        raise ValidationError('主单会员付款快照待核对')
    paid = list(Transaction.objects.select_for_update().filter(order=order, wallet__account_id=order.customer_account_id,
        tx_type='PAY', status='SUCCESS', amount=-order.amount, reversal_of__isnull=True)[:2])
    if (len(paid) != 1 or paid[0].balance_after != paid[0].balance_before + paid[0].amount
            or paid[0].reversals.exists()):
        raise ValidationError('主单本金支付流水待核对')
    sources = verified_sources(order, snapshot)
    refunded = refunded_amount(order)
    if (sum(row.amount for row in sources) != order.amount or any(row.kind != 'PREPAID' for row in sources)
            or sum(row.refunded_amount for row in sources) != refunded or not 0 <= refunded <= order.amount):
        raise ValidationError('主单本金及退款来源待核对')
    returns = Transaction.objects.filter(order=order, wallet__account_id=order.customer_account_id,
        tx_type='REFUND', status='SUCCESS', amount__gt=0).aggregate(total=Sum('amount'))['total'] or 0
    if returns != refunded:
        raise ValidationError('主单退款钱包流水待核对')
    return snapshot, refunded
