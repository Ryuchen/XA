"""Account for paid continuation rewards in their actual posting period."""


def continuation_reward_cost(start, end):
    from orders.models import OrderContinuationReward

    rewards = OrderContinuationReward.objects.filter(
        transaction__created_at__gte=start, transaction__created_at__lt=end,
    ).select_related('transaction__wallet', 'continuation')
    total = 0
    invalid = []
    for reward in rewards:
        tx = reward.transaction
        if (tx.status != 'SUCCESS' or tx.tx_type != 'REWARD' or tx.amount != reward.amount
                or reward.amount <= 0 or tx.order_id != reward.continuation.continuation_order_id
                or tx.wallet.account_id != reward.provider_id or tx.reversal_of_id
                or tx.balance_after != tx.balance_before + tx.amount
                or tx.reversals.filter(status='SUCCESS').exists()):
            invalid.append(reward.pk)
        else:
            total += reward.amount
    return total, invalid
