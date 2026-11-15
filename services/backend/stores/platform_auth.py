"""Platform-only authentication; no token routes to a store business identity."""
import hmac
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import APIException, AuthenticationFailed
from rest_framework.permissions import BasePermission
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from club_accounts.otp import decrypt_secret, matching_counter, recovery_hash
from users.sms_transport import send_verification_code
from .models import (PlatformAccount, PlatformAuditEvent, PlatformLoginChallenge, PlatformOtpCredential,
                     PlatformRoleGrant, PlatformWechatIdentity)

TOKEN_KIND = 'platform_account'


class PlatformAuthError(ValueError):
    def __init__(self, message, code='PLATFORM_LOGIN_INVALID', status=400):
        super().__init__(message)
        self.code, self.status = code, status


class PlatformAuthenticationUnavailable(APIException):
    status_code = 503
    default_detail = '平台身份服务暂不可用，请稍后重试'
    default_code = 'platform_auth_unavailable'


def _active_grant(account):
    return PlatformRoleGrant.objects.filter(account=account, is_active=True).exists()


def authenticate_platform(username, password):
    if not isinstance(username, str) or not isinstance(password, str) or len(username) > 150 or len(password) > 4096:
        raise PlatformAuthError('账号或密码格式不正确')
    username = username.strip()
    account = PlatformAccount.objects.filter(username=username, is_active=True).first()
    if not account or not account.check_password(password) or not _active_grant(account):
        raise PlatformAuthError('账号或密码错误，或平台权限不可用')
    return account


def _sms_hash(challenge, code):
    return salted_hmac('xa:platform:sms:v1', f'{challenge.pk}:{challenge.account_id}:{challenge.phone}:{code}', algorithm='sha256').hexdigest()


def _phone_hash(phone):
    return salted_hmac('xa:platform:phone:v1', phone, algorithm='sha256').hexdigest()


def start_challenge(account, *, use_sms=False):
    authenticated_version = account.security_version
    with transaction.atomic(using='default'):
        account = PlatformAccount.objects.select_for_update().filter(pk=account.pk, is_active=True).first()
        if not account or account.security_version != authenticated_version or not _active_grant(account):
            raise PlatformAuthError('平台权限不可用')
        now = timezone.now()
        attempts = PlatformLoginChallenge.objects.filter(account=account, created_at__gte=now - timedelta(minutes=5)).aggregate(total=Sum('attempts'))['total'] or 0
        if attempts >= 20:
            raise PlatformAuthError('验证尝试过多，请稍后重试', 'PLATFORM_RATE_LIMITED', 429)
        credential = PlatformOtpCredential.objects.select_for_update().filter(account=account).first()
        method = 'OTP' if credential and not use_sms else 'SMS'
        if method == 'SMS':
            phone = account.phone or ''
            if not phone.isascii() or not phone.isdigit() or not 5 <= len(phone) <= 20:
                raise PlatformAuthError('平台账号未设置可用手机号', 'PHONE_REQUIRED')
            if PlatformLoginChallenge.objects.filter(account=account, method='SMS', created_at__gt=now-timedelta(seconds=60)).exists():
                raise PlatformAuthError('验证码发送过于频繁，请稍后重试', 'SMS_RATE_LIMITED', 429)
        else:
            phone = ''
        # Switching methods or requesting a new code invalidates earlier
        # challenges, so a stale SMS cannot later authenticate the same account.
        PlatformLoginChallenge.objects.filter(account=account, consumed=False).update(consumed=True)
        challenge = PlatformLoginChallenge.objects.create(account=account, method=method,
            security_version=account.security_version, otp_version=credential.version if method == 'OTP' else None,
            phone=phone, expires_at=now+timedelta(minutes=5))
        if method == 'SMS':
            code = f'{secrets.randbelow(1000000):06d}'
            challenge.code_hash = _sms_hash(challenge, code)
            challenge.save(update_fields=['code_hash'])
    if method == 'SMS':
        # Commit the challenge and rate limit before contacting the provider.
        # An uncertain transport response must not erase the cooldown.
        send_verification_code(phone, code)
        return {'requiresSms': True, 'smsChallenge': str(challenge.pk),
                'maskedPhone': phone[:3]+'****'+phone[-4:], 'expiresIn': 300}
    return {'requiresOTP': True, 'otpChallenge': str(challenge.pk), 'expiresIn': 300}


