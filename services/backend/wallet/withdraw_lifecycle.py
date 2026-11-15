"""Identity check for a withdrawal's original provider membership."""
from django.conf import settings
from club_accounts.models import ClubAccount


def matches_cycle(withdrawal):
    if not withdrawal.account_id or not withdrawal.account_business_cycle:
        return False
    accounts = ClubAccount.objects.filter(pk=withdrawal.account_id,
        account_type=ClubAccount.AccountType.PROVIDER, business_cycle=withdrawal.account_business_cycle)
    if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
        accounts = accounts.filter(license_membership_active=True)
    return accounts.exists()
