"""Neutral console password+SMS and bound-WeChat authentication."""
from collections.abc import Mapping

from django.core import signing
from django.db import DatabaseError, InterfaceError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens
from stores.admin_entry import AdminLoginError, _active_store, _account, _username, login_context
from stores.context import store_context
from stores.db import transaction
from stores.models import Store
from users.password_login import (PasswordSmsError, resend_password_sms, sms_payload,
    start_password_sms, verify_password_sms)
from users.sms_transport import SmsUnavailable
from stores.conf import settings

from .auth import _profile_payload, _store_payload
from .permissions import is_console_account


SMS_SALT = 'xa.console.password-sms.v1'
BIND_SALT = 'xa.console.wechat-bind.v1'


def failure(message, status=400, code='ADMIN_LOGIN_INVALID'):
    return Response({'code': status, 'errorCode': code, 'msg': message}, status=status)


def signed_identity(raw, username, password):
    try:
        claim = signing.loads(raw, salt=SMS_SALT, max_age=600)
        if (not isinstance(claim, dict) or claim.get('purpose') != 'console-password-sms'
                or type(claim.get('account_id')) is not int):
            raise ValueError
        store = _active_store(claim['store_slug'])
        prefix, normalized = _username(username)
        if prefix is not None and prefix != store.slug:
            raise ValueError
        account = _account(store, normalized, password)
        if account is None or account.pk != claim['account_id'] or account.username != claim['username']:
            raise ValueError
        return store, account, claim
    except (signing.BadSignature, KeyError, TypeError, ValueError) as exc:
        raise PasswordSmsError('短信登录请求无效或已过期，请重新输入账号密码', code='SMS_CODE_EXPIRED') from exc


def sign_sms_challenge(store, account, challenge):
    return signing.dumps({'purpose': 'console-password-sms', 'store_slug': store.slug,
        'account_id': account.pk, 'username': account.username, 'challenge': str(challenge.pk)},
        salt=SMS_SALT, compress=True)


class ConsolePasswordLoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = 'login'

    def post(self, request):
        if not isinstance(request.data, Mapping):
            return failure('账号和密码格式不正确')
        username = request.data.get('username') or ''
        password = request.data.get('password') or ''
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 200 or len(password) > 4096:
            return failure('账号和密码格式不正确')
        username = username.strip()
        if not username or not password:
            return failure('请输入账号和密码')
        if any(key in request.data for key in ('store', 'store_slug', 'database_alias')):
            return failure('登录由账号识别身份，不接受门店路由参数')
        identity_kind = request.data.get('identityKind')
        if identity_kind not in (None, '', 'store', 'platform'):
            return failure('登录身份无效')
        if identity_kind == 'platform':
            if not settings.PLATFORM_IDENTITY_ENABLED:
                return failure('平台身份登录尚未启用', 503)
            from stores.platform_views import PlatformPasswordLoginView
            return PlatformPasswordLoginView().post(request)
        # Fresh unqualified credentials must be checked in both domains.
        # A challenge is bound to the identity selected when it was issued.
        if (settings.PLATFORM_IDENTITY_ENABLED and not identity_kind and '/' not in username
                and not request.data.get('smsChallenge') and not request.data.get('otpChallenge')):
            from stores.platform_auth import authenticate_platform, PlatformAuthError
            try:
                platform = authenticate_platform(username, password)
            except PlatformAuthError:
                platform = None
            except (DatabaseError, InterfaceError):
                return failure('平台登录服务暂不可用，请稍后重试', 503)
            if platform:
                try:
                    login_context(username, password, request.data)
                except AdminLoginError as error:
                    if error.status == 503:
                        return failure(str(error), error.status, error.code)
                    if error.code != 'ADMIN_STORE_AMBIGUOUS':
                        from stores.platform_views import PlatformPasswordLoginView
                        return PlatformPasswordLoginView().post(request)
                return failure('账号同时匹配平台和门店身份，请选择身份后继续', 409, 'ADMIN_IDENTITY_AMBIGUOUS')
        raw = request.data.get('smsChallenge')
        try:
            if raw:
                store, account, claim = signed_identity(raw, username, password)
            else:
                store, account, gate_data = login_context(username, password, request.data)
                claim = None
            with store_context(store):
                from club_accounts.models import OtpCredential
                from club_accounts.otp import otp_login_gate
                use_sms = request.data.get('verificationMethod') == 'sms' or bool(raw)
                if not use_sms and (request.data.get('otpChallenge') or OtpCredential.objects.filter(account=account).exists()):
                    otp = otp_login_gate(account, gate_data, 'admin', existing_only=True)
                    if isinstance(otp, Response):
                        payload = otp.data.get('data')
                        if isinstance(payload, dict) and payload.get('requiresOTP'):
                            from stores.admin_entry import bind_challenge
                            payload['otpChallenge'] = bind_challenge(payload['otpChallenge'], store, account)
                            payload.update(_store_payload(store))
                        return otp
                    token, refresh = issue_account_tokens(account, otp_verified=True, auth_method='password_otp')
                    payload = {'token': token, 'refresh': refresh, 'profile': _profile_payload(account), **_store_payload(store)}
                    if not account.is_openid_bound or not (account.openid or account.wechat_identities.exists()):
                        payload['bindToken'] = signing.dumps({'purpose': 'console-wechat-bind', 'store_slug': store.slug,
                            'account_id': account.pk, 'username': account.username}, salt=BIND_SALT, compress=True)
                    return Response({'code': 0, 'data': payload})
                if request.data.get('resend') is True:
                    if not claim:
                        raise PasswordSmsError('短信登录请求无效，请重新输入账号密码')
                    challenge = resend_password_sms(account, 'admin', claim['challenge'])
                    payload = sms_payload(challenge)
                    payload['smsChallenge'] = sign_sms_challenge(store, account, challenge)
                    return Response({'code': 0, 'data': payload})
                if not claim:
                    challenge = start_password_sms(account, 'admin')
                    payload = sms_payload(challenge)
                    payload['smsChallenge'] = sign_sms_challenge(store, account, challenge)
                    payload.update(_store_payload(store))
                    return Response({'code': 0, 'data': payload})
                account = verify_password_sms(account, 'admin', claim['challenge'], request.data.get('smsCode'))
                token, refresh = issue_account_tokens(account, auth_method='password_sms')
                payload = {'token': token, 'refresh': refresh,
                    'profile': _profile_payload(account), **_store_payload(store)}
                if not account.is_openid_bound or not (account.openid or account.wechat_identities.exists()):
                    payload['bindToken'] = signing.dumps({'purpose': 'console-wechat-bind', 'store_slug': store.slug,
                        'account_id': account.pk, 'username': account.username}, salt=BIND_SALT, compress=True)
                return Response({'code': 0, 'data': payload})
        except AdminLoginError as error:
            return failure(str(error), error.status, error.code)
        except PasswordSmsError as error:
            return failure(str(error), error.status, error.error_code)
        except SmsUnavailable as error:
            return failure(str(error), 503, 'SMS_UNAVAILABLE')
        except (DatabaseError, InterfaceError):
            return failure('门店登录服务暂不可用，请稍后重试', 503)


def console_account(store, account_id):
    with store_context(store):
        account = ClubAccount.objects.filter(pk=account_id, account_type=ClubAccount.AccountType.STAFF,
            is_active=True, can_login=True).first()
        if not is_console_account(account):
            return None
        if store.status == Store.Status.CONFIGURING:
            from stores.setup_access import is_setup_admin
            return account if is_setup_admin(store, account) else None
        return account
