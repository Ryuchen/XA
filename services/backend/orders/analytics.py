"""Read-only net turnover and actual provider income for partially refunded orders."""
from collections import defaultdict
from .refunds import proportional_targets,with_net_amount


def partial_share_rows(queryset):
    orders = with_net_amount(queryset.filter(payment_status='PARTIALLY_REFUNDED')).select_related(
        'service__service_category').prefetch_related('providers','transactions__wallet')
    result = []
    for order in orders:
        members = [row for row in order.providers.all() if not row.released_at or row.settlement_base > 0 or row.provider_income > 0]
        if members:
            weights = {row.pk:row.settlement_base for row in members if row.settlement_base > 0}
            total_base = sum(weights.values())
            refund_base = (order.amount-order._net_paid_amount)*total_base//order.amount
            refunded = proportional_targets(weights,refund_base)
            entries = [(row.provider_account_id,row.provider_id,row.settlement_base-refunded.get(row.pk,0),
                        row.provider_income,row.created_at) for row in members]
        else:
            entries = [(order.provider_account_id,order.provider_id,order._net_paid_amount,
                        order.provider_income,order.grabbed_at or order.created_at)]
        txs = [tx for tx in order.transactions.all() if tx.status == 'SUCCESS']
        reversals = defaultdict(int)
        for tx in txs:
            if tx.reversal_of_id:
                reversals[tx.reversal_of_id] += tx.amount
        for account_id,user_id,amount,snapshot,accepted_at in entries:
            originals = [tx for tx in txs if not tx.reversal_of_id and tx.amount > 0
                and tx.tx_type in ('INCOME','GIFT')
                and ((account_id and tx.wallet.account_id == account_id) or (user_id and tx.wallet.user_id == user_id))
                and not tx.remark.startswith(('推荐分佣：','礼物推荐分佣：'))]
            # Official settlement labels distinguish provider earnings from an
            # inviter credit even when both belong to the very same wallet.
            # Historical ambiguous credits retain the net snapshot estimate.
            if sum(tx.amount for tx in originals) == snapshot:
                income = sum(tx.amount+reversals[tx.pk] for tx in originals)
            else:
                income = snapshot*order._net_paid_amount//order.amount
            result.append({'provider_account_id':account_id,'provider_id':user_id,'amount':amount,
                'income':income,'count':1,'accepted_at':accepted_at,'is_gift':bool(order.service.service_category and order.service.service_category.is_gift)})
    return result
