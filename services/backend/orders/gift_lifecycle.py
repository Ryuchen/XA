"""Resolve gift lifecycle participants before any order or wallet row lock."""
from rest_framework.exceptions import ValidationError

from club_accounts.models import ClubAccount
from club_accounts.provider_lifecycle import lock_provider_operations
from .gifts import gift_context


def participants(request, order_id):
    _, profiles = gift_context(request, order_id=order_id, provider_id=request.data.get('provider_id'))
    if request.data.get('provider_id') is None and len(profiles) > 1:
        raise ValidationError('请选择本次服务的收礼陪玩')
    selected_id = request.data.get('provider_id')
    recipient = next((row for row in profiles if row.account_id == int(selected_id)), profiles[0]) if selected_id is not None else profiles[0]
    # Gift orders do not generate referral commission.  Only the recipient's
    # business cycle participates in this purchase.
    return recipient.account, None


def lock_and_check(recipient, inviter):
    lock_provider_operations([recipient] + ([inviter] if inviter else []))
    return ClubAccount.objects.filter(pk=recipient.pk, account_type=ClubAccount.AccountType.PROVIDER,
        is_active=True, can_login=True, can_view=True, license_membership_active=True,
        business_cycle=recipient.business_cycle).exists()


