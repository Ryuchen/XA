"""Post and verify each confirmed points-refund rounding obligation."""
from .settlement import SettlementError


def _postings(order):
    from .models import PointsNegotiationPosting
    return list(PointsNegotiationPosting.objects.select_for_update().filter(
        points_refund__redemption__order=order).select_related('rounding_transaction__wallet')
        .order_by('created_at', 'pk'))


def post(order, store_units, trace_id):
    from wallet.models import Transaction
    from .services import _credit_wallet
    postings = _postings(order)
    total = sum(row.rounding_to_store_units for row in postings)
    if total > store_units or any(row.rounding_transaction_id for row in postings):
        raise SettlementError('积分退款取整预留或流水异常')
    for row in postings:
        if row.rounding_to_store_units:
            row.rounding_transaction = _credit_wallet(order=order, amount=row.rounding_to_store_units,
                tx_type=Transaction.TxType.SHOP_INCOME, remark=f'积分退款取整差额：{order.order_no}',
                event='order.points_refund_settle.rounding', trace_id=trace_id, platform=True,
                log_after_commit=True)
            row.save(update_fields=['rounding_transaction'])
    return total


def verify(order, store_units):
    from wallet.models import Transaction, get_platform_wallet
    postings = _postings(order)
    total = sum(row.rounding_to_store_units for row in postings)
    if total > store_units:
        raise SettlementError('积分退款取整预留不足')
    ids = []
    for row in postings:
        tx = row.rounding_transaction
        if not row.rounding_to_store_units:
            if tx:
                raise SettlementError('零积分退款取整存在流水')
            continue
        if (not tx or tx.order_id != order.pk or tx.wallet_id != get_platform_wallet(for_update=True).pk
                or tx.tx_type != Transaction.TxType.SHOP_INCOME or tx.status != 'SUCCESS'
                or tx.amount != row.rounding_to_store_units or tx.reversal_of_id or tx.reversals.exists()
                or tx.balance_after != tx.balance_before + tx.amount):
            raise SettlementError('积分退款取整流水异常')
        ids.append(tx.pk)
    return total, ids
