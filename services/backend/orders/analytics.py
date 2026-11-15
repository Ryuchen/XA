"""Read-only net turnover and actual provider income for partially refunded orders."""
from collections import defaultdict
from .refunds import proportional_targets,with_net_amount


def partial_share_rows(queryset, *, include_refunded=False):
    states = ('PARTIALLY_REFUNDED', 'REFUNDED') if include_refunded else ('PARTIALLY_REFUNDED',)
    orders = with_net_amount(queryset.filter(payment_status__in=states, amount__gt=0)).prefetch_related(
        'providers','transactions__wallet', 'refund_records')
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
        refunds = list(order.refund_records.all())
        allocations = refunds[0].negotiated_allocation if len(refunds) == 1 else []
        negotiated = {row['share_id']: row for row in allocations
                      if row.get('role') == 'PROVIDER' and row.get('share_id')}
        # A negotiated termination credits an unsettled share only after its
        # agreed burden is deducted. Validate the retained totals against the
        # actual wallet entries, including reversals of earlier settlements.
        negotiated_income = {}
        if negotiated and members and all(row.pk in negotiated for row in members):
            groups = defaultdict(list)
            for row in members:
                groups[(row.provider_account_id, row.provider_id)].append(row)
            for (account_id, user_id), shares in groups.items():
                original = [tx for tx in txs if not tx.reversal_of_id and tx.amount > 0
                    and tx.tx_type in ('INCOME', 'GIFT')
                    and ((account_id and tx.wallet.account_id == account_id)
                         or (user_id and tx.wallet.user_id == user_id))
                    and not tx.remark.startswith(('推荐分佣：', '礼物推荐分佣：'))]
                allocated = [negotiated[row.pk] for row in shares]
                expected_original = sum(item['entitlement'] if item['settled'] else item['retained'] for item in allocated)
                actual_net = sum(tx.amount + reversals[tx.pk] for tx in original)
                if (all(item['entitlement'] == row.provider_income for item, row in zip(allocated, shares))
                        and sum(tx.amount for tx in original) == expected_original
                        and actual_net == sum(item['retained'] for item in allocated)):
                    negotiated_income.update({row.pk: item['retained'] for item, row in zip(allocated, shares)})
        for index, (account_id,user_id,amount,snapshot,accepted_at) in enumerate(entries):
            originals = [tx for tx in txs if not tx.reversal_of_id and tx.amount > 0
                and tx.tx_type in ('INCOME','GIFT')
                and ((account_id and tx.wallet.account_id == account_id) or (user_id and tx.wallet.user_id == user_id))
                and not tx.remark.startswith(('推荐分佣：','礼物推荐分佣：'))]
            # Official settlement labels distinguish provider earnings from an
            # inviter credit even when both belong to the very same wallet.
            # When the original entries cannot prove the split, never replace
            # the missing evidence with a proportional estimate.
            if negotiated:
                income = negotiated_income.get(members[index].pk) if members else None
            elif sum(tx.amount for tx in originals) == snapshot:
                income = sum(tx.amount+reversals[tx.pk] for tx in originals)
            else:
                income = None
            result.append({'provider_account_id':account_id,'provider_id':user_id,'amount':amount,
                'income':income,'income_review_order_id':order.pk if income is None else None,
                'count':1,'accepted_at':accepted_at,'is_gift':order.is_gift_snapshot})
    return result
