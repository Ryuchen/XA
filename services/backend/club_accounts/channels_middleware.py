from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from .models import ClubAccount
from .tokens import ACCOUNT_TOKEN_KIND


@database_sync_to_async
def get_account_context(raw_token):
    try:
        token = AccessToken(raw_token)
    except TokenError:
        return None, None
    if token.get('token_kind') != ACCOUNT_TOKEN_KIND:
        return None, None

    account = ClubAccount.objects.filter(
        pk=token.get('account_id'),
        is_active=True,
        can_login=True,
    ).first()
    if account is None:
        return None, None

    mapping = getattr(account, 'legacy_mapping', None)
    legacy_user = None
    if mapping is not None:
        legacy_user = get_user_model().objects.filter(
            pk=mapping.legacy_user_id,
        ).first()
    return account, legacy_user


class ClubAccountTokenAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        params = parse_qs(scope.get('query_string', b'').decode())
        raw_token = params.get('token', [None])[0]
        account, legacy_user = (
            await get_account_context(raw_token)
            if raw_token
            else (None, None)
        )
        scope['user'] = AnonymousUser()
        scope['account'] = account
        scope['legacy_user'] = legacy_user
        return await super().__call__(scope, receive, send)
