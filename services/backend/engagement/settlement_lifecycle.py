"""Capture provider identities before automatic order settlement takes locks."""
from club_accounts.models import ClubAccount
from club_accounts.provider_lifecycle import ensure_provider_lifecycle, lock_provider_operations
from orders.models import Order, OrderProvider, OrderParticipationSegment, OrderAddonIntent


def provider_ids(order_id, *, lock=False):
    orders = Order.objects.filter(pk=order_id)
    if lock:
        orders = orders.select_for_update()
    order = orders.values('provider_account_id', 'provider_id',
        'inviter_account_id', 'inviter_id').first()
    if not order:
        raise ValueError('结算订单不存在')
    if ((order['provider_id'] and not order['provider_account_id'])
            or (order['inviter_id'] and not order['inviter_account_id'])):
        raise ValueError('结算参与人账号归属待核对')
    assignments = OrderProvider.objects.filter(order_id=order_id)
    segments = OrderParticipationSegment.objects.filter(attribution__order_id=order_id)
    addons = OrderAddonIntent.objects.filter(order_id=order_id)
    if lock:
        assignments = assignments.select_for_update()
        segments = segments.select_for_update()
        addons = addons.select_for_update()
    assignment_ids = list(assignments.values_list('provider_account_id', flat=True))
    if None in assignment_ids:
        raise ValueError('结算陪玩账号归属待核对')
    ids = set(assignment_ids)
    ids.update(segments.values_list('provider_account_id', flat=True))
    ids.update(addons.values_list('provider_id', flat=True))
    if order['provider_account_id']:
        ids.add(order['provider_account_id'])
    provider_accounts = set(ClubAccount.objects.filter(pk__in=ids,
        account_type=ClubAccount.AccountType.PROVIDER).values_list('pk', flat=True))
    if provider_accounts != ids:
        raise ValueError('结算陪玩账号归属待核对')
    if order['inviter_account_id']:
        provider_accounts.update(ClubAccount.objects.filter(pk=order['inviter_account_id'],
            account_type=ClubAccount.AccountType.PROVIDER).values_list('pk', flat=True))
    return provider_accounts


def capture(order_id):
    ids = provider_ids(order_id)
    accounts = list(ClubAccount.objects.filter(pk__in=ids, account_type=ClubAccount.AccountType.PROVIDER))
    if {account.pk for account in accounts} != ids:
        raise ValueError('结算陪玩账号已变化')
    for account in accounts:
        ensure_provider_lifecycle(account)
    return accounts


def lock(accounts):
    lock_provider_operations(accounts)


def verify(order_id, accounts):
    if provider_ids(order_id, lock=True) != {account.pk for account in accounts}:
        raise ValueError('结算参与人已变化，请重试')
    for account in accounts:
        if not ClubAccount.objects.filter(pk=account.pk, account_type=ClubAccount.AccountType.PROVIDER,
                license_membership_active=True, business_cycle=account.business_cycle).exists():
            raise ValueError('结算陪玩账号周期已变化，请核对')
    cycles = {account.pk: account.business_cycle for account in accounts}
    seats = list(OrderProvider.objects.select_for_update().filter(order_id=order_id).values_list(
        'provider_account_id', 'provider_business_cycle'))
    segments = list(OrderParticipationSegment.objects.select_for_update().filter(
        attribution__order_id=order_id).values_list('provider_account_id', 'provider_business_cycle'))
    if any(not account_id or not cycle or cycles.get(account_id) != cycle for account_id, cycle in seats + segments):
        raise ValueError('结算陪玩席位或履约段来源周期待核对')
    from orders.referral_cycle import verify as verify_referral_cycle
    verify_referral_cycle(Order.objects.get(pk=order_id))
