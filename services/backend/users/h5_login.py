"""WeChat Official Account H5 OAuth with channel-specific unified identities."""
import hashlib
import hmac
import json
import secrets
import urllib.parse
import urllib.request
import uuid
from datetime import timedelta
from io import BytesIO

from django.core import signing
from django.db import DatabaseError, IntegrityError, transaction as django_transaction
from django.http import HttpResponse
from django.utils import timezone
from django.utils.html import escape
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.license_quota import LicenseClosureRestricted, LicenseClosureUnavailable
from club_accounts.models import ClubAccount, LegacyAccountMap, WechatIdentity
from club_accounts.tokens import issue_account_tokens
from common.catalog_images import bounded_public_image_url
from stores.closure import closure_refusal_response
from stores.conf import settings
from stores.context import current_store, store_context
from stores.db import transaction
from stores.models import AdminLoginHandoff, Store

from .models import WebLoginSession
from .views import _wechat_http_get


STATE_SALT = 'xa.wechat-h5.login.v1'
BIND_SALT = 'xa.console.wechat-bind.v1'
H5_CHANNEL = 'OFFICIAL_H5'


def fail(message, status=400, code='WECHAT_H5_INVALID'):
    return Response({'code': status, 'errorCode': code, 'msg': message}, status=status)


class H5ClosureRefusal(RuntimeError):
    """Keep closure HTTP semantics out of the ordinary OAuth error-page fallback."""

    def __init__(self, response):
        super().__init__(response.data.get('msg', '本店闭店状态不允许新建账号'))
        self.response = response


def configured():
    return bool(settings.WECHAT_H5_APPID and settings.WECHAT_H5_SECRET)


def allowed_origin(value, audience):
    value = str(value or '').rstrip('/')
    parts = urllib.parse.urlsplit(value)
    if parts.scheme not in ('http', 'https') or parts.path or parts.query or parts.fragment or not parts.hostname:
        raise ValueError('微信授权返回地址无效')
    allowed = set(getattr(settings, 'WECHAT_H5_ALLOWED_ORIGINS', []))
    try:
        with django_transaction.atomic(using='default'):
            stores = list(Store.objects.filter(status=Store.Status.ACTIVE, initialized_at__isnull=False))
    except DatabaseError:
        # The host middleware has already resolved this tenant from the
        # directory or its signed routing snapshot. Never use a request hint
        # or a different store's configured origin during an outage.
        store = current_store()
        if store is None or audience not in ('boss', 'provider'):
            raise
        allowed = set()
        stores = [store]
    for store in stores:
        if audience == 'admin':
            allowed.add(store.admin_url.rstrip('/'))
        elif audience == 'provider':
            allowed.add(store.provider_url.rstrip('/'))
        else:
            allowed.add(store.boss_url.rstrip('/'))
    if value not in allowed:
        raise ValueError('当前网页来源不在微信授权允许列表中')
    if parts.scheme != 'https' and not settings.DEBUG and parts.hostname not in ('127.0.0.1', 'localhost'):
        raise ValueError('微信授权返回地址必须使用 HTTPS')
    return value


def callback_url(request, audience):
    path = '/api/admin/auth/h5-login/callback' if audience == 'admin' else '/api/users/h5-login/callback/'
    configured_origin = getattr(settings, 'WECHAT_H5_API_ORIGIN', '').rstrip('/')
    return configured_origin + path if configured_origin else request.build_absolute_uri(path)


def make_state(ticket, audience, purpose, origin):
    store = current_store()
    return signing.dumps({'ticket': str(ticket), 'audience': audience, 'purpose': purpose,
        'store_slug': store.slug if store else '', 'return_origin': origin},
        salt=STATE_SALT, compress=True)


def authorization_url(request, session, audience, purpose, origin):
    state = make_state(session.ticket, audience, purpose, origin)
    query = urllib.parse.urlencode({'appid': settings.WECHAT_H5_APPID,
        'redirect_uri': callback_url(request, audience), 'response_type': 'code',
        'scope': 'snsapi_userinfo' if purpose == 'sync_avatar' else 'snsapi_base', 'state': state})
    return 'https://open.weixin.qq.com/connect/oauth2/authorize?' + query + '#wechat_redirect'


def start_payload(session, secret, request, audience, purpose, origin):
    store = current_store()
    return {'ticket': str(session.ticket), 'poll_token': secret,
        'authorization_url': authorization_url(request, session, audience, purpose, origin),
        'store': {'slug': store.slug, 'name': store.name} if store else None,
        'expires_in': 300, 'channel': 'wechat-h5'}