def verify_challenge(account, raw, code, expected_method):
    error = None
    verified = None
    authenticated_version = account.security_version
    with transaction.atomic(using='default'):
        try:
            account = PlatformAccount.objects.select_for_update().get(pk=account.pk, is_active=True)
            challenge = PlatformLoginChallenge.objects.select_for_update().get(pk=raw, account=account, method=expected_method)
        except (PlatformAccount.DoesNotExist, PlatformLoginChallenge.DoesNotExist, ValueError, TypeError, ValidationError):
            raise PlatformAuthError('验证请求无效，请重新登录') from None
        if (account.security_version != authenticated_version or not _active_grant(account) or challenge.consumed or challenge.expires_at <= timezone.now()
                or challenge.security_version != account.security_version or challenge.attempts >= 5):
            raise PlatformAuthError('验证请求已失效，请重新登录')
        challenge.attempts += 1
        challenge.save(update_fields=['attempts'])
        if not isinstance(code, str) or len(code) > 32:
            error = PlatformAuthError('验证码无效')
        elif expected_method == 'SMS':
            if account.phone != challenge.phone or not hmac.compare_digest(challenge.code_hash, _sms_hash(challenge, code)):
                error = PlatformAuthError('验证码错误或手机号已变更')
            else:
                account.is_phone_verified = True
                account.save(update_fields=['is_phone_verified', 'updated_at'])
                verified = account
        else:
            credential = PlatformOtpCredential.objects.select_for_update().filter(account=account).first()
            if not credential or credential.version != challenge.otp_version:
                error = PlatformAuthError('验证器绑定已变化，请重新登录')
            else:
                try:
                    counter = matching_counter(decrypt_secret(credential.secret_ciphertext), code, credential.last_counter)
                except ValueError:
                    counter = None
                saved = None
                if counter is None:
                    hashes = [recovery_hash(code, key) for key in [settings.SECRET_KEY, *settings.SECRET_KEY_FALLBACKS]]
                    saved = next((item for item in credential.recovery_hashes if any(hmac.compare_digest(item, value) for value in hashes)), None)
                if counter is None and saved is None:
                    error = PlatformAuthError('动态验证码错误、已使用或已过期')
                else:
                    if saved is not None:
                        credential.recovery_hashes.remove(saved)
                    if counter is not None:
                        credential.last_counter = counter
                    credential.save(update_fields=['last_counter', 'recovery_hashes'])
                    verified = account
        if verified:
            challenge.consumed = True
            challenge.save(update_fields=['consumed'])
            PlatformAuditEvent.objects.create(account=account, action='auth.second_factor_verified', detail={'method': expected_method})
    if error:
        raise error
    return verified, (challenge.phone if expected_method == 'SMS' else challenge.otp_version)


def issue_tokens(account, method, proof_version):
    current = PlatformAccount.objects.filter(pk=account.pk, is_active=True).first()
    if not current or current.security_version != account.security_version or not _active_grant(current):
        raise PlatformAuthError('平台账号或授权已变化，请重新登录')
    credential = None
    if method == 'SMS':
        if not current.is_phone_verified or current.phone != proof_version:
            raise PlatformAuthError('平台手机号已变化，请重新登录')
    elif method == 'OTP':
        credential = PlatformOtpCredential.objects.filter(account=current, version=proof_version).first()
        if not credential:
            raise PlatformAuthError('验证器绑定已变化，请重新登录')
    elif method == 'WECHAT':
        credential = PlatformWechatIdentity.objects.filter(account=current, version=proof_version,
            appid=settings.WECHAT_MINIAPP_APPID).first()
        if not credential:
            raise PlatformAuthError('微信绑定已变化，请重新登录')
    else:
        raise PlatformAuthError('平台登录方式无效')
    refresh = RefreshToken()
    refresh['token_kind'] = TOKEN_KIND
    refresh['platform_account_id'] = str(current.pk)
    refresh['security_version'] = str(current.security_version)
    refresh['auth_method'] = method
    refresh['session_id'] = str(uuid.uuid4())
    if method == 'OTP':
        refresh['otp_version'] = str(credential.version)
    elif method == 'SMS':
        refresh['phone_hash'] = _phone_hash(current.phone)
    else:
        refresh['wechat_version'] = str(credential.version)
    return str(refresh.access_token), str(refresh)


def account_for_token(token):
    if token.get('token_kind') != TOKEN_KIND:
        raise TokenError('平台登录身份无效')
    try:
        account = PlatformAccount.objects.get(pk=token.get('platform_account_id'), is_active=True)
    except (PlatformAccount.DoesNotExist, ValueError, TypeError, ValidationError):
        raise TokenError('平台账号不可用') from None
    if token.get('security_version') != str(account.security_version) or not _active_grant(account):
        raise TokenError('平台账号或授权已变化')
    if token.get('auth_method') == 'SMS':
        if (not account.is_phone_verified
                or token.get('phone_hash') != _phone_hash(account.phone)):
            raise TokenError('平台手机号验证已失效')
    elif token.get('auth_method') == 'OTP':
        credential = PlatformOtpCredential.objects.filter(account=account).first()
        if not credential or str(credential.version) != token.get('otp_version'):
            raise TokenError('平台验证器绑定已变化')
    elif token.get('auth_method') == 'WECHAT':
        identity = PlatformWechatIdentity.objects.filter(account=account, appid=settings.WECHAT_MINIAPP_APPID).first()
        if not identity or str(identity.version) != token.get('wechat_version'):
            raise TokenError('平台微信绑定已变化')
    else:
        raise TokenError('平台登录方式无效')
    return account


class PlatformAuthentication(BaseAuthentication):
    def authenticate_header(self, request):
        return 'Bearer realm="platform-account"'

    def authenticate(self, request):
        from django.contrib.auth.models import AnonymousUser
        if not settings.PLATFORM_IDENTITY_ENABLED:
            raise AuthenticationFailed('平台身份登录尚未启用')
        header = get_authorization_header(request).split()
        if len(header) != 2 or header[0].lower() != b'bearer':
            return None
        try:
            token = AccessToken(header[1])
            account = account_for_token(token)
        except DatabaseError as error:
            raise PlatformAuthenticationUnavailable() from error
        except (TokenError, ValueError, TypeError) as error:
            raise AuthenticationFailed('平台登录已失效，请重新登录') from error
        request.platform_account = account
        return AnonymousUser(), token


class IsPlatformAuthenticated(BasePermission):
    def has_permission(self, request, view):
        return getattr(request, 'platform_account', None) is not None
