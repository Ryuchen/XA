from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import DatabaseError, InterfaceError
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from .tokens import account_for_claims
from stores.context import resolve_store, store_context
from stores.hosts import host_store_slug, is_admin_host, websocket_store_slug


@database_sync_to_async
def get_account_context(raw_token, expected_slug=None, require_console=False, allow_cached=False):
    try:
        token = AccessToken(raw_token)
        slug = token.get('store_slug')
        if not isinstance(slug, str) or not slug:
            raise TokenError('登录令牌缺少所属门店')
        if require_console and (not token.get('store_slug') or token.get('account_type') != 'STAFF'):
            raise TokenError('请使用已绑定门店的后台账号登录')
        if expected_slug and expected_slug != slug:
            raise TokenError('登录所属门店不一致')
        store = resolve_store(slug, allow_cached=allow_cached)
        with store_context(store):
            account = account_for_claims(token)
            if require_console:
                from console.permissions import is_console_account
                if not is_console_account(account):
                    raise TokenError('后台访问权限已停用')

            mapping = getattr(account, 'legacy_mapping', None)
            legacy_user = None
            if mapping is not None:
                legacy_user = get_user_model().objects.filter(
                    pk=mapping.legacy_user_id,
                ).first()
    except (TokenError, ValueError, TypeError):
        return None, None, None, None

    return account, legacy_user, {
        key: token[key]
        for key in ('account_id', 'account_type', 'business_cycle', 'token_kind', 'exp', 'otp_verified', 'otp_version', 'store_slug',
                    'auth_method', 'sms_phone_proof', 'wechat_openid_hash', 'wechat_identity_id')
        if key in token
    }, store


class ClubAccountTokenAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        params = parse_qs(scope.get('query_string', b'').decode('utf-8', errors='replace'), keep_blank_values=True)
        try:
            expected_slug = await database_sync_to_async(websocket_store_slug)(scope, params)
        except (DatabaseError, InterfaceError):
            await send({'type': 'websocket.close', 'code': 1013})
            return
        except (ValueError, UnicodeDecodeError):
            await send({'type': 'websocket.close', 'code': 4401})
            return
        raw_token = params.get('token', [None])[0]
        host = next((value.decode('ascii') for key, value in scope.get('headers', []) if key.lower() == b'host'), '')
        try:
            account, legacy_user, claims, store = (
                await get_account_context(raw_token, expected_slug, require_console=is_admin_host(host),
                                          allow_cached=bool(expected_slug) or is_admin_host(host))
                if raw_token
                else (None, None, None, None)
            )
        except (DatabaseError, InterfaceError):
            await send({'type': 'websocket.close', 'code': 1013})
            return
        scope['user'] = AnonymousUser()
        scope['account'] = account
        scope['legacy_user'] = legacy_user
        # Verified claims only; do not retain the raw bearer string in state.
        scope['account_auth'] = claims
        scope['store'] = store
        if store is None:
            # Do not run a consumer against the default store after a failed
            # store lookup, even when the supplied token itself was valid.
            await send({'type': 'websocket.close', 'code': 4401})
            return
        with store_context(store):
            return await super().__call__(scope, receive, send)
