"""Resolve a business-account chat without losing pre-migration history."""
from django.db import transaction

from club_accounts.models import ClubAccount
from .models import ChatSession


@transaction.atomic
def account_chat_session(account, legacy_user):
    # The account row serializes first access to an account with no session.
    ClubAccount.objects.select_for_update().get(pk=account.pk)
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
