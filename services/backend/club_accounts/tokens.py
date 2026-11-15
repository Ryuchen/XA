import hashlib
import hmac

from django.utils.crypto import salted_hmac
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from .models import ClubAccount

ACCOUNT_TOKEN_KIND = 'club_account'


def _sms_phone_proof(phone):
    return salted_hmac('xa.account.sms-login.phone.v1', phone, algorithm='sha256').hexdigest()


def account_for_claims(claims):
    """Resolve current identity after the caller has verified the JWT signature."""
    from stores.context import current_store
    from django.conf import settings
    store = current_store()
    if store is None:
        raise TokenError('请从所属门店入口登录')
    slug = store.slug
    # A valid signature is not enough: IDs can be identical in separate stores.
    # Every first-release business token must explicitly identify its store.
    claimed_slug = claims.get('store_slug')
    if claimed_slug != slug:
        raise TokenError('登录所属门店不一致，请从当前门店重新登录')
    account_id = claims.get('account_id')
    if (claims.get('token_kind') != ACCOUNT_TOKEN_KIND
            or type(account_id) is not int or not 0 < account_id < 2 ** 63
            or claims.get('account_type') not in ClubAccount.AccountType.values):
        raise TokenError('无效的业务账户令牌')
    method = claims.get('auth_method', 'legacy')
    if method == 'platform':
        raise TokenError('请使用独立平台账号登录')
    account = ClubAccount.objects.filter(pk=account_id, is_active=True, can_login=True).first()
    if account is None:
        raise TokenError('业务账户不可登录')
    if claims['account_type'] != account.account_type:
        raise TokenError('业务账户身份已变更，请重新登录')
    if account.account_type in ('BOSS', 'PROVIDER') and (
            not account.license_membership_active or claims.get('business_cycle') != account.business_cycle):
        raise TokenError('账号已退出门店，请重新登录并办理入店')
    if method == 'password_sms':
        proof = claims.get('sms_phone_proof')
        if (not account.is_phone_verified or not account.phone or not isinstance(proof, str)
                or not hmac.compare_digest(proof, _sms_phone_proof(account.phone))):
            raise TokenError('请使用账号密码重新完成短信验证')
    elif method == 'password_otp':
        credential = getattr(account, 'otp_credential', None)
        if not credential or claims.get('otp_verified') is not True or claims.get('otp_version') != str(credential.version):
            raise TokenError('验证器绑定已变化，请重新登录')
    elif method == 'wechat':
        expected = claims.get('wechat_openid_hash', '')
        identity_id = claims.get('wechat_identity_id')
        if type(identity_id) is int:
            from .models import WechatIdentity
            identity = WechatIdentity.objects.filter(pk=identity_id, account=account).first()
            if (not account.is_openid_bound or identity is None
                    or expected != hashlib.sha256(identity.openid.encode()).hexdigest()):
                raise TokenError('微信绑定已变化，请重新使用微信登录')
        else:
            actual = hashlib.sha256((account.openid or '').encode()).hexdigest()
            if not account.openid or not account.is_openid_bound or expected != actual:
                raise TokenError('微信绑定已变化，请重新使用微信登录')
    elif method != 'legacy':
        raise TokenError('未知的登录方式，请重新登录')
    else:
        from .otp import otp_required
        if otp_required(account):
            credential = getattr(account, 'otp_credential', None)
            if not credential or claims.get('otp_verified') is not True or claims.get('otp_version') != str(credential.version):
                raise TokenError('请完成验证器动态验证码登录')
    return account


def issue_account_tokens(account, *, otp_verified=False, auth_method='legacy', wechat_identity=None):
    from stores.context import current_database, current_store
    from django.conf import settings
    store = current_store()
    if store is None:
        raise TokenError('请从所属门店入口登录')
    if auth_method == 'platform':
        raise TokenError('请使用独立平台账号登录')
    if account._state.db and account._state.db != current_database():
        raise TokenError('账号所属门店不一致')
    if auth_method not in ('legacy', 'password_sms', 'password_otp', 'wechat', 'platform'):
        raise TokenError('未知的登录方式')
    if auth_method == 'password_sms':
        if not account.is_phone_verified or not account.phone:
            raise TokenError('请先完成短信验证')
    if auth_method == 'password_otp':
        credential = getattr(account, 'otp_credential', None)
        if not otp_verified or not credential:
            raise TokenError('请完成验证器动态验证码登录')
    if auth_method == 'wechat':
        if not account.is_openid_bound or (wechat_identity is None and not account.openid):
            raise TokenError('当前账号尚未绑定微信')
        if wechat_identity is not None and wechat_identity.account_id != account.pk:
            raise TokenError('微信身份与账号不一致')
    if auth_method == 'legacy':
        from .otp import otp_required
        if otp_required(account):
            credential = getattr(account, 'otp_credential', None)
            if not otp_verified or not credential:
                raise TokenError('请完成动态验证码登录')
    from .license_quota import rejoin_if_retired
    rejoin_if_retired(account)
    refresh = RefreshToken()
    refresh['token_kind'] = ACCOUNT_TOKEN_KIND
    refresh['account_id'] = account.pk
    refresh['account_type'] = account.account_type
    refresh['business_cycle'] = account.business_cycle
    refresh['store_slug'] = store.slug
    refresh['auth_method'] = auth_method
    if auth_method == 'password_sms':
        refresh['sms_phone_proof'] = _sms_phone_proof(account.phone)
    if auth_method == 'password_otp':
        refresh['otp_verified'] = True
        refresh['otp_version'] = str(credential.version)
    if auth_method == 'wechat':
        identity_openid = wechat_identity.openid if wechat_identity is not None else account.openid
        refresh['wechat_openid_hash'] = hashlib.sha256(identity_openid.encode()).hexdigest()
        if wechat_identity is not None:
            refresh['wechat_identity_id'] = wechat_identity.pk
    if auth_method == 'legacy':
        if otp_required(account):
            refresh['otp_verified'] = True
            refresh['otp_version'] = str(credential.version)
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