class H5LoginStartView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = 'login'

    def post(self, request):
        audience = request.data.get('audience', 'boss')
        purpose = request.data.get('purpose', 'login')
        if audience not in ('boss', 'provider') or purpose not in ('login', 'bind_wechat', 'sync_avatar'):
            return fail('微信登录用途无效')
        if purpose == 'sync_avatar' and not settings.WECHAT_H5_AVATAR_SYNC_ENABLED:
            return fail('当前环境未开放真实微信头像获取，请联系门店客服', 503, 'WECHAT_AVATAR_UNAVAILABLE')
        owner = None
        if purpose in ('bind_wechat', 'sync_avatar'):
            owner = getattr(request, 'account', None)
            expected = ClubAccount.AccountType.BOSS if audience == 'boss' else ClubAccount.AccountType.PROVIDER
            if not owner or owner.account_type != expected:
                return fail('请先使用账号密码和短信验证登录', 401)
            if purpose == 'sync_avatar' and (audience != 'provider' or not owner.is_openid_bound or not
                    WechatIdentity.objects.filter(account=owner, channel=H5_CHANNEL, appid=settings.WECHAT_H5_APPID).exists()):
                return fail('请先绑定当前公众号的本人微信，再获取头像')
        if not configured():
            return fail('微信 H5 登录尚未配置，请使用账号密码和短信验证登录', 503, 'WECHAT_H5_NOT_CONFIGURED')
        try:
            origin = allowed_origin(request.data.get('return_origin') or request.META.get('HTTP_ORIGIN'), audience)
        except ValueError as error:
            return fail(str(error))
        secret = secrets.token_urlsafe(32)
        session = WebLoginSession.objects.create(poll_token_hash=hashlib.sha256(secret.encode()).hexdigest(),
            audience=audience, purpose=purpose, expected_account=owner,
            expected_business_cycle=owner.business_cycle if owner else None,
            expires_at=timezone.now() + timedelta(minutes=5))
        return Response({'code': 0, 'data': start_payload(session, secret, request, audience, purpose, origin)})


class ConsoleH5StartView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = 'login'

    def post(self, request):
        from stores.admin_entry import _active_store
        from console.dual_auth import console_account

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
                account_id, store_slug = account.pk, store.slug
            except (signing.BadSignature, KeyError, TypeError, ValueError):
                return fail('微信初始化请求无效或已过期，请重新完成密码与短信验证', code='WECHAT_BIND_INVALID')
        elif purpose != 'login':
            return fail('微信登录用途无效')
        if not configured():
            return fail('微信 H5 登录尚未配置，请使用账号密码和短信验证登录', 503, 'WECHAT_H5_NOT_CONFIGURED')
        try:
            origin = allowed_origin(request.data.get('return_origin') or request.META.get('HTTP_ORIGIN'), 'admin')
        except ValueError as error:
            return fail(str(error))
        secret = secrets.token_urlsafe(32)
        with store_context(store):
            session = AdminLoginHandoff.objects.create(poll_token_hash=hashlib.sha256(secret.encode()).hexdigest(),
                purpose=purpose, store_slug=store_slug, expected_account_id=account_id,
                expires_at=timezone.now() + timedelta(minutes=5))
            return Response({'code': 0, 'data': start_payload(session, secret, request, 'admin', purpose, origin)})


def exchange_code(code):
    if not isinstance(code, str) or not code.strip() or len(code) > 256:
        raise ValueError('微信授权凭证无效，请重新授权')
    query = urllib.parse.urlencode({'appid': settings.WECHAT_H5_APPID,
        'secret': settings.WECHAT_H5_SECRET, 'code': code.strip(), 'grant_type': 'authorization_code'})
    data = _wechat_http_get('https://api.weixin.qq.com/sns/oauth2/access_token?' + query)
    if data.get('errcode') or not data.get('openid'):
        raise ValueError('微信授权已失效，请重新发起登录')
    return data


