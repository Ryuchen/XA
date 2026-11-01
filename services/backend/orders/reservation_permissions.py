"""Reuse order-management permissions for staff acting on reservations."""
from club_accounts.models import ClubAccount
from console.permissions import get_account_permissions, is_console_account


def staff_permissions(account):
    if not account or account.account_type != ClubAccount.AccountType.STAFF or not is_console_account(account):
        return set()
    return get_account_permissions(account)


def staff_can(account, permission):
    return permission in staff_permissions(account)
