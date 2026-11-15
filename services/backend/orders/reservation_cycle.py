"""Classify the viewer using the reservation's recorded membership cycles."""


def viewer_cycle_status(reservation, account):
    if account.account_type == 'STAFF':
        return 'NOT_APPLICABLE'
    cycles = []
    if account.account_type == 'BOSS' and reservation.customer_account_id == account.pk:
        cycles.append(reservation.customer_business_cycle)
    elif account.account_type == 'PROVIDER':
        if reservation.required_provider_count == 1 and reservation.provider_account_id == account.pk:
            cycles.append(reservation.provider_business_cycle)
        if reservation.required_provider_count == 2:
            cycles.extend(seat.provider_business_cycle for seat in reservation.seats.all()
                          if seat.provider_account_id == account.pk)
            cycles.extend(event.provider_business_cycle for event in reservation.seat_events.all()
                          if event.provider_account_id == account.pk and event.action == 'INVITED')
        if reservation.order_id and reservation.status == 'CONVERTED':
            cycles.extend(provider.provider_business_cycle for provider in reservation.order.providers.all()
                          if provider.provider_account_id == account.pk and provider.released_at is None)
    known = [cycle for cycle in cycles if cycle is not None and cycle > 0]
    if (getattr(account, 'license_membership_active', False)
            and account.business_cycle in known):
        return 'CURRENT'
    return 'PREVIOUS' if known else 'UNKNOWN'


def viewer_participation_status(reservation, account):
    if account.account_type != 'PROVIDER':
        return 'NOT_APPLICABLE'
    if (reservation.required_provider_count == 1 and reservation.provider_account_id == account.pk
            and reservation.provider_business_cycle == account.business_cycle):
        return 'CURRENT'
    if any(seat.provider_account_id == account.pk and seat.provider_business_cycle == account.business_cycle
           for seat in reservation.seats.all()):
        return 'CURRENT'
    if reservation.order_id and reservation.status == 'CONVERTED' and any(
            item.provider_account_id == account.pk and item.released_at is None
            and item.provider_business_cycle == account.business_cycle
            for item in reservation.order.providers.all()):
        return 'CURRENT'
    return 'HISTORICAL'
