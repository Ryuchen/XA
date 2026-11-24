"""Account for paid continuation rewards in their actual posting period."""


def continuation_reward_cost(start, end):
    from orders.models import OrderContinuationReward

    rewards = OrderContinuationReward.objects.filter(
        transaction__created_at__gte=start, transaction__created_at__lt=end,
    ).select_related('transaction__wallet', 'continuation')
    total = 0
    invalid = []
    for reward in rewards:
        if reward.has_verified_payment():
            total += reward.amount
        else:
            invalid.append(reward.pk)
    return total, invalid