def _verify_wechat_avatar_image(url):
    """Fetch only the approved HTTPS CDN without redirects, and verify a bounded image."""
    from PIL import Image, UnidentifiedImageError

    class RejectRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ValueError('微信头像地址发生跳转，请重新获取')

    try:
        opener = urllib.request.build_opener(RejectRedirect())
        with opener.open(urllib.request.Request(url, headers={'Accept': 'image/*'}), timeout=8) as response:
            image_bytes = response.read(5 * 1024 * 1024 + 1)
        if not image_bytes or len(image_bytes) > 5 * 1024 * 1024:
            raise ValueError('微信头像大小无效')
        with Image.open(BytesIO(image_bytes)) as image:
            if image.format not in ('JPEG', 'PNG', 'WEBP'):
                raise ValueError('微信头像格式不支持')
            if image.width * image.height > 20_000_000:
                raise ValueError('微信头像尺寸过大')
            image.verify()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
        raise ValueError('无法读取微信头像，请稍后重试') from None


def verified_avatar_url(proof):
    """Read the authorized account's avatar from WeChat, never from client input."""
    openid, access_token = proof.get('openid'), proof.get('access_token')
    if not isinstance(openid, str) or not isinstance(access_token, str) or not openid or not access_token:
        raise ValueError('微信未提供头像授权，请重新授权')
    query = urllib.parse.urlencode({'access_token': access_token, 'openid': openid, 'lang': 'zh_CN'})
    info = _wechat_http_get('https://api.weixin.qq.com/sns/userinfo?' + query)
    if not isinstance(info, dict) or info.get('errcode') or info.get('openid') != openid:
        raise ValueError('微信头像身份校验失败，请重新授权')
    avatar = info.get('headimgurl')
    if not isinstance(avatar, str) or len(avatar) > ClubAccount._meta.get_field('avatar_url').max_length:
        raise ValueError('微信未返回可用头像，请稍后重试')
    try:
        parts = urllib.parse.urlsplit(avatar)
        if (parts.scheme not in ('http', 'https') or parts.hostname not in ('wx.qlogo.cn', 'thirdwx.qlogo.cn')
                or parts.username or parts.password or parts.port not in (None, 443) or not parts.path
                or parts.fragment or any(ord(c) < 32 for c in avatar)):
            raise ValueError
    except ValueError:
        raise ValueError('微信未返回可用头像，请稍后重试') from None
    # Some WeChat responses still contain an HTTP CDN URL; use the same
    # allowlisted host over TLS instead of presenting an insecure image.
    secure = urllib.parse.urlunsplit(('https', parts.netloc, parts.path, parts.query, ''))
    if len(secure) > ClubAccount._meta.get_field('avatar_url').max_length:
        raise ValueError('微信头像地址过长，请稍后重试')
    _verify_wechat_avatar_image(secure)
    return secure


def identity_for(account, proof):
    # The caller locks the account row before binding, serializing concurrent
    # authorization callbacks for the same account within this store.
    from .wechat_identity import clean_unionid, check_h5_provider_binding, claim_provider_unionid
    unionid = clean_unionid(proof.get('unionid'))
    if WechatIdentity.objects.filter(account=account, channel=H5_CHANNEL,
            appid=settings.WECHAT_H5_APPID).exclude(openid=proof['openid']).exists():
        raise ValueError('该账号已绑定其他微信，请联系门店管理员')
    identity = WechatIdentity.objects.select_for_update().filter(channel=H5_CHANNEL,
        appid=settings.WECHAT_H5_APPID, openid=proof['openid']).first()
    if identity and identity.account_id != account.pk:
        raise ValueError('当前微信已绑定其他账号，请联系门店管理员')
    if identity and identity.unionid and unionid and identity.unionid != unionid:
        raise ValueError('微信身份信息不一致，请联系门店管理员')
    if account.account_type == ClubAccount.AccountType.PROVIDER:
        check_h5_provider_binding(account, unionid or (identity.unionid if identity else ''))
        claim_provider_unionid(account, unionid or (identity.unionid if identity else ''))
    if identity is None:
        identity = WechatIdentity.objects.create(account=account, channel=H5_CHANNEL,
            appid=settings.WECHAT_H5_APPID, openid=proof['openid'], unionid=unionid)
    elif unionid and not identity.unionid:
        identity.unionid = unionid
        identity.save(update_fields=['unionid', 'updated_at'])
    if not account.is_openid_bound:
        account.is_openid_bound = True
        account.save(update_fields=['is_openid_bound', 'updated_at'])
    return identity


