"""Serialize both sides of a transfer with provider account closure."""
from django.conf import settings

from club_accounts.models import ClubAccount
from club_accounts.provider_lifecycle import (ProviderLifecycleError, ensure_provider_lifecycle,
    lock_provider_accounts_for_read, lock_provider_operations, provider_membership_current)

from .state_machine import IllegalTransitionError


def lock_incoming_cycles(account_ids):
    """Keep the incoming cycle stable while allowing schedule foreign-key reads."""
    return {pk: account.business_cycle for pk, account in lock_provider_accounts_for_read(account_ids).items()}


def capture(commands):
    if not getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
        return None
    old_ids = {old for old, _ in commands}
    new_ids = {new for _, new in commands}
    ids = old_ids | new_ids
    accounts = list(ClubAccount.objects.filter(pk__in=ids, account_type=ClubAccount.AccountType.PROVIDER))
    if {account.pk for account in accounts} != ids:
        raise IllegalTransitionError('转单陪玩账号不存在，请刷新后重试')
    try:
        for account in accounts:
            ensure_provider_lifecycle(account)
    except ProviderLifecycleError as exc:
        raise IllegalTransitionError('陪玩账号状态暂不可核验，请稍后重试') from exc
    return accounts, new_ids


def lock(snapshot):
    if snapshot is None:
        return
    accounts, new_ids = snapshot
    try:
        lock_provider_operations(accounts)
        if any(not ClubAccount.objects.filter(pk=account.pk, account_type=ClubAccount.AccountType.PROVIDER,
                       business_cycle=account.business_cycle).exists() for account in accounts):
            raise IllegalTransitionError('转单陪玩账号周期已变化，请重新核对')
        if any(not provider_membership_current(account) for account in accounts if account.pk in new_ids):
            raise IllegalTransitionError('接替陪玩账号状态已变化，请重新选择')
    except ProviderLifecycleError as exc:
        raise IllegalTransitionError('陪玩账号状态暂不可核验，请稍后重试') from exc
