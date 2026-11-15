"""Validate a store-local card without assigning chat or payroll authority."""
from rest_framework.exceptions import ValidationError

from club_accounts.models import ClubAccount
from console.models import AdminMembership
from .models import SupportContactCard


def active_staff(account_id, *, lock=False):
    accounts = ClubAccount.objects
    memberships = AdminMembership.objects
    if lock:
        accounts, memberships = accounts.select_for_update(), memberships.select_for_update()
    return (accounts.filter(pk=account_id, account_type='STAFF', is_active=True, can_login=True).exists()
            and memberships.filter(account_id=account_id, is_active=True).exists())


def selected_card(card_id, *, lock=False, expected_staff_id=None):
    if card_id is None:
        return None
    if isinstance(card_id, bool):
        raise ValidationError('客服名片标识无效')
    try:
        card_id = int(card_id)
    except (ValueError, TypeError):
        raise ValidationError('客服名片标识无效') from None
    cards = SupportContactCard.objects
    if lock:
        cards = cards.select_for_update()
    card = cards.filter(pk=card_id, is_active=True).first()
    if not card or not card.staff_account_id or not active_staff(card.staff_account_id, lock=lock):
        raise ValidationError('所选客服名片不可用，请重新选择')
    if expected_staff_id is not None and card.staff_account_id != expected_staff_id:
        raise ValidationError('所选客服已变化，请重新选择')
    return card
