"""Password + SMS authentication for browser accounts, separate from legacy OTP."""
import hashlib
import hmac
import secrets
from collections.abc import Mapping
from datetime import timedelta

from django.contrib.auth import authenticate
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from club_accounts.services import authenticate_account, get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from stores.db import transaction

from .models import PasswordLoginChallenge, ProviderRegistration
from .sms_transport import SmsUnavailable, send_verification_code


class PasswordSmsError(ValueError):
    def __init__(self, message, status=400, code='SMS_CODE_INVALID'):
        super().__init__(message)
        self.status, self.error_code = status, code


def masked_phone(phone):
    return phone[:3] + '****' + phone[-4:] if len(phone) >= 7 else '已绑定手机号'


def sms_digest(challenge, code):
    return salted_hmac('xa:password-login:sms:v1',
        f'{challenge.pk}:{challenge.account_id}:{challenge.phone}:{code}', algorithm='sha256').hexdigest()


def password_fingerprint(account):
    value = account.password
    if account.account_type in (ClubAccount.AccountType.BOSS, ClubAccount.AccountType.PROVIDER):
        value = f'{value}|membership:{account.business_cycle}'
    return hashlib.sha256(value.encode()).hexdigest()


def _challenge(account, scope, raw, *, lock=False):
    if not isinstance(raw, str) or len(raw) != 36:
        raise PasswordSmsError('短信登录请求无效，请重新输入账号密码')
    query = PasswordLoginChallenge.objects.select_for_update() if lock else PasswordLoginChallenge.objects
    try:
        challenge = query.get(pk=raw, account=account, scope=scope)
    except (PasswordLoginChallenge.DoesNotExist, ValueError):
        raise PasswordSmsError('短信登录请求无效，请重新输入账号密码') from None
    if (challenge.consumed or challenge.expires_at <= timezone.now()
            or challenge.password_fingerprint != password_fingerprint(account)):
        raise PasswordSmsError('短信登录请求已失效，请重新输入账号密码', code='SMS_CODE_EXPIRED')
    return challenge


def _prepare_sms(challenge):
    code = f'{secrets.randbelow(1000000):06d}'
    now = timezone.now()
    challenge.sms_hash = sms_digest(challenge, code)
    challenge.sms_sent_at = now
    challenge.sms_expires_at = now + timedelta(minutes=5)
    challenge.sms_attempts = 0
    challenge.save(update_fields=['sms_hash', 'sms_sent_at', 'sms_expires_at', 'sms_attempts'])
    return code


def _check_sms_cooldown(account):
    """All challenges for one account share a cooldown, even after a failed send."""
    latest = PasswordLoginChallenge.objects.filter(account=account).order_by('-sms_sent_at').first()
    if latest:
        remaining = (latest.sms_sent_at + timedelta(seconds=60) - timezone.now()).total_seconds()
        if remaining > 0:
            from math import ceil
            raise PasswordSmsError(f'验证码已发送，请 {ceil(remaining)} 秒后再试', 429, 'SMS_RATE_LIMITED')


def _check_locked_account(locked, authenticated):
    if (not locked.is_active or not locked.can_login or locked.password != authenticated.password):
        raise PasswordSmsError('账号状态或密码已变更，请重新登录', 403, 'ACCOUNT_CHANGED')
    if (locked.account_type != authenticated.account_type or
            (locked.account_type in (ClubAccount.AccountType.BOSS, ClubAccount.AccountType.PROVIDER)
             and locked.business_cycle != authenticated.business_cycle)):
        raise PasswordSmsError('账号门店身份已变化，请重新登录', 403, 'ACCOUNT_CHANGED')


