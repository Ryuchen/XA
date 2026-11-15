"""Protect timely low-rating aftersale creation against provider retirement."""
from django.conf import settings
from django.utils import timezone

from club_accounts.models import ClubAccount
from club_accounts.provider_lifecycle import ProviderLifecycleError, ensure_provider_lifecycle
from engagement.confirmation import aftersale_deadline


def capture(order, target, scores):
    if not getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False) or min(scores) > 2:
        return None
    deadline = aftersale_deadline(order)
    if not deadline or timezone.now() >= deadline:
        return None
    account = ClubAccount.objects.filter(pk=target.get('provider_account_id'),
        account_type=ClubAccount.AccountType.PROVIDER).first()
    if account is None:
        raise ProviderLifecycleError('评价陪玩账号待核对')
    ensure_provider_lifecycle(account)
    return account


def verify(order, account):
    # A request may wait for locks until after the window expires. The normal
    # evaluation path then records the rating without opening an aftersale.
    deadline = aftersale_deadline(order)
    if not deadline or timezone.now() >= deadline:
        return True
    from site_messages.order_cycle import recipient_cycle
    cycle = recipient_cycle(order, account.pk)
    return bool(cycle and cycle == account.business_cycle and ClubAccount.objects.filter(
        pk=account.pk, account_type=ClubAccount.AccountType.PROVIDER,
        license_membership_active=True, business_cycle=cycle).exists())
