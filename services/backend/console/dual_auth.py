"""Neutral console password+SMS and bound-WeChat authentication."""
import hashlib
import hmac
import secrets
from collections.abc import Mapping
from datetime import timedelta

from django.core import signing
from django.conf import settings as global_settings
from django.db import DatabaseError, IntegrityError, transaction as control_transaction
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens
from stores.admin_entry import AdminLoginError, _active_store, _account, _username, login_context
from stores.context import current_store, store_context
from stores.db import transaction
from stores.media import decode_login_scene, encode_login_scene
from stores.models import (AdminLoginHandoff, Store, PlatformAccount, PlatformAuditEvent,
                           PlatformWechatBindingIntent, PlatformWechatIdentity)
from users.password_login import (PasswordSmsError, resend_password_sms, sms_payload,
    start_password_sms, verify_password_sms)
from users.sms_transport import SmsUnavailable
from users.web_login import create_bridge_code
from users.views import _wechat_code2session
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
            except DatabaseError:
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
        except DatabaseError:
            return failure('门店登录服务暂不可用，请稍后重试', 503)


def handoff_for(ticket, *, lock=False):
    try:
        store_id, ticket_id = decode_login_scene(ticket)
        store = current_store()
        if store_id is not None and (store is None or store.pk != store_id):
            raise ValueError
        query = AdminLoginHandoff.objects.select_for_update() if lock else AdminLoginHandoff.objects
        handoff = query.get(ticket=ticket_id)
        if store_id is None and handoff.purpose == 'bind_wechat':
            raise ValueError
        if handoff.store_slug != (store.slug if store else ''):
            raise ValueError
        return handoff
    except (ValueError, TypeError, AttributeError, AdminLoginHandoff.DoesNotExist):
        raise ValueError('登录请求无效，请在网页重新生成二维码') from None


def ensure_handoff_active(session):
    if session.expires_at <= timezone.now():
        raise ValueError('登录二维码已过期，请刷新后重试')


def ensure_handoff_bridge(session, request):
    digest = hashlib.sha256(str(request.data.get('bridge_token', '')).encode()).hexdigest()
    if not session.bridge_token_hash or not hmac.compare_digest(session.bridge_token_hash, digest):
        raise ValueError('请在已授权的微信页面内继续操作')


def handoff_payload(session):
    store = current_store()
    with store_context(store):
        image, launch = create_bridge_code(session)
        return {'ticket': encode_login_scene(session.ticket, platform=not session.store_slug),
            'store': {'slug': store.slug, 'name': store.name} if store else None,
            'qr_image': image, 'launch_url': launch, 'expires_in': 300, 'requiresWechatBridge': True}


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


def resolve_openid_account(openid, *, identity_kind='auto'):
    matches = []
    if identity_kind != 'platform':
        stores = Store.objects.filter(status__in=[Store.Status.ACTIVE, Store.Status.CONFIGURING], initialized_at__isnull=False,
            database_alias__in=settings.DATABASES)
        for store in stores:
            with store_context(store):
                account = ClubAccount.objects.filter(openid=openid, account_type=ClubAccount.AccountType.STAFF,
                    is_openid_bound=True, is_active=True, can_login=True).first()
                if account and console_account(store, account.pk):
                    matches.append(('store', store, account))
    if identity_kind != 'store' and global_settings.PLATFORM_IDENTITY_ENABLED:
        from stores.platform_auth import _active_grant
        identity = PlatformWechatIdentity.objects.select_related('account').filter(
            appid=global_settings.WECHAT_MINIAPP_APPID, openid=openid, account__is_active=True).first()
        if identity and _active_grant(identity.account):
            matches.append(('platform', None, identity.account))
    if not matches:
        raise ValueError('当前微信尚未绑定后台账号，请使用账号密码登录并完成微信初始化')
    if len(matches) != 1:
        raise LookupError('当前微信关联多个后台身份，请选择平台或门店身份；多门店请使用账号登录')
    return matches[0]


