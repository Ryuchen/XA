"""Provider referral identity captured with an ordinary order payment."""
from club_accounts.models import ClubAccount
from club_accounts.provider_lifecycle import ensure_provider_lifecycle, lock_provider_operations
from .inviter import buyer_inviter


def capture(buyer, legacy_user, raw_ids):
    if not isinstance(raw_ids, list):
        raise ValueError('请选择有效陪玩')
    try:
        ids = {int(value) for value in raw_ids}
    except (TypeError, ValueError):
        raise ValueError('请选择有效陪玩')
    if any(value <= 0 for value in ids):
        raise ValueError('请选择有效陪玩')
    inviter, account, _ = buyer_inviter(buyer, legacy_user)
    if inviter and not account:
        raise ValueError('推荐人账号归属待核对')
    inviter_id = account.pk if account else None
    provider_inviter_id = (account.pk if account and account.account_type == ClubAccount.AccountType.PROVIDER else None)
    if provider_inviter_id:
        ids.add(provider_inviter_id)
    accounts = list(ClubAccount.objects.filter(pk__in=ids, account_type=ClubAccount.AccountType.PROVIDER))
    if {item.pk for item in accounts} != ids:
        raise ValueError('陪玩账号不存在或不属于本店')
    for item in accounts:
        ensure_provider_lifecycle(item)
    return inviter_id, provider_inviter_id, accounts


def lock(snapshot):
    lock_provider_operations(snapshot[2])


def validate(snapshot, buyer, legacy_user, providers):
    inviter_id, provider_inviter_id, accounts = snapshot
    inviter, account, _ = buyer_inviter(buyer, legacy_user)
    if (account.pk if account else None) != inviter_id or (inviter and not account):
        raise ValueError('推荐人已变化，请重新下单')
    expected = {item.pk for item in providers}
    if provider_inviter_id:
        expected.add(provider_inviter_id)
    if {item.pk for item in accounts} != expected:
        raise ValueError('陪玩已变化，请重新下单')
    for item in accounts:
        if not ClubAccount.objects.filter(pk=item.pk, account_type=ClubAccount.AccountType.PROVIDER,
                                          business_cycle=item.business_cycle).exists():
            raise ValueError('陪玩账号周期已变化，请重新下单')
    if provider_inviter_id:
        inviter = next(item for item in accounts if item.pk == provider_inviter_id)
        if not ClubAccount.objects.filter(pk=inviter.pk, account_type=ClubAccount.AccountType.PROVIDER,
                license_membership_active=True, business_cycle=inviter.business_cycle).exists():
            raise ValueError('陪玩推荐人账号状态待核对，请联系客服')


def record(order, snapshot):
    inviter_id, provider_inviter_id, accounts = snapshot
    if not provider_inviter_id:
        return
    if order.inviter_account_id != inviter_id:
        raise ValueError('订单推荐人已变化')
    from .referral_cycle_models import OrderReferralCycleSnapshot
    inviter = next(item for item in accounts if item.pk == provider_inviter_id)
    OrderReferralCycleSnapshot.objects.create(order=order, inviter_id=inviter.pk, business_cycle=inviter.business_cycle)


def verify(order):
    snapshot = getattr(order, 'referral_cycle_snapshot', None)
    if snapshot is None:
        if order.inviter_account_id and ClubAccount.objects.filter(pk=order.inviter_account_id,
                account_type=ClubAccount.AccountType.PROVIDER).exists():
            raise ValueError('陪玩推荐佣金来源周期缺失，请核对')
        return
    if (snapshot.inviter_id != order.inviter_account_id or not ClubAccount.objects.filter(
            pk=snapshot.inviter_id, account_type=ClubAccount.AccountType.PROVIDER,
            license_membership_active=True, business_cycle=snapshot.business_cycle).exists()):
        raise ValueError('推荐佣金账号周期已变化，请核对')
