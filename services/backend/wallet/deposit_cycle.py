"""Bind the store's outstanding deposit to a provider membership cycle."""
from club_accounts.models import ClubAccount


def deposit_cycle(profile, *, increase=False):
    account = ClubAccount.objects.filter(pk=profile.account_id,
        account_type=ClubAccount.AccountType.PROVIDER).only('business_cycle').first()
    if account is None:
        raise ValueError('陪玩押金账号归属待核对')
    if profile.deposit_paid:
        if profile.deposit_business_cycle != account.business_cycle:
            raise ValueError('陪玩押金账号周期待核对')
    elif increase:
        profile.deposit_business_cycle = account.business_cycle
    return account.business_cycle