def platform_binding_account(session):
    from stores.platform_auth import _active_grant
    if not global_settings.PLATFORM_IDENTITY_ENABLED or not session.platform_account_id:
        return None
    intent = PlatformWechatBindingIntent.objects.select_related('account').filter(handoff=session).first()
    if (not intent or intent.account_id != session.platform_account_id or not intent.account.is_active
            or intent.security_version != intent.account.security_version or not _active_grant(intent.account)):
        return None
    return intent.account


class ConsoleWechatStartView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = 'login'

    def post(self, request):
        if not isinstance(request.data, Mapping):
            return failure('微信登录请求格式不正确')
        if not settings.WECHAT_MINIAPP_APPID or not settings.WECHAT_MINIAPP_SECRET:
            return failure('微信登录服务尚未配置，请联系平台管理员', 503, 'WECHAT_NOT_CONFIGURED')
        purpose = request.data.get('purpose', 'login')
        store = None
        account_id = None
        store_slug = ''
        if purpose == 'bind_wechat':
            try:
                claim = signing.loads(request.data.get('bindToken', ''), salt=BIND_SALT, max_age=900)
                if (not isinstance(claim, dict) or claim.get('purpose') != 'console-wechat-bind'
                        or type(claim.get('account_id')) is not int):
                    raise ValueError
                store = _active_store(claim['store_slug'])
                account = console_account(store, claim['account_id'])
                if not account or account.username != claim['username']:
                    raise ValueError
                if account.openid and account.is_openid_bound:
                    return failure('当前账号已完成微信绑定，请重新选择登录方式', 409, 'ACCOUNT_CONFLICT')
                account_id, store_slug = account.pk, store.slug
            except (signing.BadSignature, KeyError, TypeError, ValueError):
                return failure('微信初始化请求无效或已过期，请重新完成密码与短信验证', 400, 'WECHAT_BIND_INVALID')
        elif purpose == 'login':
            kind = request.data.get('identityKind', 'auto')
            if kind not in ('auto', 'platform', 'store'):
                return failure('登录身份无效')
            purpose = {'platform': 'platform_login', 'store': 'store_login', 'auto': 'login'}[kind]
        else:
            return failure('微信登录用途无效')
        secret = secrets.token_urlsafe(32)
        session = AdminLoginHandoff.objects.create(
            poll_token_hash=hashlib.sha256(secret.encode()).hexdigest(), purpose=purpose,
            store_slug=store_slug, expected_account_id=account_id,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        try:
            with store_context(store):
                payload = handoff_payload(session)
        except (OSError, ValueError):
            session.delete()
            return failure('微信登录码暂时无法获取，请稍后重试', 503, 'WECHAT_UNAVAILABLE')
        return Response({'code': 0, 'data': {**payload, 'poll_token': secret}})


class ConsoleWechatAuthorizeView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = 'login'

    def post(self, request):
        if not isinstance(request.data, Mapping):
            return failure('微信登录请求格式不正确')
        code = request.data.get('code')
        if not isinstance(code, str) or not code.strip() or len(code) > 256:
            return failure('请重新获取微信授权凭证', code='WECHAT_CODE_INVALID')
        try:
            candidate = handoff_for(request.data.get('ticket'))
            ensure_handoff_active(candidate)
            with store_context(None):
                openid = _wechat_code2session(code.strip())
            with control_transaction.atomic(using='default'):
                candidate = handoff_for(request.data.get('ticket'), lock=True)
                ensure_handoff_active(candidate)
                if candidate.confirmed_at:
                    return failure('该请求已确认，请返回网页', 409, 'ACCOUNT_CONFLICT')
                if candidate.wechat_openid and candidate.wechat_openid != openid:
                    return failure('当前微信身份与之前授权不一致，请重新扫码', 409, 'ACCOUNT_CONFLICT')
                store = None
                if candidate.purpose == 'bind_wechat':
                    store = _active_store(candidate.store_slug)
                    account = console_account(store, candidate.expected_account_id)
                    if not account or (account.openid and account.openid != openid):
                        return failure('当前账号已绑定其他微信，请联系门店管理员', 409, 'ACCOUNT_CONFLICT')
                    with store_context(store):
                        if ClubAccount.objects.filter(openid=openid).exclude(pk=account.pk).exists():
                            return failure('当前微信已绑定其他账号，请联系门店管理员', 409, 'ACCOUNT_CONFLICT')
                elif candidate.purpose == 'platform_bind':
                    account = platform_binding_account(candidate)
                    if not account or PlatformWechatIdentity.objects.filter(appid=global_settings.WECHAT_MINIAPP_APPID,
                                                                              openid=openid).exclude(account=account).exists():
                        return failure('平台账号不可用或微信已绑定其他平台账号', 409, 'ACCOUNT_CONFLICT')
                else:
                    kind = {'platform_login': 'platform', 'store_login': 'store', 'login': 'auto'}.get(candidate.purpose)
                    if kind is None:
                        return failure('微信登录用途无效')
                    resolved, store, account = resolve_openid_account(openid, identity_kind=kind)
                    if resolved == 'platform':
                        candidate.platform_account = account
                        candidate.purpose = 'platform_login'
                    else:
                        candidate.store_slug = store.slug
                        candidate.expected_account_id = account.pk
                bridge_token = secrets.token_urlsafe(32)
                candidate.wechat_openid = openid
                candidate.bridge_token_hash = hashlib.sha256(bridge_token.encode()).hexdigest()
                candidate.save(update_fields=['purpose', 'store_slug', 'expected_account_id', 'platform_account',
                                              'wechat_openid', 'bridge_token_hash'])
            return Response({'code': 0, 'data': {
                'store': {'slug': store.slug, 'name': store.name} if store else None, 'bridge_token': bridge_token,
                'requiresPhoneAuthorization': False, 'requiresSms': False, 'canConfirm': True,
                'maskedPhone': '',
            }})
        except LookupError as error:
            return failure(str(error), 409, 'ADMIN_STORE_AMBIGUOUS')
        except ValueError as error:
            return failure(str(error), 400, 'WECHAT_AUTHORIZE_INVALID')
        except (OSError, DatabaseError):
            return failure('微信授权暂时无法完成，请稍后重试', 503, 'WECHAT_UNAVAILABLE')


class ConsoleWechatConfirmView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = 'login'

    def post(self, request):
        if not isinstance(request.data, Mapping):
            return failure('微信登录请求格式不正确')
        try:
            with control_transaction.atomic(using='default'):
                session = handoff_for(request.data.get('ticket'), lock=True)
                ensure_handoff_active(session)
                ensure_handoff_bridge(session, request)
                if not session.wechat_openid:
                    return failure('请先完成微信授权')
                if session.confirmed_at:
                    return Response({'code': 0, 'data': {'status': 'CONFIRMED'}})
                if session.purpose in ('platform_bind', 'platform_login'):
                    from stores.database_identity import lock_store_registration
                    from stores.platform_auth import _active_grant
                    lock_store_registration()
                    account = PlatformAccount.objects.select_for_update().filter(pk=session.platform_account_id, is_active=True).first()
                    if not account or not _active_grant(account):
                        return failure('平台账号或授权已失效，请重新登录', 403, 'ACCOUNT_UNAVAILABLE')
                    if session.purpose == 'platform_bind':
                        if not platform_binding_account(session):
                            return failure('平台登录已变化，请重新发起绑定', 403, 'ACCOUNT_UNAVAILABLE')
                        try:
                            with control_transaction.atomic(using='default'):
                                PlatformWechatIdentity.objects.create(account=account, appid=global_settings.WECHAT_MINIAPP_APPID,
                                                                     openid=session.wechat_openid)
                        except IntegrityError:
                            return failure('账号或微信已绑定其他平台身份', 409, 'ACCOUNT_CONFLICT')
                        PlatformAuditEvent.objects.create(account=account, action='auth.wechat_bound',
                            target_type='platform_account', target_id=str(account.pk))
                    elif not PlatformWechatIdentity.objects.filter(account=account, appid=global_settings.WECHAT_MINIAPP_APPID,
                                                                    openid=session.wechat_openid).exists():
                        return failure('平台微信绑定已变化，请重新登录', 409, 'ACCOUNT_CONFLICT')
                else:
                    if not session.store_slug or not session.expected_account_id:
                        return failure('请先完成微信授权')
                    store = _active_store(session.store_slug)
                    account = console_account(store, session.expected_account_id)
                    if not account:
                        return failure('后台账号不可用，请联系门店管理员', 403, 'ACCOUNT_UNAVAILABLE')
                    if session.purpose == 'bind_wechat':
                        with store_context(store), transaction.atomic():
                            locked = ClubAccount.objects.select_for_update().get(pk=account.pk)
                            if locked.openid and locked.openid != session.wechat_openid:
                                return failure('当前账号已绑定其他微信，请联系门店管理员', 409, 'ACCOUNT_CONFLICT')
                            if ClubAccount.objects.select_for_update().filter(
                                    openid=session.wechat_openid).exclude(pk=locked.pk).exists():
                                return failure('当前微信已绑定其他账号，请联系门店管理员', 409, 'ACCOUNT_CONFLICT')
                            locked.openid = session.wechat_openid
                            locked.wechat_appid = global_settings.WECHAT_MINIAPP_APPID
                            locked.is_openid_bound = True
                            locked.save(update_fields=['openid', 'wechat_appid', 'is_openid_bound', 'updated_at'])
                    elif account.openid != session.wechat_openid or not account.is_openid_bound:
                        return failure('账号微信绑定已变化，请重新登录', 409, 'ACCOUNT_CONFLICT')
                    session.confirmed_account_id = account.pk
                session.confirmed_at = timezone.now()
                session.save(update_fields=['confirmed_account_id', 'confirmed_at'])
                return Response({'code': 0, 'data': {'status': 'CONFIRMED'}})
        except ValueError as error:
            return failure(str(error), 400, 'WECHAT_CONFIRM_INVALID')
        except DatabaseError:
            return failure('微信确认暂时不可用，请稍后重试', 503, 'WECHAT_UNAVAILABLE')


class ConsoleWechatStatusView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        try:
            session = handoff_for(request.query_params.get('ticket'))
            digest = hashlib.sha256(str(request.query_params.get('poll_token', '')).encode()).hexdigest()
            if not hmac.compare_digest(session.poll_token_hash, digest):
                return failure('无法核对当前浏览器的登录请求，请重新扫码', 403)
            if session.expires_at <= timezone.now():
                return Response({'code': 0, 'data': {'status': 'EXPIRED'}})
            if session.purpose in ('platform_bind', 'platform_login'):
                if not session.confirmed_at:
                    return Response({'code': 0, 'data': {'status': 'PENDING'}})
                if session.purpose == 'platform_bind' and not platform_binding_account(session):
                    return failure('平台登录已变化，请重新登录', 401, 'ACCOUNT_UNAVAILABLE')
                identity = PlatformWechatIdentity.objects.select_related('account').filter(
                    account_id=session.platform_account_id, appid=global_settings.WECHAT_MINIAPP_APPID,
                    openid=session.wechat_openid).first()
                if not identity:
                    return failure('平台微信绑定已变化，请重新登录', 409, 'ACCOUNT_CONFLICT')
                from stores.platform_auth import issue_tokens
                from stores.platform_views import profile
                token, refresh = issue_tokens(identity.account, 'WECHAT', identity.version)
                response = Response({'code': 0, 'data': {'status': 'CONFIRMED', 'identity_kind': 'platform',
                    'token': token, 'refresh': refresh, 'profile': profile(identity.account)}})
                response['Cache-Control'] = 'no-store'
                return response
            if not session.confirmed_account_id:
                return Response({'code': 0, 'data': {'status': 'PENDING'}})
            store = _active_store(session.store_slug)
            account = console_account(store, session.confirmed_account_id)
            if not account or account.openid != session.wechat_openid or not account.is_openid_bound:
                return failure('账号微信绑定已变化，请重新登录', 409, 'ACCOUNT_CONFLICT')
            with store_context(store):
                token, refresh = issue_account_tokens(account, auth_method='wechat')
                return Response({'code': 0, 'data': {'status': 'CONFIRMED', 'token': token,
                    'refresh': refresh, 'profile': _profile_payload(account), **_store_payload(store)}})
        except ValueError as error:
            return failure(str(error), 400, 'WEB_LOGIN_INVALID')
        except DatabaseError:
            return failure('微信登录暂时不可用，请稍后重试', 503, 'WECHAT_UNAVAILABLE')