def start_password_sms(account, scope):
    phone = account.phone or ''
    if not phone.isascii() or not phone.isdigit() or not 5 <= len(phone) <= 20:
        raise PasswordSmsError('当前账号尚未设置可用手机号，请联系门店管理员补齐后再登录', 400, 'PHONE_REQUIRED')
    with transaction.atomic():
        locked = ClubAccount.objects.select_for_update().get(pk=account.pk)
        _check_locked_account(locked, account)
        if locked.phone != phone:
            raise PasswordSmsError('手机号已变更，请重新登录验证', code='SMS_CODE_EXPIRED')
        _check_sms_cooldown(locked)
        PasswordLoginChallenge.objects.filter(account=locked, consumed=False).update(consumed=True)
        challenge = PasswordLoginChallenge.objects.create(
            account=locked, scope=scope, password_fingerprint=password_fingerprint(locked),
            phone=phone, sms_hash='', sms_sent_at=timezone.now(),
            sms_expires_at=timezone.now(), expires_at=timezone.now() + timedelta(minutes=10),
        )
        code = _prepare_sms(challenge)
    # No network call inside the database transaction: an uncertain response
    # must not roll back the persisted code or its account-wide cooldown.
    send_verification_code(challenge.phone, code)
    return challenge


def resend_password_sms(account, scope, raw):
    with transaction.atomic():
        locked = ClubAccount.objects.select_for_update().get(pk=account.pk)
        _check_locked_account(locked, account)
        challenge = _challenge(locked, scope, raw, lock=True)
        if locked.phone != challenge.phone:
            raise PasswordSmsError('手机号已变更，请重新登录验证', code='SMS_CODE_EXPIRED')
        _check_sms_cooldown(locked)
        code = _prepare_sms(challenge)
    send_verification_code(challenge.phone, code)
    return challenge


def verify_password_sms(account, scope, raw, code):
    error = None
    result = None
    with transaction.atomic():
        locked = ClubAccount.objects.select_for_update().get(pk=account.pk)
        _check_locked_account(locked, account)
        challenge = _challenge(locked, scope, raw, lock=True)
        if (challenge.sms_expires_at <= timezone.now() or challenge.sms_attempts >= 5
                or not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit()):
            raise PasswordSmsError('验证码已失效，请重新获取', code='SMS_CODE_EXPIRED')
        challenge.sms_attempts += 1
        challenge.save(update_fields=['sms_attempts'])
        if not hmac.compare_digest(challenge.sms_hash, sms_digest(challenge, code)):
            error = PasswordSmsError('验证码错误，请重新输入')
        else:
            if locked.phone != challenge.phone:
                error = PasswordSmsError('手机号已变更，请重新登录验证', code='SMS_CODE_EXPIRED')
            else:
                challenge.consumed = True
                challenge.save(update_fields=['consumed'])
                if not locked.is_phone_verified:
                    locked.is_phone_verified = True
                    locked.save(update_fields=['is_phone_verified', 'updated_at'])
                result = locked
    # Persist failed attempts before the view converts this error to a response.
    if error is not None:
        raise error
    return result


def sms_payload(challenge):
    return {
        'requiresSms': True,
        'smsChallenge': str(challenge.pk),
        'maskedPhone': masked_phone(challenge.phone),
        'retryAfter': 60,
        'expiresIn': 300,
    }


def user_payload(account, audience):
    from identifiers.services import get_entity_number
    return {
        'id': str(account.pk),
        'entity_no': get_entity_number(account),
        'username': account.username,
        'nickname': account.nickname or account.username,
        'role': 'customer' if audience == 'boss' else 'provider',
        'backendRole': account.account_type,
        'phone': account.phone or '',
        'isWechatBound': bool(account.is_openid_bound and (account.openid or account.wechat_identities.exists())),
        'isPhoneVerified': account.is_phone_verified,
    }


class PasswordLoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = 'login'

    def post(self, request):
        if not isinstance(request.data, Mapping):
            return Response({'code': 400, 'msg': '登录请求格式不正确'}, status=400)
        audience = request.data.get('audience', 'provider')
        if audience not in ('boss', 'provider'):
            return Response({'code': 400, 'msg': '登录端类型无效'}, status=400)
        username = request.data.get('username') or ''
        password = request.data.get('password') or ''
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 200 or len(password) > 4096:
            return Response({'code': 400, 'msg': '账号和密码格式不正确'}, status=400)
        username = username.strip()
        if not username or not password:
            return Response({'code': 400, 'msg': '请输入账号和密码'})
        account_type = ClubAccount.AccountType.BOSS if audience == 'boss' else ClubAccount.AccountType.PROVIDER
        if audience == 'provider' and ProviderRegistration.objects.filter(
                username__iexact=username).exclude(status='APPROVED').exists():
            return Response({'code': 403, 'msg': '注册申请尚未通过，请查询审核结果'}, status=403)
        account = authenticate_account(username, password, account_type=account_type)
        if account is None:
            legacy_user = authenticate(request=request, username=username, password=password)
            if legacy_user is not None:
                candidate = get_or_create_account_for_legacy_user(legacy_user)
                if candidate and candidate.account_type == account_type:
                    account = candidate
        if account is None:
            return Response({'code': 400, 'msg': '账号或密码错误'})
        if not account.is_active or not account.can_login:
            return Response({'code': 403, 'msg': '账号已被禁用'}, status=403)
        from club_accounts.models import OtpCredential
        from club_accounts.otp import otp_login_gate
        use_sms = request.data.get('verificationMethod') == 'sms' or bool(request.data.get('smsChallenge'))
        if not use_sms and (request.data.get('otpChallenge') or OtpCredential.objects.filter(account=account).exists()):
            otp = otp_login_gate(account, request.data, audience, existing_only=True)
            if isinstance(otp, Response):
                return otp
            token, refresh = issue_account_tokens(account, otp_verified=True, auth_method='password_otp')
            return Response({'code': 0, 'data': {
                'token': token, 'refreshToken': refresh, 'userInfo': user_payload(account, audience),
            }})
        raw = request.data.get('smsChallenge')
        try:
            if request.data.get('resend') is True:
                challenge = resend_password_sms(account, audience, raw)
                return Response({'code': 0, 'data': sms_payload(challenge)})
            if not raw:
                challenge = start_password_sms(account, audience)
                return Response({'code': 0, 'data': sms_payload(challenge)})
            account = verify_password_sms(account, audience, raw, request.data.get('smsCode'))
            token, refresh = issue_account_tokens(account, auth_method='password_sms')
            return Response({'code': 0, 'data': {
                'token': token, 'refreshToken': refresh, 'userInfo': user_payload(account, audience),
            }})
        except PasswordSmsError as error:
            return Response({'code': error.status, 'errorCode': error.error_code,
                'msg': str(error)}, status=error.status)
        except SmsUnavailable as error:
            return Response({'code': 503, 'errorCode': 'SMS_UNAVAILABLE', 'msg': str(error)}, status=503)


class PasswordView(APIView):
    """Let a phone-verified boss establish or rotate browser password credentials."""
    from club_accounts.permissions import IsClubAccountAuthenticated
    permission_classes = [IsClubAccountAuthenticated]
    throttle_scope = 'login'

    def post(self, request):
        import re
        from django.contrib.auth import get_user_model

        account = request.account
        if account.account_type != ClubAccount.AccountType.BOSS:
            return Response({'code': 403, 'msg': '当前端仅支持老板账号设置登录密码'}, status=403)
        if not account.is_phone_verified:
            return Response({'code': 400, 'msg': '请先完成手机号验证，再设置登录密码'}, status=400)
        username = str(request.data.get('username') or account.username).strip()
        password = request.data.get('password') or ''
        if not re.fullmatch(r'[A-Za-z0-9_.-]{3,32}', username):
            return Response({'code': 400, 'msg': '登录账号须为 3–32 位字母、数字、点、下划线或短横线'})
        if not isinstance(password, str) or not 8 <= len(password) <= 128:
            return Response({'code': 400, 'msg': '登录密码须为 8–128 位'})
        if ClubAccount.objects.filter(username=username).exclude(pk=account.pk).exists():
            return Response({'code': 409, 'msg': '该登录账号已被使用，请更换'}, status=409)
        with transaction.atomic():
            locked = ClubAccount.objects.select_for_update().get(pk=account.pk)
            if not locked.is_phone_verified:
                return Response({'code': 400, 'msg': '手机号验证状态已变化，请重新验证'}, status=400)
            locked.username = username
            locked.set_password(password)
            locked.save(update_fields=['username', 'password', 'updated_at'])
            mapping = getattr(locked, 'legacy_mapping', None)
            if mapping:
                user = get_user_model().objects.filter(pk=mapping.legacy_user_id).first()
                if user:
                    user.username = username
                    user.set_password(password)
                    user.save(update_fields=['username', 'password'])
        return Response({'code': 0, 'data': {'username': username, 'passwordConfigured': True}})
