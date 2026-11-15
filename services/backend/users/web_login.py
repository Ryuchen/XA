"""Browser authentication through the minimal authorized miniapp bridge."""
import base64
import hashlib
import hmac
import json
import secrets
import uuid
import urllib.parse
import urllib.request
from datetime import timedelta

from stores.conf import settings
from stores.db import transaction
from stores.context import current_store
from stores.media import decode_login_scene, encode_login_scene
from django.utils import timezone
from django.db import DatabaseError, IntegrityError, transaction as django_transaction
from django.utils.crypto import salted_hmac
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens
from common.catalog_images import bounded_public_image_url
from .models import WebLoginSession
from .sms_transport import SmsUnavailable, send_verification_code
from .views import (WechatLoginView, WechatLoginError, _wechat_code2session, _wechat_code2session_proof,
    _wechat_get_phone, _wechat_get_access_token, _ensure_boss_login_allowed,
    _assert_phone_available, _lock_wechat_phone, _legacy_user_for_account)


def fail(message, status=400, error_code='WEB_LOGIN_INVALID'):
    return Response({'code': status, 'errorCode': error_code, 'msg': message}, status=status)


def is_admin_handoff(ticket):
    """The miniapp uses one URL; the control-plane ticket selects console auth."""
    try:
        from stores.models import AdminLoginHandoff
        store_id, ticket_id = decode_login_scene(ticket)
        try:
            # Keep a failed control read from breaking a surrounding transaction.
            with django_transaction.atomic(using='default'):
                handoff = AdminLoginHandoff.objects.filter(ticket=ticket_id).first()
                if not handoff:
                    return False
                if store_id is None:
                    return handoff.purpose != 'bind_wechat'
                from stores.models import Store
                return Store.objects.filter(pk=store_id, slug=handoff.store_slug).exists()
        except DatabaseError:
            # Only an existing session in the already resolved tenant can be
            # treated as a tenant login. Unknown/platform tickets fail closed.
            store = current_store()
            if (store is not None and store_id == store.pk
                    and WebLoginSession.objects.filter(ticket=ticket_id).exists()):
                return False
            raise
    except (ValueError, TypeError, AttributeError):
        return False


def session_for(ticket, *, lock=False):
    try:
        internal_ticket = isinstance(ticket, uuid.UUID)
        store_id, ticket_id = decode_login_scene(ticket)
        store = current_store()
        if store is None or (not internal_ticket and store_id != store.pk):
            raise ValueError('Login ticket store mismatch')
        query = WebLoginSession.objects.select_for_update() if lock else WebLoginSession.objects
        return query.get(ticket=ticket_id)
    except (ValueError, TypeError, AttributeError, WebLoginSession.DoesNotExist):
        raise WechatLoginError('登录请求无效，请在网页重新生成二维码', 'WEB_LOGIN_INVALID', 400)


def ensure_active(session):
    if session.expires_at <= timezone.now():
        raise WechatLoginError('登录二维码已过期，请在网页刷新后重试', 'WEB_LOGIN_EXPIRED', 410)


def sms_digest(session, code):
    return salted_hmac('xa:web-login:phone:v1', f'{session.ticket}:{session.phone}:{code}', algorithm='sha256').hexdigest()


def ensure_bridge(session, request):
    token = request.data.get('bridge_token', '')
    digest = hashlib.sha256(str(token).encode()).hexdigest()
    if not session.bridge_token_hash or not hmac.compare_digest(session.bridge_token_hash, digest):
        raise WechatLoginError('请在已授权的微信页面内继续操作', 'WEB_LOGIN_BRIDGE_REQUIRED', 403)


def ensure_account_login(account, audience):
    expected = ClubAccount.AccountType.BOSS if audience == 'boss' else ClubAccount.AccountType.PROVIDER
    if account.account_type != expected:
        raise WechatLoginError('该微信账号不属于当前登录端，请使用对应端登录', 'ACCOUNT_ROLE_MISMATCH', 403)
    if audience == 'boss':
        _ensure_boss_login_allowed(account)
    elif not account.is_active or not account.can_login:
        raise WechatLoginError('该陪玩账号已被禁用，请联系门店管理员', 'ACCOUNT_DISABLED', 403)


