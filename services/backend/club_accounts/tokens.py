from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken


ACCOUNT_TOKEN_KIND = 'club_account'


def issue_account_tokens(account):
    refresh = RefreshToken()
    refresh['token_kind'] = ACCOUNT_TOKEN_KIND
    refresh['account_id'] = account.pk
    refresh['account_type'] = account.account_type
    return str(refresh.access_token), str(refresh)


def refresh_account_access_token(raw_refresh_token):
    try:
        refresh = RefreshToken(raw_refresh_token)
    except TokenError as exc:
        raise TokenError('业务账户登录已过期') from exc

    if refresh.get('token_kind') != ACCOUNT_TOKEN_KIND or not refresh.get('account_id'):
        raise TokenError('无效的业务账户令牌')
    return str(refresh.access_token)
