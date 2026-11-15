"""Resolve a business-account chat without losing pre-migration history."""
from stores.db import transaction
from rest_framework.exceptions import APIException

from club_accounts.models import ClubAccount
from .models import ChatSession


class ChatMembershipChanged(APIException):
    status_code = 409
    default_detail = '账号状态已变化，请重新登录后再操作'
    default_code = 'chat_membership_changed'


def lock_chat_account(account):
    """Lock the request's account until the caller's transaction completes."""
    locked = ClubAccount.objects.select_for_update().get(pk=account.pk)
    if (locked.account_type != account.account_type or locked.business_cycle != account.business_cycle
            or not locked.license_membership_active or not locked.is_active or not locked.can_login):
        raise ChatMembershipChanged()
    return locked


def lock_owner_snapshot(session_id):
    """Call inside a transaction before taking the staff-side session lock."""
    account_id = ChatSession.objects.filter(pk=session_id).values_list('account_id', flat=True).first()
    account = ClubAccount.objects.select_for_update().filter(pk=account_id).first() if account_id else None
    return account_id, account.business_cycle if account else None


@transaction.atomic
def account_chat_session(account, legacy_user):
    # The account row serializes first access to an account with no session.
    lock_chat_account(account)
    session = ChatSession.objects.select_for_update().filter(account_id=account.pk).first()
    if session is not None:
        return session

    if legacy_user is not None:
        session = ChatSession.objects.select_for_update().filter(user_id=legacy_user.pk).first()
        if session is not None:
            if session.account_id is None:
                session.account_id = account.pk
                session.save(update_fields=['account'])
                return session
            # An explicitly bound conversation belongs to its account. Never
            # reassign or expose it through an old identity mapping.
            legacy_user = None

    return ChatSession.objects.create(account=account, user=legacy_user)