def wx_post(path, body, *, binary=False):
    access = _wechat_get_access_token()
    target = 'https://api.weixin.qq.com/' + path + '?access_token=' + urllib.parse.quote(access, safe='')
    request = urllib.request.Request(target, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=8) as response:
        value = response.read()
    if binary and value.startswith(b'\x89PNG'):
        return value
    result = json.loads(value.decode())
    if result.get('errcode'):
        raise WechatLoginError('微信授权入口暂不可用，请稍后重试或联系平台客服', 'WECHAT_UNAVAILABLE', 503)
    return result


def create_bridge_code(session):
    environment = getattr(settings, 'WECHAT_WEB_LOGIN_ENV', 'release')
    from stores.models import AdminLoginHandoff
    platform = isinstance(session, AdminLoginHandoff) and not session.store_slug
    ticket = encode_login_scene(session.ticket, platform=platform)
    image = wx_post('wxa/getwxacodeunlimit', {'scene': ticket,
        'page': 'pages/web-login/index', 'check_path': environment == 'release',
        'env_version': environment, 'width': 360}, binary=True)
    if not isinstance(image, bytes):
        raise WechatLoginError('微信未返回有效登录码，请稍后重试', 'WECHAT_UNAVAILABLE', 503)
    launch = ''
    try:
        result = wx_post('wxa/generate_urllink', {'path': 'pages/web-login/index',
            'query': 'ticket=' + ticket, 'env_version': environment,
            'is_expire': True, 'expire_type': 0, 'expire_time': int(session.expires_at.timestamp())})
        launch = result.get('url_link', '')
    except (OSError, ValueError):
        pass
    return 'data:image/png;base64,' + base64.b64encode(image).decode(), launch


class WebLoginStartView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = 'login'

    def post(self, request):
        audience = request.data.get('audience', 'boss')
        purpose = request.data.get('purpose', 'login')
        if audience not in ('boss', 'provider') or purpose not in ('login', 'bind_phone', 'bind_wechat'):
            return fail('登录用途无效')
        owner = None
        if purpose != 'login':
            owner = getattr(request, 'account', None)
            expected = ClubAccount.AccountType.BOSS if audience == 'boss' else ClubAccount.AccountType.PROVIDER
            if not owner or owner.account_type != expected:
                return fail('请先登录当前账号', 401)
            if purpose == 'bind_phone' and audience != 'boss':
                return fail('当前端不支持该手机号操作')
        if not settings.WECHAT_MINIAPP_APPID or not settings.WECHAT_MINIAPP_SECRET:
            return fail('微信登录服务尚未配置，请联系平台客服', 503, 'WECHAT_NOT_CONFIGURED')
        secret = secrets.token_urlsafe(32)
        session = WebLoginSession.objects.create(poll_token_hash=hashlib.sha256(secret.encode()).hexdigest(),
            audience=audience, purpose=purpose, expected_account=owner, expires_at=timezone.now() + timedelta(minutes=5))
        try:
            image, launch = create_bridge_code(session)
        except (OSError, ValueError):
            session.delete()
            return fail('微信登录码暂时无法获取，请稍后重试或联系平台客服', 503, 'WECHAT_UNAVAILABLE')
        store = current_store()
        return Response({'code': 0, 'data': {'ticket': encode_login_scene(session.ticket), 'poll_token': secret,
            'store': {'slug': store.slug, 'name': store.name} if store is not None else None,
            'qr_image': image, 'expires_in': 300, 'launch_url': launch, 'requiresWechatBridge': True}})


