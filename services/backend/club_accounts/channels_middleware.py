from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from .tokens import account_for_claims


@database_sync_to_async
def get_account_context(raw_token):
    try:
        token = AccessToken(raw_token)
        account = account_for_claims(token)
    except TokenError:
        return None, None, None

    mapping = getattr(account, 'legacy_mapping', None)
    legacy_user = None
    if mapping is not None:
        legacy_user = get_user_model().objects.filter(
            pk=mapping.legacy_user_id,
        ).first()
    return account, legacy_user, {
        key: token[key]
        for key in ('account_id', 'account_type', 'token_kind', 'exp', 'otp_verified', 'otp_version')
        if key in token
    }


class ClubAccountTokenAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        params = parse_qs(scope.get('query_string', b'').decode('utf-8', errors='replace'))
        raw_token = params.get('token', [None])[0]
        account, legacy_user, claims = (
            await get_account_context(raw_token)
            if raw_token
            else (None, None, None)
        )
        scope['user'] = AnonymousUser()
        scope['account'] = account
        scope['legacy_user'] = legacy_user
        # Verified claims only; do not retain the raw bearer string in state.
        scope['account_auth'] = claims
        return await super().__call__(scope, receive, send)
