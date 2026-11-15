"""Read recipient cycle only from the order's persisted business facts."""


def recipient_cycle(order, account_id):
    if not account_id:
        return None
    if account_id == order.customer_account_id:
        return order.business_cycle or None
    from orders.models import OrderParticipationSegment, OrderProvider
    cycles = list(OrderProvider.objects.filter(order=order, provider_account_id=account_id)
                  .values_list('provider_business_cycle', flat=True))
    cycles += list(OrderParticipationSegment.objects.filter(attribution__order=order,
        provider_account_id=account_id).values_list('provider_business_cycle', flat=True))
    if order.is_gift_snapshot and account_id == order.provider_account_id:
        cycles.append(order.gift_provider_business_cycle)
    if account_id == order.inviter_account_id:
        snapshot = getattr(order, 'referral_cycle_snapshot', None)
        cycles.append(snapshot.business_cycle if snapshot and snapshot.inviter_id == account_id else None)
    if not cycles or any(not cycle for cycle in cycles) or len(set(cycles)) != 1:
        return None
    return cycles[0]
