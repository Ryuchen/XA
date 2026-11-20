"""Neutral console origin: credentials/JWTs, never routing hints, choose a DB."""
import json

from django.conf import settings
from django.core import signing
from django.db import DatabaseError, InterfaceError
from django.http import JsonResponse
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from .context import StoreUnavailable, resolve_store, store_context
from .hosts import STORE_SLUG, request_store_slug


CHALLENGE_SALT = 'xa.neutral-admin.otp.v1'
LOGIN_PATH = '/api/admin/auth/login'
PASSWORD_LOGIN_PATH = '/api/admin/auth/password-login'
INITIAL_OTP_PATH = '/api/admin/auth/initial-otp'
WECHAT_PREFIX = '/api/admin/auth/wechat/'
H5_LOGIN_PREFIX = '/api/admin/auth/h5-login/'
REFRESH_PATH = '/api/admin/auth/refresh'


class AdminLoginError(ValueError):
    def __init__(self, message, *, status=400, code='ADMIN_LOGIN_INVALID'):
        super().__init__(message)
        self.status, self.code = status, code


def _active_store(slug):
    if not isinstance(slug, str) or not STORE_SLUG.fullmatch(slug):
        raise ValueError('登录未绑定有效门店，请重新登录')
    from .models import Store
    store = resolve_store(slug, require_active=False, allow_cached=True)
    if store.initialized_at is None:
        raise StoreUnavailable('门店尚未完成初始化')
    if store.status not in (Store.Status.ACTIVE, Store.Status.CONFIGURING):
        raise StoreUnavailable('门店尚未开放或已停用')
    return store


def _account(store, username, password):
    from club_accounts.models import ClubAccount
    from club_accounts.services import authenticate_account
    from console.permissions import is_console_account
    with store_context(store):
        account = authenticate_account(username, password, account_type=ClubAccount.AccountType.STAFF)
        if not is_console_account(account):
            return None
        if store.status == store.Status.CONFIGURING:
            from .setup_access import is_setup_admin
            return account if is_setup_admin(store, account) else None
        return account


def _username(value):
    username = str(value or '').strip()
    if '/' not in username:
        return None, username
    slug, username = username.split('/', 1)
    if not STORE_SLUG.fullmatch(slug) or not username.strip():
        raise AdminLoginError('请使用“门店标识/账号”格式登录')
    return slug, username.strip()


def login_context(username, password, data):
    """Return only a uniquely authenticated, active console identity."""
    from .models import Store
    if any(key in data for key in ('store', 'store_slug', 'database_alias')):
        raise AdminLoginError('请通过账号识别门店，不要提交门店路由参数')
    prefix, username = _username(username)
    gate_data = data.copy()
    challenge = data.get('otpChallenge')
    try:
        if challenge:
            try:
                claim = signing.loads(challenge, salt=CHALLENGE_SALT, max_age=600)
                if (not isinstance(claim, dict) or claim.get('purpose') != 'console-login'
                        or type(claim.get('account_id')) is not int):
                    raise ValueError
                store = _active_store(claim['store_slug'])
                if prefix is not None and prefix != store.slug:
                    raise ValueError
                account = _account(store, username, password)
                if account is None or account.pk != claim['account_id'] or account.username != claim['username']:
                    raise ValueError
                gate_data['otpChallenge'] = claim['challenge']
                return store, account, gate_data
            except (signing.BadSignature, KeyError, TypeError, ValueError) as exc:
                raise AdminLoginError('验证请求无效或已过期，请重新输入账号密码', code='OTP_INVALID') from exc
        if prefix is not None:
            try:
                stores = [_active_store(prefix)]
            except ValueError as exc:
                raise AdminLoginError('账号或密码错误，或所属门店不可用') from exc
        else:
            stores = Store.objects.filter(status__in=[Store.Status.ACTIVE, Store.Status.CONFIGURING], initialized_at__isnull=False)
        matches = []
        for store in stores:
            from .dynamic_database import discover_store_connection
            try:
                available = discover_store_connection(store)
            except ValueError as exc:
                raise AdminLoginError('门店登录服务暂不可用，请稍后重试', status=503) from exc
            if not available:
                # An unavailable store may contain the same username; do not
                # silently select a different store as the unique match.
                raise AdminLoginError('门店登录服务暂不可用，请稍后重试', status=503)
            account = _account(store, username, password)
            if account is not None:
                matches.append((store, account))
        if not matches:
            raise AdminLoginError('账号或密码错误，或账号无后台访问权限')
        if len(matches) != 1:
            raise AdminLoginError('账号在多个门店存在，请使用“门店标识/账号”登录',
                                  status=409, code='ADMIN_STORE_AMBIGUOUS')
        store, account = matches[0]
        return store, account, gate_data
    except (DatabaseError, InterfaceError) as exc:
        # Never choose the healthy database when an unavailable database might
        # contain another matching identity.
        raise AdminLoginError('门店登录服务暂不可用，请稍后重试', status=503) from exc