def new_h5_boss(proof):
    from django.contrib.auth import get_user_model
    from users.boss_types import default_boss_type
    from wallet.models import Wallet

    with transaction.atomic():
        boss_type = default_boss_type()
        if not boss_type.is_active:
            raise ValueError('默认老板类型暂不可用，请联系门店客服')
        # Existing identities never enter this function. Recheck immediately
        # before the account write, after any default-type or lock wait.
        denied = closure_refusal_response(current_store())
        if denied is not None:
            if denied.status_code == 409:
                denied.data['msg'] = '本店处于闭店公示或待结清阶段，暂不接受微信新老板注册'
            raise H5ClosureRefusal(denied)
        account = ClubAccount.objects.create_account(username='wx_' + uuid.uuid4().hex,
            password=None, account_type=ClubAccount.AccountType.BOSS, nickname='微信用户',
            boss_type=boss_type, is_openid_bound=True)
        user = get_user_model().objects.create_user(username=account.username, password=None,
            role='CUSTOMER', nickname=account.nickname, boss_type=boss_type)
        LegacyAccountMap.objects.create(legacy_user_id=user.id, account=account, legacy_role='CUSTOMER')
        Wallet.objects.filter(user=user).update(account=account)
        return identity_for(account, proof)


def callback_page(origin, success, message):
    payload = json.dumps({'type': 'xa-wechat-h5', 'status': 'CONFIRMED' if success else 'ERROR'}, ensure_ascii=False)
    origin_json = json.dumps(origin, ensure_ascii=False)
    title = '微信授权已完成' if success else '微信授权未完成'
    detail = '请返回原页面继续操作，本页可以关闭。' if success else message
    html = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title></head><body style="font-family:sans-serif;max-width:32rem;margin:15vh auto;padding:1.5rem;line-height:1.7"><h1>{title}</h1><p>{escape(detail)}</p><a id="continue" href="{escape(origin)}">返回原页面</a><script>(function(){{var origin={origin_json};if(window.opener){{window.opener.postMessage({payload},origin);window.close();}}document.getElementById('continue').href=origin;}})();</script></body></html>'''
    response = HttpResponse(html, content_type='text/html; charset=utf-8')
    response['Cache-Control'] = 'no-store'
    return response


def state_from(request):
    try:
        state = signing.loads(request.GET.get('state', ''), salt=STATE_SALT, max_age=600)
        if not isinstance(state, dict) or not state.get('return_origin'):
            raise ValueError
        return state
    except (signing.BadSignature, ValueError, TypeError) as exc:
        raise ValueError('微信授权请求无效或已过期，请重新发起') from exc


class H5LoginCallbackView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = 'login'

    @transaction.atomic
    def get(self, request):
        origin = ''
        try:
            state = state_from(request)
            origin = state['return_origin']
            audience = state['audience']
            if audience not in ('boss', 'provider') or state.get('purpose') not in ('login', 'bind_wechat', 'sync_avatar'):
                raise ValueError('微信授权用途无效')
            if state.get('purpose') == 'sync_avatar' and not settings.WECHAT_H5_AVATAR_SYNC_ENABLED:
                raise ValueError('当前环境未开放真实微信头像获取')
            session = WebLoginSession.objects.select_for_update().get(ticket=state['ticket'], audience=audience)
            if session.purpose != state.get('purpose') or session.expires_at <= timezone.now() or session.confirmed_account_id:
                raise ValueError('微信授权请求已失效，请重新发起')
            proof = exchange_code(request.GET.get('code'))
            with transaction.atomic():
                if state['purpose'] == 'sync_avatar':
                    if audience != 'provider':
                        raise ValueError('微信授权用途无效')
                    account = ClubAccount.objects.select_for_update().get(pk=session.expected_account_id,
                        account_type=ClubAccount.AccountType.PROVIDER, is_active=True, can_login=True, is_openid_bound=True)
                    if (session.expected_business_cycle != account.business_cycle
                            or not account.license_membership_active):
                        raise ValueError('账号业务周期已变化，请重新发起微信授权')
                    if not WechatIdentity.objects.filter(account=account, channel=H5_CHANNEL,
                            appid=settings.WECHAT_H5_APPID, openid=proof['openid']).exists():
                        raise ValueError('请使用已绑定的本人微信获取头像')
                    avatar = verified_avatar_url(proof)
                    from .models import EscortProfile
                    profile = EscortProfile.objects.select_for_update().filter(account=account).first()
                    if not profile:
                        raise ValueError('陪玩资料尚未创建，请联系门店客服')
                    account.avatar_url = avatar
                    account.save(update_fields=['avatar_url', 'updated_at'])
                    profile.avatar = ''
                    profile.wechat_avatar_verified_at = profile.wechat_avatar_verified_at or timezone.now()
                    profile.avatar_source = 'WECHAT'
                    profile.save(update_fields=['avatar', 'wechat_avatar_verified_at', 'avatar_source', 'updated_at'])
                    from django.contrib.auth import get_user_model
                    get_user_model().objects.filter(pk=profile.user_id).update(avatar_url=avatar)
                elif state['purpose'] == 'bind_wechat':
                    account = ClubAccount.objects.select_for_update().get(pk=session.expected_account_id)
                    expected = ClubAccount.AccountType.BOSS if audience == 'boss' else ClubAccount.AccountType.PROVIDER
                    if (account.account_type != expected or not account.is_active or not account.can_login
                            or not account.license_membership_active
                            or session.expected_business_cycle != account.business_cycle):
                        raise ValueError('账号状态或业务周期已变化，请重新发起微信授权')
                    identity = identity_for(account, proof)
                else:
                    identity = WechatIdentity.objects.select_for_update().select_related('account').filter(
                        channel=H5_CHANNEL, appid=settings.WECHAT_H5_APPID, openid=proof['openid']).first()
                    if identity is None:
                        if audience != 'boss':
                            raise ValueError('当前微信尚未绑定陪玩账号，请先使用账号密码和短信登录后绑定')
                        identity = new_h5_boss(proof)
                    account = identity.account
                    expected = ClubAccount.AccountType.BOSS if audience == 'boss' else ClubAccount.AccountType.PROVIDER
                    if account.account_type != expected or not account.is_active or not account.can_login:
                        raise ValueError('当前微信所属账号不可从此端登录')
                session.wechat_openid = proof['openid']
                session.confirmed_account = account
                session.confirmed_at = timezone.now()
                session.save(update_fields=['wechat_openid', 'confirmed_account', 'confirmed_at'])
            return callback_page(origin, True, '')
        except H5ClosureRefusal as error:
            return error.response
        except (ValueError, TypeError, WebLoginSession.DoesNotExist, ClubAccount.DoesNotExist, IntegrityError) as error:
            return callback_page(origin, False, str(error) or '微信授权暂不可用，请稍后重试')
        except (OSError, DatabaseError):
            return callback_page(origin, False, '微信授权暂不可用，请稍后重试')


def valid_poll(session, request):
    digest = hashlib.sha256(str(request.query_params.get('poll_token', '')).encode()).hexdigest()
    if not hmac.compare_digest(session.poll_token_hash, digest):
        raise ValueError('无法核对当前浏览器的登录请求，请重新授权')


class H5LoginStatusView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        try:
            ticket = uuid.UUID(request.query_params.get('ticket', ''))
        except ValueError:
            return fail('微信授权请求无效，请重新发起')
        try:
            session = WebLoginSession.objects.get(ticket=ticket)
            valid_poll(session, request)
            if session.expires_at <= timezone.now():
                return Response({'code': 0, 'data': {'status': 'EXPIRED'}})
            if not session.confirmed_account_id:
                return Response({'code': 0, 'data': {'status': 'PENDING'}})
            account = session.confirmed_account
            identity = WechatIdentity.objects.get(account=account, channel=H5_CHANNEL,
                appid=settings.WECHAT_H5_APPID, openid=session.wechat_openid)
            if session.purpose == 'sync_avatar':
                return Response({'code': 0, 'data': {'status': 'CONFIRMED', 'avatarSynced': True}})
            from identifiers.services import get_entity_number
            token, refresh = issue_account_tokens(account, auth_method='wechat', wechat_identity=identity)
            return Response({'code': 0, 'data': {'status': 'CONFIRMED', 'token': token, 'refreshToken': refresh,
                'userInfo': {'id': str(account.pk), 'entity_no': get_entity_number(account), 'username': account.username,
                    'nickname': account.nickname or account.username,
                    'role': 'customer' if session.audience == 'boss' else 'provider',
                    'avatar': bounded_public_image_url(account.avatar_url or ''),
                    'phone': account.phone or '', 'isWechatBound': True,
                    'isPhoneVerified': account.is_phone_verified}}})
        except (LicenseClosureRestricted, LicenseClosureUnavailable):
            # Do not turn a new-cycle closure refusal into the ordinary H5 400.
            raise
        except (ValueError, TypeError, WebLoginSession.DoesNotExist, WechatIdentity.DoesNotExist) as error:
            return fail(str(error) or '登录请求无效，请重新授权')


class ConsoleH5CallbackView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = 'login'

    @django_transaction.atomic(using='default')
    def get(self, request):
        from stores.admin_entry import _active_store
        from console.dual_auth import console_account
        from console.permissions import is_console_account

        origin = ''
        try:
            state = state_from(request)
            origin = state['return_origin']
            if state.get('audience') != 'admin' or state.get('purpose') not in ('login', 'bind_wechat'):
                raise ValueError('微信授权用途无效')
            session = AdminLoginHandoff.objects.select_for_update().get(ticket=state['ticket'])
            if session.expires_at <= timezone.now() or session.confirmed_account_id:
                raise ValueError('微信授权请求已失效，请重新发起')
            proof = exchange_code(request.GET.get('code'))
            if state['purpose'] == 'bind_wechat':
                store = _active_store(session.store_slug)
                with store_context(store), transaction.atomic():
                    account = ClubAccount.objects.select_for_update().get(pk=session.expected_account_id,
                        account_type=ClubAccount.AccountType.STAFF)
                    if not console_account(store, account.pk):
                        raise ValueError('后台账号不可绑定微信')
                    identity_for(account, proof)
            else:
                matches = []
                stores = Store.objects.filter(status__in=[Store.Status.ACTIVE, Store.Status.CONFIGURING], initialized_at__isnull=False,
                    database_alias__in=settings.DATABASES)
                for candidate in stores:
                    with store_context(candidate):
                        identity = WechatIdentity.objects.select_related('account').filter(channel=H5_CHANNEL,
                            appid=settings.WECHAT_H5_APPID, openid=proof['openid'],
                            account__account_type=ClubAccount.AccountType.STAFF,
                            account__is_active=True, account__can_login=True).first()
                        if identity and console_account(candidate, identity.account_id):
                            matches.append((candidate, identity.account))
                if not matches:
                    raise ValueError('当前微信尚未绑定后台账号，请使用账号密码和短信登录后完成初始化')
                if len(matches) != 1:
                    raise LookupError('当前微信关联多个门店，请使用“门店标识/账号”完成密码登录')
                store, account = matches[0]
                session.store_slug = store.slug
                session.expected_account_id = account.pk
            session.wechat_openid = proof['openid']
            session.confirmed_account_id = account.pk
            session.confirmed_at = timezone.now()
            session.save()
            return callback_page(origin, True, '')
        except LookupError as error:
            return callback_page(origin, False, str(error))
        except (ValueError, TypeError, AdminLoginHandoff.DoesNotExist, ClubAccount.DoesNotExist, IntegrityError) as error:
            return callback_page(origin, False, str(error) or '微信授权暂不可用，请稍后重试')
        except (OSError, DatabaseError):
            return callback_page(origin, False, '微信授权暂不可用，请稍后重试')


class ConsoleH5StatusView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        from stores.admin_entry import _active_store
        from console.auth import _profile_payload, _store_payload
        from console.dual_auth import console_account

        try:
            session = AdminLoginHandoff.objects.get(ticket=request.query_params.get('ticket'))
            valid_poll(session, request)
            if session.expires_at <= timezone.now():
                return Response({'code': 0, 'data': {'status': 'EXPIRED'}})
            if not session.confirmed_account_id:
                return Response({'code': 0, 'data': {'status': 'PENDING'}})
            store = _active_store(session.store_slug)
            account = console_account(store, session.confirmed_account_id)
            if not account:
                return fail('后台账号不可用，请联系门店管理员', 403, 'ACCOUNT_UNAVAILABLE')
            with store_context(store):
                identity = WechatIdentity.objects.get(account=account, channel=H5_CHANNEL,
                    appid=settings.WECHAT_H5_APPID, openid=session.wechat_openid)
                token, refresh = issue_account_tokens(account, auth_method='wechat', wechat_identity=identity)
                return Response({'code': 0, 'data': {'status': 'CONFIRMED', 'token': token,
                    'refresh': refresh, 'profile': _profile_payload(account), **_store_payload(store)}})
        except (ValueError, TypeError, AdminLoginHandoff.DoesNotExist, WechatIdentity.DoesNotExist) as error:
            return fail(str(error) or '登录请求无效，请重新授权')