class WebLoginStatusView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        try:
            session = session_for(request.query_params.get('ticket'))
            digest = hashlib.sha256(str(request.query_params.get('poll_token', '')).encode()).hexdigest()
            if not hmac.compare_digest(session.poll_token_hash, digest):
                return fail('无法核对当前浏览器的登录请求，请重新扫码', 403)
            if session.expires_at <= timezone.now():
                return Response({'code': 0, 'data': {'status': 'EXPIRED'}})
            if not session.confirmed_account_id:
                return Response({'code': 0, 'data': {'status': 'PENDING'}})
            account = session.confirmed_account
            from identifiers.services import get_entity_number
            ensure_account_login(account, session.audience)
            token, refresh = issue_account_tokens(account, auth_method='wechat')
            return Response({'code': 0, 'data': {'status': 'CONFIRMED', 'token': token, 'refreshToken': refresh,
                'userInfo': {'id': str(account.pk), 'entity_no': get_entity_number(account), 'username': account.username,
                    'nickname': account.nickname or account.username,
                    'role': 'customer' if session.audience == 'boss' else 'provider',
                    'avatar': bounded_public_image_url(account.avatar_url or ''),
                    'phone': account.phone or '', 'isWechatBound': True, 'isPhoneVerified': account.is_phone_verified}}})
        except WechatLoginError as error:
            return fail(str(error), error.code, error.error_code)


class WebLoginAuthorizeView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = 'login'

    def post(self, request):
        if is_admin_handoff(request.data.get('ticket')):
            from console.dual_auth import ConsoleWechatAuthorizeView
            return ConsoleWechatAuthorizeView().post(request)
        code, phone_code = request.data.get('code'), request.data.get('phoneCode', '')
        if not isinstance(code, str) or not code.strip() or len(code) > 256 or not isinstance(phone_code, str) or len(phone_code) > 256:
            return fail('请重新获取微信授权凭证')
        if 'phone' in request.data:
            return fail('手机号必须通过微信授权读取')
        try:
            candidate = session_for(request.data.get('ticket'))
            ensure_active(candidate)
            if not settings.WECHAT_MINIAPP_APPID or not settings.WECHAT_MINIAPP_SECRET:
                return fail('微信授权尚未配置', 503, 'WECHAT_NOT_CONFIGURED')
            if candidate.audience == 'provider':
                proof = _wechat_code2session_proof(code.strip())
                openid, unionid = proof['openid'], proof['unionid']
            else:
                openid, unionid = _wechat_code2session(code.strip()), ''
            phone = _wechat_get_phone(phone_code.strip()) if phone_code.strip() else ''
            with transaction.atomic():
                session = session_for(candidate.ticket, lock=True)
                ensure_active(session)
                if session.confirmed_account_id:
                    return fail('该请求已确认，请返回网页')
                if session.wechat_openid and session.wechat_openid != openid:
                    return fail('当前微信身份与之前授权不一致，请重新扫码', 409, 'ACCOUNT_CONFLICT')
                if session.wechat_unionid and unionid and session.wechat_unionid != unionid:
                    return fail('当前微信身份信息不一致，请重新扫码', 409, 'ACCOUNT_CONFLICT')
                if session.expected_account_id:
                    if session.purpose == 'bind_wechat':
                        if session.expected_account.openid and session.expected_account.openid != openid:
                            return fail('当前账号已绑定其他微信，不能切换账号', 409, 'ACCOUNT_CONFLICT')
                    elif session.expected_account.openid != openid:
                        return fail('请使用当前账号绑定的微信扫码，不能切换账号', 409, 'ACCOUNT_CONFLICT')
                account = ClubAccount.objects.filter(openid=openid).first()
                if account:
                    ensure_account_login(account, session.audience)
                elif session.audience == 'provider' and session.purpose == 'login':
                    return fail('当前微信尚未绑定陪玩账号，请先使用账号密码登录后绑定，或联系门店管理员', 403, 'WECHAT_ACCOUNT_UNBOUND')
                # An already bound WeChat is sufficient for a login.  Phone
                # changes use bind_phone, not an incidental login phone grant.
                if session.purpose == 'login' and account and account.is_openid_bound:
                    phone = ''
                    if session.phone:
                        session.phone = ''
                        session.sms_hash = ''
                        session.sms_attempts = 0
                        session.sms_expires_at = None
                if session.wechat_openid != openid or (phone and session.phone != phone):
                    session.sms_hash = ''
                    session.sms_attempts = 0
                    session.sms_expires_at = None
                session.wechat_openid = openid
                session.wechat_unionid = unionid or session.wechat_unionid
                bridge_token = secrets.token_urlsafe(32)
                session.bridge_token_hash = hashlib.sha256(bridge_token.encode()).hexdigest()
                if phone:
                    session.phone = phone
                session.save()
                bound_login = bool(session.purpose == 'login' and account and account.is_openid_bound)
                binding_wechat = bool(session.purpose == 'bind_wechat' and session.expected_account_id)
                requires_phone = session.purpose == 'bind_phone' or not (bound_login or binding_wechat)
                requires_sms = bool(session.phone)
                confirmed_candidate = session.expected_account if session.purpose == 'bind_wechat' else account
                store = current_store()
                return Response({'code': 0, 'data': {
                    'store': {'slug': store.slug, 'name': store.name} if store is not None else None,
                    'bridge_token': bridge_token,
                    'requiresPhoneAuthorization': requires_phone and not session.phone,
                    'requiresSms': requires_sms,
                    'maskedPhone': session.phone[:3] + '****' + session.phone[-4:] if session.phone else '',
                    'canConfirm': bool(confirmed_candidate and (bound_login or binding_wechat) and not requires_sms),
                }})
        except WechatLoginError as error:
            return fail(str(error), error.code, error.error_code)
        except (OSError, ValueError):
            return fail('微信授权暂时无法完成，请稍后重试', 503, 'WECHAT_UNAVAILABLE')


