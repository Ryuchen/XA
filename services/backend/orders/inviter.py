"""Resolve a buyer's referral without silently losing commission."""
from django.contrib.auth import get_user_model
from club_accounts.models import LegacyAccountMap


def buyer_inviter(account, legacy_user):
    if account.inviter_id:
        inviter_account = account.inviter
        mapping = LegacyAccountMap.objects.filter(account_id=inviter_account.pk).first()
        inviter = get_user_model().objects.filter(pk=mapping.legacy_user_id).first() if mapping else None
        return inviter, inviter_account, account.inviter_commission_rate
    inviter = getattr(legacy_user, 'inviter', None)
    if not inviter:
        return None, None, 0
    mapping = LegacyAccountMap.objects.select_related('account').filter(legacy_user_id=inviter.pk).first()
    return inviter, mapping.account if mapping else None, legacy_user.inviter_commission_rate
