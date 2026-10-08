from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from .models import ClubAccount

ACCOUNT_TOKEN_KIND = 'club_account'


def account_for_claims(claims):
    """Resolve current identity after the caller has verified the JWT signature."""
    account_id = claims.get('account_id')
    if (claims.get('token_kind') != ACCOUNT_TOKEN_KIND
            or type(account_id) is not int or not 0 < account_id < 2 ** 63
            or claims.get('account_type') not in ClubAccount.AccountType.values):
        raise TokenError('无效的业务账户令牌')
    account = ClubAccount.objects.filter(pk=account_id, is_active=True, can_login=True).first()
    if account is None:
        raise TokenError('业务账户不可登录')
    if claims['account_type'] != account.account_type:
        raise TokenError('业务账户身份已变更，请重新登录')
    from .otp import otp_required
    if otp_required(account):
        credential=getattr(account,'otp_credential',None)
        if not credential or claims.get('otp_verified') is not True or claims.get('otp_version') != str(credential.version):
            raise TokenError('请完成验证器动态验证码登录')
    return account


def issue_account_tokens(account, *, otp_verified=False):
    refresh = RefreshToken()
    refresh['token_kind'] = ACCOUNT_TOKEN_KIND
    refresh['account_id'] = account.pk
    refresh['account_type'] = account.account_type
    from .otp import otp_required
    if otp_required(account):
        credential=getattr(account,'otp_credential',None)
        if not otp_verified or not credential:raise TokenError('请完成动态验证码登录')
        refresh['otp_verified']=True
        refresh['otp_version']=str(credential.version)
    return str(refresh.access_token), str(refresh)


def refresh_account_access_token(raw_refresh_token, *, require_console=False):
    try:
        refresh = RefreshToken(raw_refresh_token)
    except TokenError as exc:
        raise TokenError('业务账户登录已过期') from exc

    account = account_for_claims(refresh)
    if require_console:
        from console.permissions import is_console_account
        if not is_console_account(account):
            raise TokenError('后台访问权限已停用')
    return str(refresh.access_token)