def bind_challenge(challenge, store, account):
    return signing.dumps({'purpose': 'console-login', 'store_slug': store.slug,
        'account_id': account.pk, 'username': account.username, 'challenge': challenge},
        salt=CHALLENGE_SALT, compress=True)


def _json_body(request):
    if request.content_type == 'application/json':
        value = json.loads(request.body or b'{}')
        if not isinstance(value, dict):
            raise ValueError('请求格式不正确')
        return value
    return request.POST


def _verified_claims(raw, token_class=AccessToken):
    from club_accounts.tokens import ACCOUNT_TOKEN_KIND
    claims = token_class(raw)
    if claims.get('token_kind') != ACCOUNT_TOKEN_KIND or claims.get('account_type') != 'STAFF':
        raise TokenError('请使用后台账号登录')
    return claims


def neutral_admin_request(request, get_response):
    request.neutral_admin_entry = True
    path = request.path.rstrip('/')
    if request.path.startswith('/static/') or path == '/api/health':
        return get_response(request)
    if request.path.startswith('/media/platform/logos/'):
        if request.headers.get('X-Store-Slug') is not None or 'store' in request.GET:
            return JsonResponse({'code': 400, 'msg': '平台素材不接受门店路由参数'}, status=400)
        with store_context(None):
            return get_response(request)
    if request.path.startswith('/api/admin/platform/'):
        if request.headers.get('X-Store-Slug') is not None or 'store' in request.GET:
            return JsonResponse({'code': 400, 'msg': '平台身份不接受门店路由参数'}, status=400)
        request.store = None
        with store_context(None):
            return get_response(request)
    if path in (LOGIN_PATH, PASSWORD_LOGIN_PATH, INITIAL_OTP_PATH):
        if request.headers.get('X-Store-Slug') is not None or 'store' in request.GET:
            return JsonResponse({'code': 400, 'msg': '登录由账号识别门店，请移除门店路由参数'}, status=400)
        request.store = None
        with store_context(None):
            return get_response(request)
    if path.startswith((WECHAT_PREFIX, H5_LOGIN_PREFIX)):
        if request.headers.get('X-Store-Slug') is not None or 'store' in request.GET:
            return JsonResponse({'code': 400, 'msg': '微信登录由绑定账号识别门店，请移除门店路由参数'}, status=400)
        try:
            from .media import decode_login_scene
            from .models import AdminLoginHandoff, Store
            data = _json_body(request) if request.method == 'POST' else request.GET
            ticket = data.get('ticket')
            store = None
            if data.get('state'):
                claim = signing.loads(data['state'], salt='xa.wechat-h5.login.v1', max_age=600)
                if claim.get('store_slug'):
                    store = _active_store(claim['store_slug'])
            elif ticket:
                store_id, ticket_id = decode_login_scene(ticket)
                if store_id is not None:
                    store = _active_store(Store.objects.get(pk=store_id).slug)
                else:
                    handoff = AdminLoginHandoff.objects.filter(ticket=ticket_id).first()
                    if handoff and handoff.store_slug:
                        store = _active_store(handoff.store_slug)
            elif data.get('bindToken'):
                claim = signing.loads(data['bindToken'], salt='xa.console.wechat-bind.v1', max_age=900)
                store = _active_store(claim['store_slug'])
            request.store = store
            with store_context(store):
                response = get_response(request)
                if store is not None:
                    response['X-Store-Slug'] = store.slug
                return response
        except (signing.BadSignature, KeyError, TypeError, ValueError, UnicodeError, Store.DoesNotExist):
            return JsonResponse({'code': 400, 'msg': '微信登录请求无效或已过期，请重新发起'}, status=400)
        except DatabaseError:
            return JsonResponse({'code': 503, 'msg': '微信登录服务暂不可用，请稍后重试'}, status=503)
    try:
        if path == REFRESH_PATH:
            claims = _verified_claims(_json_body(request).get('refresh'), RefreshToken)
        elif request.path.startswith('/api/media/private/'):
            # Images cannot attach Bearer headers. Their existing signed file
            # capability is the only alternative, and authorizes no API action.
            from common.private_media import SALT, MAX_AGE
            ticket = request.path.rstrip('/').rsplit('/', 1)[-1]
            grant = signing.loads(ticket, salt=SALT, max_age=MAX_AGE)
            claims = {'store_slug': grant['store_slug'], 'account_id': grant['account']}
        else:
            authorization = request.headers.get('Authorization', '').split()
            if len(authorization) != 2 or authorization[0].lower() != 'bearer':
                raise TokenError('请先登录后台')
            claims = _verified_claims(authorization[1])
        store = _active_store(claims.get('store_slug'))
        hint = request_store_slug(request.get_host(), request.headers.get('X-Store-Slug'), request.GET.getlist('store'))
        if hint is not None and hint != store.slug:
            return JsonResponse({'code': 400, 'msg': '请求门店与登录账号不一致，请重新登录'}, status=400)
        with store_context(store):
            if store.status == store.Status.CONFIGURING:
                from .setup_access import is_setup_admin, setup_request_allowed
                from club_accounts.tokens import account_for_claims
                if request.path.startswith('/api/media/private/') or not is_setup_admin(store, account_for_claims(claims)):
                    raise TokenError('待配置门店只允许首位管理员登录')
                if not setup_request_allowed(path, request.method):
                    return JsonResponse({'code': 403, 'errorCode': 'STORE_SETUP_ONLY',
                                         'msg': '门店尚未营业，目前仅开放初始化配置'}, status=403)
            if path != REFRESH_PATH:
                from club_accounts.models import ClubAccount
                from club_accounts.tokens import account_for_claims
                from console.permissions import is_console_account
                if request.path.startswith('/api/media/private/'):
                    account = ClubAccount.objects.filter(pk=claims['account_id'], account_type='STAFF').first()
                else:
                    account = account_for_claims(claims)
                if not is_console_account(account):
                    raise TokenError('后台访问权限已停用，请重新登录')
            request.store = store
            if (getattr(settings, 'STORE_FIRST_OPEN_ENABLED', False)
                    and store.status == store.Status.CONFIGURING
                    and request.method not in ('GET', 'HEAD', 'OPTIONS')
                    and path != '/api/admin/license/activate'):
                # The activation endpoint already acquires registration then
                # store locks; taking them in the opposite order can deadlock.
                # Other setup writes serialize with first opening here.
                from django.db import transaction
                from .models import Store
                with transaction.atomic(using='default'):
                    locked = Store.objects.select_for_update().filter(pk=store.pk).first()
                    if not locked or locked.status not in (Store.Status.CONFIGURING, Store.Status.ACTIVE):
                        return JsonResponse({'code': 409, 'msg': '门店状态已变化，请重新登录'}, status=409)
                    request.store = locked
                    with store_context(locked):
                        response = get_response(request)
            else:
                response = get_response(request)
            response['X-Store-Slug'] = store.slug
            return response
    except (TokenError, signing.BadSignature, KeyError, TypeError, ValueError, UnicodeError):
        return JsonResponse({'code': 401, 'msg': '后台登录无效或未绑定所属门店，请重新登录'}, status=401)
    except DatabaseError:
        return JsonResponse({'code': 503, 'errorCode': 'STORE_DIRECTORY_UNAVAILABLE',
                             'msg': '门店服务暂不可用，请稍后重试'}, status=503)
