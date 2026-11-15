"""Reception access is checked again at every message and media boundary."""
from club_accounts.models import ClubAccount
from console.permissions import get_account_permissions, is_console_account


def can_read_session(account, session):
    if not account or not account.is_active or not account.can_login:
        return False
    if account.account_type != ClubAccount.AccountType.STAFF:
        return session.account_id == account.pk
    if not is_console_account(account):
        return False
    permissions = get_account_permissions(account)
    return 'chat:view' in permissions and (
        'chat:supervise' in permissions or session.claimed_by_id == account.pk
    )