class WebLoginSmsView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = 'login'

    def post(self, request):
        try:
            with transaction.atomic():
                session = session_for(request.data.get('ticket'), lock=True)
                ensure_active(session)
                ensure_bridge(session, request)
                if not session.wechat_openid or not session.phone or session.confirmed_account_id:
                    return fail('请先授权读取微信手机号')
                now = timezone.now()
                if session.sms_sent_at and now < session.sms_sent_at + timedelta(seconds=60):
                    seconds = max(1, int((session.sms_sent_at + timedelta(seconds=60) - now).total_seconds()))
                    return Response({'code': 429, 'msg': '验证码已发送，请稍后再试', 'retry_after': seconds}, status=429)
                code = f'{secrets.randbelow(1000000):06d}'
                session.sms_hash = sms_digest(session, code)
                session.sms_sent_at = now
                session.sms_expires_at = now + timedelta(minutes=5)
                session.sms_attempts = 0
                session.save()
            send_verification_code(session.phone, code)
            return Response({'code': 0, 'data': {'retry_after': 60, 'expires_in': 300}})
        except WechatLoginError as error:
            return fail(str(error), error.code, error.error_code)
        except SmsUnavailable as error:
            return fail(str(error), 503, 'SMS_UNAVAILABLE')


class WebLoginConfirmView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = 'login'

    def post(self, request):
        if is_admin_handoff(request.data.get('ticket')):
            from console.dual_auth import ConsoleWechatConfirmView
            return ConsoleWechatConfirmView().post(request)
        try:
            with transaction.atomic():
                session = session_for(request.data.get('ticket'), lock=True)
                ensure_active(session)
                ensure_bridge(session, request)
                if session.confirmed_account_id:
                    return Response({'code': 0, 'data': {'status': 'CONFIRMED'}})
                if not session.wechat_openid:
                    return fail('请先使用微信授权登录')
                account = ClubAccount.objects.filter(openid=session.wechat_openid).first()
                if account:
                    ensure_account_login(account, session.audience)
                if session.audience == 'provider' and session.purpose == 'login' and not account:
                    return fail('当前微信尚未绑定陪玩账号，请先使用账号密码登录后绑定', 403, 'WECHAT_ACCOUNT_UNBOUND')
                requires_sms = bool(session.phone)
                bound_login = bool(session.purpose == 'login' and account and account.is_openid_bound)
                binding_wechat = bool(session.purpose == 'bind_wechat' and session.expected_account_id)
                if not requires_sms and not (bound_login or binding_wechat):
                    return fail('请先授权微信手机号并完成短信验证')
                if requires_sms:
                    if not session.sms_hash or not session.sms_expires_at or session.sms_expires_at <= timezone.now() or session.sms_attempts >= 5:
                        return fail('验证码已失效，请重新获取并验证', 400, 'SMS_CODE_EXPIRED')
                    session.sms_attempts += 1
                    session.save(update_fields=['sms_attempts'])
                    code = request.data.get('smsCode', '')
                    if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit() or not hmac.compare_digest(session.sms_hash, sms_digest(session, code)):
                        return fail('验证码错误，请重新输入', 400, 'SMS_CODE_INVALID')
                if session.expected_account_id and session.purpose != 'bind_wechat' and (not account or account.pk != session.expected_account_id):
                    return fail('授权账号与当前网页账号不一致', 409, 'ACCOUNT_CONFLICT')
                _lock_wechat_phone(session.phone)
                if session.purpose == 'bind_wechat':
                    if account and account.pk != session.expected_account_id:
                        return fail('当前微信已绑定其他账号，请联系门店管理员', 409, 'ACCOUNT_CONFLICT')
                    account = ClubAccount.objects.select_for_update().get(pk=session.expected_account_id)
                    ensure_account_login(account, session.audience)
                    if account.openid and account.openid != session.wechat_openid:
                        return fail('当前账号已绑定其他微信，请联系门店管理员', 409, 'ACCOUNT_CONFLICT')
                    if (account.account_type == ClubAccount.AccountType.PROVIDER and account.openid
                            and account.wechat_appid and account.wechat_appid != settings.WECHAT_MINIAPP_APPID):
                        return fail('当前微信渠道身份尚无法核验，请联系门店管理员', 409, 'ACCOUNT_CONFLICT')
                    if account.account_type == ClubAccount.AccountType.PROVIDER:
                        from .wechat_identity import bind_mini_provider
                        try:
                            bind_mini_provider(account, appid=settings.WECHAT_MINIAPP_APPID,
                                openid=session.wechat_openid, unionid=session.wechat_unionid)
                        except ValueError as error:
                            return fail(str(error), 409, 'ACCOUNT_CONFLICT')
                    account.openid = session.wechat_openid
                    account.wechat_appid = settings.WECHAT_MINIAPP_APPID
                    account.is_openid_bound = True
                    if session.phone:
                        account.phone = session.phone
                        account.is_phone_verified = True
                    account.save(update_fields=['openid', 'wechat_appid', 'is_openid_bound', 'phone', 'is_phone_verified', 'updated_at'])
                else:
                    if session.audience == 'provider':
                        ensure_account_login(account, session.audience)
                    else:
                        defaults = {'username': 'wx_' + uuid.uuid4().hex, 'account_type': ClubAccount.AccountType.BOSS,
                            'nickname': '微信用户', 'phone': session.phone or None,
                            'is_phone_verified': bool(session.phone), 'is_openid_bound': True}
                        account, _, _ = WechatLoginView._resolve_customer(session.wechat_openid, session.phone, defaults, bool(session.phone))
                        _ensure_boss_login_allowed(account)
                        if session.phone:
                            _assert_phone_available(account, session.phone)
                    if session.phone:
                        account.phone = session.phone
                        account.is_phone_verified = True
                    account.wechat_appid = settings.WECHAT_MINIAPP_APPID
                    account.is_openid_bound = True
                    account.save(update_fields=['phone', 'is_phone_verified', 'wechat_appid', 'is_openid_bound', 'updated_at'])
                legacy = _legacy_user_for_account(account)
                if legacy and session.phone:
                    legacy.phone, legacy.is_phone_verified = session.phone, True
                    legacy.save(update_fields=['phone', 'is_phone_verified'])
                if legacy and session.purpose == 'bind_wechat':
                    legacy.openid = account.openid
                    legacy.is_openid_bound = True
                    legacy.save(update_fields=['openid', 'is_openid_bound'])
                session.confirmed_account = account
                session.confirmed_at = timezone.now()
                session.sms_hash = ''
                session.save(update_fields=['confirmed_account', 'confirmed_at', 'sms_hash'])
                return Response({'code': 0, 'data': {'status': 'CONFIRMED'}})
        except WechatLoginError as error:
            return fail(str(error), error.code, error.error_code)
        except IntegrityError:
            return fail('当前微信已绑定其他账号，请联系门店管理员', 409, 'ACCOUNT_CONFLICT')
