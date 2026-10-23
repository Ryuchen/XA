import calendar
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, time, timedelta

from PIL import Image, UnidentifiedImageError
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Case, Count, ExpressionWrapper, F, FloatField, IntegerField, OuterRef, Q, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError

from club_accounts.models import ClubAccount, LegacyAccountMap
from club_accounts.permissions import IsClubAccountAuthenticated
from club_accounts.services import (
    authenticate_account,
    get_or_create_account_for_legacy_user,
)
from club_accounts.tokens import issue_account_tokens, refresh_account_access_token
from orders.models import GameCategory, Order, ServiceItem
from orders.catalog import skill_service_q, visible_service_q
from site_messages.utils import create_message
from wallet.models import (
    Transaction,
    Wallet,
    get_pass_daily_price,
    get_pass_daily_prices,
    get_pass_delay_seconds,
    get_platform_wallet,
)
from wallet.services import get_wallet, claim_fund_write, peek_fund_write
from wallet.receipts import fund_success, recover_fund_result
from common.logging_utils import log_money_event
from common.media import build_media_url
from common.catalog_images import bounded_public_image_url

from .models import (
    Achievement, CheckinGift, CheckinMonthProgress, CheckinRecord, CheckinRuleConfig,
    CustomerGameProfile, EscortProfile, EscortSchedule, ProviderPassPurchase,
    WechatPhoneLock,
)

User = get_user_model()

ROLE_REVERSE_MAP = {
    ClubAccount.AccountType.BOSS: 'customer',
    ClubAccount.AccountType.PROVIDER: 'provider',
    ClubAccount.AccountType.STAFF: 'support',
}

WECHAT_API_TIMEOUT = 5


def _validate_avatar(avatar):
    if avatar.size > 5 * 1024 * 1024:
        raise ValueError('头像不能超过 5MB')
    try:
        picture = Image.open(avatar)
        extension = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}.get(picture.format)
        if not extension or picture.width * picture.height > 20_000_000:
            raise ValueError('请使用不超过 2000 万像素的 JPG、PNG 或 WebP 头像')
        picture.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('头像图片内容无效') from exc
    avatar.seek(0)
    avatar.name = f'{uuid.uuid4().hex}.{extension}'


class WechatLoginError(ValueError):
    def __init__(self, message, error_code='WECHAT_UNAVAILABLE', code=400):
        super().__init__(message)
        self.error_code = error_code
        self.code = code


def _wechat_error_response(exc):
    return Response({
        'code': getattr(exc, 'code', 400),
        'errorCode': getattr(exc, 'error_code', 'WECHAT_UNAVAILABLE'),
        'msg': str(exc) if isinstance(exc, WechatLoginError) else '微信服务暂不可用，请稍后重试',
    })


def _wechat_binding_info(account):
    wechat_bound = bool(account.openid and account.is_openid_bound)
    phone_verified = bool(account.phone and account.is_phone_verified)
    return {
        'isWechatBound': wechat_bound,
        'isPhoneVerified': phone_verified,
        'bindingState': 'PHONE_BOUND' if wechat_bound and phone_verified else (
            'WECHAT_BOUND' if wechat_bound else 'UNBOUND'
        ),
    }


def _ensure_boss_login_allowed(account):
    if account is None or account.account_type != ClubAccount.AccountType.BOSS:
        raise WechatLoginError('该微信号非老板账号，请使用对应端登录', 'ACCOUNT_ROLE_MISMATCH', 403)
    if not account.is_active or not account.can_login:
        raise WechatLoginError('该老板账号已被禁用，请联系客服', 'ACCOUNT_DISABLED', 403)


def _legacy_user_for_account(account):
    mapping = getattr(account, 'legacy_mapping', None)
    return User.objects.filter(pk=mapping.legacy_user_id).first() if mapping else None


def _assert_phone_available(account, phone):
    """A verified phone may not silently merge or replace another boss account."""
    mapping = getattr(account, 'legacy_mapping', None)
    accounts = ClubAccount.objects.filter(account_type=ClubAccount.AccountType.BOSS, phone=phone)
    legacy_users = User.objects.filter(role=User.Role.CUSTOMER, phone=phone)
    if account.pk:
        accounts = accounts.exclude(pk=account.pk)
    if mapping:
        legacy_users = legacy_users.exclude(pk=mapping.legacy_user_id)
    if accounts.exists() or legacy_users.exists():
        raise WechatLoginError('该手机号已关联其他老板账号，请联系客服处理', 'ACCOUNT_CONFLICT', 409)


def _lock_wechat_phone(phone):
    # Account rows cannot lock an as-yet-unowned phone. Both entry points must
    # acquire this stable row BEFORE account locks and hold it until commit.
    if phone:
        WechatPhoneLock.objects.get_or_create(phone=phone)
        WechatPhoneLock.objects.select_for_update().get(phone=phone)


def _wechat_http_get(url):
    with urllib.request.urlopen(url, timeout=WECHAT_API_TIMEOUT) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _wechat_code2session(code):
    """用登录 code 换取 openid。失败抛出 ValueError。"""
    params = urllib.parse.urlencode({
        'appid': settings.WECHAT_MINIAPP_APPID,
        'secret': settings.WECHAT_MINIAPP_SECRET,
        'js_code': code,
        'grant_type': 'authorization_code',
    })
    data = _wechat_http_get(f'https://api.weixin.qq.com/sns/jscode2session?{params}')
    if data.get('errcode'):
        if str(data['errcode']) in ('40029', '40163'):
            raise WechatLoginError('微信登录凭证已失效，请重新登录', 'WECHAT_CODE_INVALID')
        raise WechatLoginError('微信登录服务暂不可用，请稍后重试')
    openid = data.get('openid')
    if not openid:
        raise WechatLoginError('微信未返回有效身份，请重新登录')
    return openid


def _wechat_get_access_token():
    params = urllib.parse.urlencode({
        'grant_type': 'client_credential',
        'appid': settings.WECHAT_MINIAPP_APPID,
        'secret': settings.WECHAT_MINIAPP_SECRET,
    })
    data = _wechat_http_get(f'https://api.weixin.qq.com/cgi-bin/token?{params}')
    token = data.get('access_token')
    if not token:
        raise WechatLoginError('微信手机号服务暂不可用，请稍后重试')
    return token


def _wechat_get_phone(phone_code):
    """用手机号授权 code 换取真实手机号。失败抛出 ValueError。"""
    access_token = _wechat_get_access_token()
    url = f'https://api.weixin.qq.com/wxa/business/getuserphonenumber?access_token={access_token}'
    body = json.dumps({'code': phone_code}).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=body,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=WECHAT_API_TIMEOUT) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    if data.get('errcode'):
        if str(data['errcode']) in ('40029', '40163'):
            raise WechatLoginError('手机号授权已失效，请重新授权', 'WECHAT_PHONE_CODE_INVALID')
        raise WechatLoginError('微信手机号服务暂不可用，请稍后重试')
    phone_info = data.get('phone_info') or {}
    watermark = phone_info.get('watermark') or {}
    if watermark.get('appid') and watermark['appid'] != settings.WECHAT_MINIAPP_APPID:
        raise WechatLoginError('手机号授权身份不匹配', 'WECHAT_PHONE_CODE_INVALID')
    phone = phone_info.get('purePhoneNumber', '')
    if not isinstance(phone, str) or not phone.isdigit() or not 5 <= len(phone) <= 20:
        raise WechatLoginError('未获取到有效手机号，请重新授权', 'WECHAT_PHONE_CODE_INVALID')
    return phone


class WechatLoginView(APIView):
    """老板端微信快捷登录：仅服务 CUSTOMER 角色。

    陪玩改走 AccountLoginView（账号密码），客服走后台 /api/admin/auth/login，
    故此端点不再接收 role / bindCode，openid 命中的用户必须是老板。
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    # 未鉴权入口：必须限流，否则是免费的枚举/刷号通道（仅作用于 POST）。
    throttle_scope = 'login'
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @staticmethod
    def _resolve_customer(openid, phone, defaults, allow_phone_binding, check_only=False):
        """按稳定微信身份解析老板，手机号仅用于真实微信登录的首次绑定。

        匹配顺序固定为：openid -> 唯一手机号老板 -> 新建。手机号冲突时不
        猜测归属，交由后台人工整理，避免把两个历史老板错误合并。
        """
        with transaction.atomic():
            account = ClubAccount.objects.select_for_update().filter(openid=openid).first()
            if account is not None:
                _ensure_boss_login_allowed(account)
                if phone:
                    _assert_phone_available(account, phone)
                return account, False, 'openid'

            legacy_openid_user = (
                User.objects.select_for_update()
                .filter(openid=openid)
                .first()
            )
            if legacy_openid_user is not None:
                account = get_or_create_account_for_legacy_user(legacy_openid_user)
                _ensure_boss_login_allowed(account)
                if phone:
                    _assert_phone_available(account, phone)
                return account, False, 'openid'

            if check_only:
                return None, False, 'unbound'

            if allow_phone_binding and phone:
                account_candidates = list(
                    ClubAccount.objects.select_for_update()
                    .filter(account_type=ClubAccount.AccountType.BOSS, phone=phone)
                    .order_by('id')[:2]
                )
                mapped_legacy_ids = set(
                    LegacyAccountMap.objects.filter(
                        account_id__in=[item.id for item in account_candidates],
                    ).values_list('legacy_user_id', flat=True)
                )
                legacy_candidates = list(
                    User.objects.select_for_update()
                    .filter(role=User.Role.CUSTOMER, phone=phone)
                    .exclude(id__in=mapped_legacy_ids)
                    .order_by('id')[:2]
                )
                if len(account_candidates) + len(legacy_candidates) > 1:
                    raise WechatLoginError('该手机号关联了多个老板账号，请联系客服处理', 'ACCOUNT_CONFLICT', 409)
                if account_candidates or legacy_candidates:
                    legacy_user = legacy_candidates[0] if legacy_candidates else None
                    account = (
                        account_candidates[0]
                        if account_candidates
                        else get_or_create_account_for_legacy_user(legacy_user)
                    )
                    _ensure_boss_login_allowed(account)
                    migrating_appid = bool(account.openid and account.openid != openid and account.wechat_appid
                        and account.is_phone_verified and account.wechat_appid != settings.WECHAT_MINIAPP_APPID and not settings.WECHAT_MOCK_LOGIN)
                    if account.openid and account.openid != openid and not migrating_appid:
                        raise WechatLoginError('该老板账号已绑定其他微信，请联系客服处理', 'ACCOUNT_CONFLICT', 409)
                    legacy_user = legacy_user or _legacy_user_for_account(account)
                    if legacy_user and legacy_user.openid and legacy_user.openid != openid and not (migrating_appid and legacy_user.openid == account.openid):
                        raise WechatLoginError('历史账号的微信绑定不一致，请联系客服处理', 'ACCOUNT_CONFLICT', 409)
                    if migrating_appid:
                        import hashlib
                        from club_accounts.models import WechatBindingChange
                        WechatBindingChange.objects.create(account=account,from_appid=account.wechat_appid,
                            to_appid=settings.WECHAT_MINIAPP_APPID,old_openid_hash=hashlib.sha256(account.openid.encode()).hexdigest(),
                            new_openid_hash=hashlib.sha256(openid.encode()).hexdigest())
                    account.wechat_appid = settings.WECHAT_MINIAPP_APPID if not settings.WECHAT_MOCK_LOGIN else ''
                    account.openid = openid
                    account.is_openid_bound = True
                    account.is_phone_verified = True
                    account.save(update_fields=[
                        'openid', 'wechat_appid', 'is_openid_bound', 'is_phone_verified', 'updated_at',
                    ])
                    if legacy_user:
                        legacy_user.openid = openid
                        legacy_user.is_openid_bound = True
                        legacy_user.is_phone_verified = True
                        legacy_user.save(update_fields=[
                            'openid', 'is_openid_bound', 'is_phone_verified',
                        ])
                    return account, False, 'phone'

            try:
                # The nested savepoint keeps a uniqueness race from breaking
                # the surrounding select-for-update transaction.
                with transaction.atomic():
                    from .boss_types import default_miniapp_boss_type
                    boss_type = default_miniapp_boss_type()
                    if not boss_type.is_active:
                        raise WechatLoginError('默认老板类型暂不可用，请联系客服', 'DEFAULT_BOSS_TYPE_UNAVAILABLE', 503)
                    account = ClubAccount.objects.create_account(
                        password=None,
                        openid=openid,
                        boss_type=boss_type,
                        **defaults,
                    )
                    legacy_user = User.objects.create_user(
                        username=account.username,
                        password=None,
                        openid=openid,
                        role=User.Role.CUSTOMER,
                        phone=account.phone,
                        nickname=account.nickname,
                        is_phone_verified=account.is_phone_verified,
                        is_openid_bound=True,
                        boss_type=boss_type,
                    )
                    LegacyAccountMap.objects.create(
                        legacy_user_id=legacy_user.id,
                        account=account,
                        legacy_role=User.Role.CUSTOMER,
                    )
                    Wallet.objects.filter(user=legacy_user).update(account=account)
                return account, True, 'created'
            except IntegrityError:
                # 唯一约束处理两个并发 code2session 请求的竞态。
                account = ClubAccount.objects.select_for_update().filter(openid=openid).first()
                if account is None:
                    raise WechatLoginError('账号绑定发生冲突，请重试或联系客服', 'ACCOUNT_CONFLICT', 409)
                _ensure_boss_login_allowed(account)
                if phone:
                    _assert_phone_available(account, phone)
                return account, False, 'openid'

    @transaction.atomic
    def post(self, request):
        code = request.data.get('code')
        phone_code = request.data.get('phoneCode') or ''
        nickname = request.data.get('nickname') or ''
        check_only = request.data.get('checkOnly', False)
        avatar_file = request.FILES.get('avatar')

        if not isinstance(code, str) or not code.strip() or len(code) > 256:
            return Response({'code': 400, 'errorCode': 'WECHAT_CODE_INVALID', 'msg': '缺少有效的微信登录code'})
        if not isinstance(phone_code, str) or len(phone_code) > 256 or not isinstance(nickname, str):
            return Response({'code': 400, 'errorCode': 'INVALID_INPUT', 'msg': '登录参数格式错误'})
        if not isinstance(check_only, bool):
            return Response({'code': 400, 'errorCode': 'INVALID_INPUT', 'msg': 'checkOnly 必须为布尔值'})
        phone_code = phone_code.strip() if not check_only else ''
        nickname = nickname.strip()[:50] if not check_only else ''
        avatar_file = avatar_file if not check_only else None
        code = code.strip()
        if avatar_file:
            try:
                _validate_avatar(avatar_file)
            except ValueError as exc:
                return Response({'code': 400, 'errorCode': 'INVALID_AVATAR', 'msg': str(exc)})

        if settings.WECHAT_MOCK_LOGIN:
            openid = f"wx_mock_{code[:10]}"
            phone = f"138{code[:8].zfill(8)[-8:]}" if phone_code else ''
        else:
            if not settings.WECHAT_MINIAPP_APPID or not settings.WECHAT_MINIAPP_SECRET:
                return Response({'code': 503, 'errorCode': 'WECHAT_NOT_CONFIGURED', 'msg': '微信登录尚未完成配置，请联系管理员'})
            try:
                openid = _wechat_code2session(code)
                phone = _wechat_get_phone(phone_code) if phone_code else ''
            except (ValueError, OSError) as exc:
                return _wechat_error_response(exc)

        try:
            _lock_wechat_phone(phone)
            account, created, bind_status = self._resolve_customer(
                openid=openid,
                phone=phone,
                allow_phone_binding=not settings.WECHAT_MOCK_LOGIN,
                check_only=check_only,
                defaults={
                    'username': f"xa_{uuid.uuid4().hex[:8]}",
                    'account_type': ClubAccount.AccountType.BOSS,
                    'phone': phone,
                    'nickname': nickname,
                    'is_phone_verified': bool(phone),
                    'is_openid_bound': True,
                },
            )
        except WechatLoginError as exc:
            return _wechat_error_response(exc)

        if account is None:
            return Response({'code': 0, 'data': {
                'requiresBinding': True,
                'bindingState': 'UNBOUND',
            }})

        # 老板端仅允许老板登录：已存在的非老板账号（陪玩/客服/管理员）拒绝从此入口进入。
        if not created and account.account_type != ClubAccount.AccountType.BOSS:
            return Response({'code': 403, 'msg': '该微信号非老板账号，请使用对应端登录'})

        if not account.is_active or not account.can_login:
            return Response({'code': 403, 'msg': '该老板账号已被禁用，请联系客服'})

        updated_fields = []
        if not settings.WECHAT_MOCK_LOGIN and account.wechat_appid != settings.WECHAT_MINIAPP_APPID:
            account.wechat_appid = settings.WECHAT_MINIAPP_APPID
            updated_fields.append('wechat_appid')
        if not account.is_openid_bound:
            account.is_openid_bound = True
            updated_fields.append('is_openid_bound')
        if phone and (account.phone != phone or not account.is_phone_verified):
            account.phone = phone
            account.is_phone_verified = True
            updated_fields.extend(['phone', 'is_phone_verified'])

        # 微信昵称：填写能力返回真实昵称时同步落库
        if nickname and account.nickname != nickname:
            account.nickname = nickname
            updated_fields.append('nickname')

        # 微信头像：chooseAvatar 上传的头像文件，落库并回填绝对 URL 供 C 端/后台展示
        if avatar_file:
            account.avatar = avatar_file
            account.save(update_fields=['avatar'] if not created else None)
            account.avatar_url = build_media_url(request, account.avatar)
            updated_fields.append('avatar_url')

        if created:
            updated_fields.extend([
                'account_type', 'phone', 'nickname',
                'is_phone_verified', 'is_openid_bound',
            ])

        if updated_fields:
            account.save(update_fields=list(dict.fromkeys(updated_fields)))

        legacy_user = _legacy_user_for_account(account)
        if legacy_user:
            legacy_fields = []
            for field in ('phone', 'nickname', 'avatar_url'):
                value = getattr(account, field)
                if getattr(legacy_user, field) != value:
                    setattr(legacy_user, field, value)
                    legacy_fields.append(field)
            if account.is_phone_verified != legacy_user.is_phone_verified:
                legacy_user.is_phone_verified = account.is_phone_verified
                legacy_fields.append('is_phone_verified')
            if account.is_openid_bound != legacy_user.is_openid_bound:
                legacy_user.is_openid_bound = account.is_openid_bound
                legacy_fields.append('is_openid_bound')
            if legacy_fields:
                legacy_user.save(update_fields=legacy_fields)

        access_token, refresh_token = issue_account_tokens(account)
        response_id = (
            legacy_user.id
            if getattr(settings, 'LEGACY_FORCE_AUTH_COMPAT', False) and legacy_user
            else account.id
        )
        return Response({
            'code': 0,
            'msg': 'success',
            'data': {
                'token': access_token,
                'refreshToken': refresh_token,
                'requiresBinding': False,
                'userInfo': {
                    'id': str(response_id),
                    'username': account.username,
                    'nickname': account.nickname or account.username,
                    'avatar': bounded_public_image_url(account.avatar_url or ''),
                    'role': 'customer',
                    'backendRole': account.account_type,
                    'openid': account.openid,
                    'phone': account.phone,
                    'bindCode': None,
                    'bindStatus': bind_status,
                    **_wechat_binding_info(account),
                }
            }
        })


class WechatBindView(APIView):
    """Bind fresh WeChat proof to the authenticated boss without moving assets."""

    permission_classes = [IsClubAccountAuthenticated]
    throttle_scope = 'login'

    def post(self, request):
        code = request.data.get('code')
        phone_code = request.data.get('phoneCode') or ''
        if not isinstance(code, str) or not code.strip() or len(code) > 256:
            return Response({'code': 400, 'errorCode': 'WECHAT_CODE_INVALID', 'msg': '请重新获取微信登录凭证'})
        if not isinstance(phone_code, str) or len(phone_code) > 256:
            return Response({'code': 400, 'errorCode': 'INVALID_INPUT', 'msg': '手机号授权参数格式错误'})
        try:
            _ensure_boss_login_allowed(request.account)
            if settings.WECHAT_MOCK_LOGIN:
                openid = f'wx_mock_{code.strip()[:10]}'
                phone = f"138{code.strip()[:8].zfill(8)[-8:]}" if phone_code else ''
            else:
                if not settings.WECHAT_MINIAPP_APPID or not settings.WECHAT_MINIAPP_SECRET:
                    raise WechatLoginError('微信登录尚未完成配置，请联系管理员', 'WECHAT_NOT_CONFIGURED', 503)
                openid = _wechat_code2session(code.strip())
                phone = _wechat_get_phone(phone_code.strip()) if phone_code.strip() else ''
            with transaction.atomic():
                _lock_wechat_phone(phone)
                account = ClubAccount.objects.select_for_update().get(pk=request.account.pk)
                _ensure_boss_login_allowed(account)
                legacy_user = _legacy_user_for_account(account)
                if account.openid and account.openid != openid:
                    raise WechatLoginError('该账号已绑定其他微信，请联系客服处理', 'ACCOUNT_CONFLICT', 409)
                owners = ClubAccount.objects.filter(openid=openid).exclude(pk=account.pk)
                legacy_owners = User.objects.filter(openid=openid)
                if legacy_user:
                    legacy_owners = legacy_owners.exclude(pk=legacy_user.pk)
                    if legacy_user.openid and legacy_user.openid != openid:
                        raise WechatLoginError('历史账号的微信绑定不一致，请联系客服处理', 'ACCOUNT_CONFLICT', 409)
                if owners.exists() or legacy_owners.exists():
                    raise WechatLoginError('当前微信已绑定其他账号，请联系客服处理', 'ACCOUNT_CONFLICT', 409)
                if phone:
                    _assert_phone_available(account, phone)
                already_bound = bool(account.openid == openid and account.is_openid_bound)
                account.openid = openid
                account.is_openid_bound = True
                account.wechat_appid = settings.WECHAT_MINIAPP_APPID if not settings.WECHAT_MOCK_LOGIN else ''
                updated_fields = ['openid', 'wechat_appid', 'is_openid_bound']
                if phone:
                    account.phone = phone
                    account.is_phone_verified = True
                    updated_fields.extend(['phone', 'is_phone_verified'])
                account.save(update_fields=[*updated_fields, 'updated_at'])
                if legacy_user:
                    legacy_fields = [field for field in updated_fields if field != 'wechat_appid']
                    for field in legacy_fields:
                        setattr(legacy_user, field, getattr(account, field))
                    legacy_user.save(update_fields=legacy_fields)
        except (ValueError, OSError) as exc:
            return _wechat_error_response(exc)
        except IntegrityError:
            return _wechat_error_response(WechatLoginError(
                '当前微信已绑定其他账号，请刷新后重试', 'ACCOUNT_CONFLICT', 409,
            ))
        return Response({'code': 0, 'msg': '绑定成功', 'data': {
            'bindStatus': 'already_bound' if already_bound else 'bound',
            'userInfo': {
                'id': str(account.pk),
                'phone': account.phone or '',
                **_wechat_binding_info(account),
            },
        }})


class AccountLoginView(APIView):
    """陪玩端账号密码登录：仅服务 PROVIDER 角色。

    陪玩账号由客服在后台开户（见 console 的创建陪玩账号接口），此端点只负责校验登录，
    不再接收 role / bindCode，也不会改写既有账号角色。
    开发期 WECHAT_MOCK_LOGIN=True 时按用户名自动创建陪玩账号、不强校验密码；
    生产环境用 ClubAccount 密码哈希校验真实密码。
    """
    permission_classes = [AllowAny]
    authentication_classes = []
    # 口令登录：爆破的首选目标，限流优先级最高。
    throttle_scope = 'login'

    def post(self, request):
        username = (request.data.get('username') or '').strip()
        password = request.data.get('password') or ''

        if not username:
            return Response({'code': 400, 'msg': '请输入账号'})

        from .models import ProviderRegistration
        if ProviderRegistration.objects.filter(username__iexact=username).exclude(status='APPROVED').exists():
            return Response({'code': 403, 'msg': '注册申请尚未通过，请查询审核结果'})

        if settings.WECHAT_MOCK_LOGIN:
            account = ClubAccount.objects.filter(username=username).first()
            if account is None:
                legacy_user = User.objects.filter(username=username).first()
                if legacy_user is not None:
                    account = get_or_create_account_for_legacy_user(legacy_user)
            created = account is None
            if created:
                account = ClubAccount.objects.create_account(
                    username=username,
                    password=password or None,
                    account_type=ClubAccount.AccountType.PROVIDER,
                    openid=f"acct_{username}",
                    is_openid_bound=True,
                )
                user = User.objects.create_user(
                    username=username,
                    password=None,
                    openid=account.openid,
                    role=User.Role.PROVIDER,
                    is_openid_bound=True,
                )
                LegacyAccountMap.objects.create(
                    legacy_user_id=user.id,
                    account=account,
                    legacy_role=User.Role.PROVIDER,
                )
                EscortProfile.objects.create(
                    user=user,
                    account=account,
                    display_name=account.username,
                    status=EscortProfile.Status.OFFLINE,
                    is_verified=False,
                )
                Wallet.objects.filter(user=user).update(account=account)
        else:
            if not password:
                return Response({'code': 400, 'msg': '请输入密码'})
            account = authenticate_account(
                username,
                password,
                account_type=ClubAccount.AccountType.PROVIDER,
            )
            if account is None:
                # Allow a staged deployment to migrate a valid historical
                # provider lazily when it first logs in.
                legacy_user = authenticate(
                    request=request,
                    username=username,
                    password=password,
                )
                if legacy_user is not None:
                    account = get_or_create_account_for_legacy_user(legacy_user)
            if account is None:
                return Response({'code': 400, 'msg': '账号或密码错误'})

        # 陪玩端仅允许陪玩登录：老板/客服/管理员账号一律拒绝，且不改写其角色。
        if account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '该账号非陪玩账号，请使用对应端登录'})

        if not account.is_active or not account.can_login:
            return Response({'code': 403, 'msg': '账号已被禁用'})

        from club_accounts.otp import otp_login_gate
        otp=otp_login_gate(account,request.data,'provider')
        if isinstance(otp,Response):return otp
        access_token, refresh_token = issue_account_tokens(account,otp_verified=bool(otp and otp['otp_verified']))

        return Response({
            'code': 0,
            'msg': 'success',
            'data': {
                'recoveryCodes': otp.get('recoveryCodes',[]) if otp else [],
                'token': access_token,
                'refreshToken': refresh_token,
                'userInfo': {
                    'id': str(account.id),
                    'username': account.username,
                    'nickname': account.nickname or account.username,
                    'role': 'provider',
                    'backendRole': account.account_type,
                    'openid': account.openid,
                    'phone': account.phone,
                    'bindCode': None,
                    'bindStatus': 'direct',
                }
            }
        })


class AccountRefreshView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        raw_refresh_token = request.data.get('refresh')
        if not raw_refresh_token:
            return Response({'code': 400, 'msg': '缺少 refresh token'})
        try:
            access_token = refresh_account_access_token(raw_refresh_token)
        except TokenError:
            return Response(
                {'code': 401, 'msg': '登录已过期，请重新登录'},
                status=401,
            )
        return Response({'code': 0, 'data': {'token': access_token}})


class MeView(APIView):
    """当前登录用户资料：GET 读取，PATCH 更新昵称/手机号/常用游戏资料。"""
    permission_classes = [IsClubAccountAuthenticated]

    def _serialize(self, account):
        game_profiles = [
            {
                'game_category': profile.game_category_id,
                'game_category_name': profile.game_category.name,
                'region': profile.region,
                'nickname': profile.nickname,
                'uid': profile.uid,
                'is_default': profile.is_default,
            }
            for profile in account.game_profiles.select_related('game_category').filter(
                game_category__is_active=True,
            )
        ]
        return {
            'id': str(account.id),
            'username': account.username,
            'nickname': account.nickname or account.username,
            'role': ROLE_REVERSE_MAP.get(account.account_type, 'customer'),
            'phone': account.phone or '',
            'avatar': bounded_public_image_url(account.avatar_url or ''),
            'game_region': account.game_region,
            'game_nickname': account.game_nickname,
            'game_uid': account.game_uid,
            'game_profiles': game_profiles,
            **_wechat_binding_info(account),
        }

    def get(self, request):
        return Response({'code': 0, 'data': self._serialize(request.account)})

    def patch(self, request):
        account = request.account
        legacy_user = request.legacy_user
        if 'phone' in request.data:
            phone = request.data.get('phone') or ''
            if not isinstance(phone, str):
                return Response({'code': 400, 'errorCode': 'INVALID_INPUT', 'msg': '手机号格式错误'})
            if account.account_type == ClubAccount.AccountType.BOSS and phone.strip() != (account.phone or ''):
                return Response({
                    'code': 400,
                    'errorCode': 'PHONE_VERIFICATION_REQUIRED',
                    'msg': '请通过微信手机号授权修改绑定手机号',
                })
        # Validate every scalar before game profile writes, so malformed mixed
        # updates cannot save one part and then report a validation failure.
        editable = {'nickname': 50, 'phone': 20, 'game_region': 50,
                    'game_nickname': 50, 'game_uid': 50}
        for field, max_len in editable.items():
            if field not in request.data:
                continue
            value = request.data.get(field) or ''
            if not isinstance(value, str) or len(value.strip()) > max_len:
                return Response({'code': 400, 'msg': f'{field} 格式或长度无效'})
        game_profiles = request.data.get('game_profiles')
        if game_profiles is not None:
            if account.account_type != ClubAccount.AccountType.BOSS:
                return Response({'code': 403, 'msg': '仅老板可维护常用游戏资料'})
            if not isinstance(game_profiles, list):
                return Response({'code': 400, 'msg': 'game_profiles 必须为数组'})
            category_ids = [item.get('game_category') for item in game_profiles if isinstance(item, dict)]
            if len(category_ids) != len(game_profiles) or any(not isinstance(item, int) for item in category_ids):
                return Response({'code': 400, 'msg': '请选择有效的游戏类目'})
            if len(set(category_ids)) != len(category_ids):
                return Response({'code': 400, 'msg': '游戏类目不能重复'})
            active_categories = set(
                GameCategory.objects.filter(id__in=category_ids, is_active=True).values_list('id', flat=True)
            )
            if active_categories != set(category_ids):
                return Response({'code': 400, 'msg': '包含不存在或已停用的游戏类目'})

            normalized = []
            for item in game_profiles:
                values = {}
                for source, target in (('region', 'region'), ('nickname', 'nickname'), ('uid', 'uid')):
                    raw_value = item.get(source) or ''
                    if not isinstance(raw_value, str):
                        return Response({'code': 400, 'msg': f'{source} 格式无效'})
                    value = raw_value.strip()
                    if len(value) > 50:
                        return Response({'code': 400, 'msg': f'{source} 长度不能超过 50'})
                    values[target] = value
                values['game_category_id'] = item['game_category']
                values['is_default'] = bool(item.get('is_default'))
                normalized.append(values)

            with transaction.atomic():
                default_assigned = False
                saved_profiles = []
                for values in normalized:
                    has_content = any(values[field] for field in ('region', 'nickname', 'uid'))
                    if not has_content:
                        CustomerGameProfile.objects.filter(
                            account=account,
                            game_category_id=values['game_category_id'],
                        ).delete()
                        continue
                    is_default = values.pop('is_default') and not default_assigned
                    profile, _ = CustomerGameProfile.objects.update_or_create(
                        account=account,
                        game_category_id=values.pop('game_category_id'),
                        defaults={
                            **values,
                            'user': legacy_user,
                            'is_default': is_default,
                        },
                    )
                    if is_default:
                        default_assigned = True
                    saved_profiles.append(profile)
                CustomerGameProfile.objects.filter(account=account).exclude(
                    game_category_id__in=category_ids,
                ).delete()
                if saved_profiles and not default_assigned:
                    saved_profiles[0].is_default = True
                    saved_profiles[0].save(update_fields=['is_default'])
                    default_assigned = True
                default_profile = next((item for item in saved_profiles if item.is_default), None)
                if default_profile is None and saved_profiles:
                    default_profile = saved_profiles[0]
                account.game_region = default_profile.region if default_profile else ''
                account.game_nickname = default_profile.nickname if default_profile else ''
                account.game_uid = default_profile.uid if default_profile else ''
                account.save(update_fields=['game_region', 'game_nickname', 'game_uid'])
                if legacy_user:
                    legacy_user.game_region = account.game_region
                    legacy_user.game_nickname = account.game_nickname
                    legacy_user.game_uid = account.game_uid
                    legacy_user.save(update_fields=[
                        'game_region', 'game_nickname', 'game_uid',
                    ])

        # 允许更新的字段及其最大长度
        editable = {
            'nickname': 50,
            'phone': 20,
            'game_region': 50,
            'game_nickname': 50,
            'game_uid': 50,
        }
        updated_fields = []
        for field, max_len in editable.items():
            if field not in request.data:
                continue
            value = (request.data.get(field) or '').strip()
            if len(value) > max_len:
                return Response({'code': 400, 'msg': f'{field} 长度不能超过 {max_len}'})
            if field == 'phone' and value != (account.phone or ''):
                account.is_phone_verified = False
                updated_fields.append('is_phone_verified')
            setattr(account, field, value)
            updated_fields.append(field)

        if updated_fields:
            account.save(update_fields=updated_fields)
            if legacy_user:
                for field in updated_fields:
                    setattr(legacy_user, field, getattr(account, field))
                legacy_user.save(update_fields=updated_fields)

        return Response({'code': 0, 'msg': '已更新', 'data': self._serialize(account)})


class MeAvatarView(APIView):
    """用户主动选择微信头像后，仅更新当前登录老板的头像。"""
    permission_classes = [IsClubAccountAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        if request.account.account_type != ClubAccount.AccountType.BOSS:
            return Response({'code': 403, 'msg': '仅老板可通过此入口修改头像'})
        avatar = request.FILES.get('avatar')
        if not avatar:
            return Response({'code': 400, 'msg': '请选择头像'})
        try:
            _validate_avatar(avatar)
        except ValueError as exc:
            return Response({'code': 400, 'errorCode': 'INVALID_AVATAR', 'msg': str(exc)})
        with transaction.atomic():
            account = ClubAccount.objects.select_for_update().get(pk=request.account.pk)
            account.avatar = avatar
            account.save(update_fields=['avatar'])
            account.avatar_url = build_media_url(request, account.avatar)
            account.save(update_fields=['avatar_url'])
            if request.legacy_user:
                User.objects.filter(pk=request.legacy_user.pk).update(avatar_url=account.avatar_url)
        return Response({'code': 0, 'data': {'avatar': account.avatar_url}})


class EscortProfileListView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request, provider_id=None):
        game = request.query_params.get('game', '').strip()
        game_category_id = request.query_params.get('game_category', '').strip()
        service_item_id = request.query_params.get('service_item', '').strip()
        gender = request.query_params.get('gender', 'ANY')
        if gender not in ('ANY', 'MALE', 'FEMALE'):
            return Response({'code': 400, 'msg': '陪玩性别参数无效'}, status=400)
        detail = provider_id is not None
        shortcuts = not detail and request.query_params.get('shortcuts') == '1'
        picker = not detail and request.query_params.get('picker') == '1' and not shortcuts
        directory = detail or shortcuts or (request.query_params.get('directory') == '1' and not service_item_id)
        service = None
        if service_item_id:
            if not service_item_id.isascii() or not service_item_id.isdigit() or len(service_item_id) > 18:
                return Response({'code': 400, 'msg': '服务项目参数无效'}, status=400)
            service = ServiceItem.objects.filter(visible_service_q(), pk=int(service_item_id)).select_related('required_level').first()
            if service is None:
                if detail:
                    return Response({'code': 404, 'msg': '服务已下架，请返回重新选择'}, status=404)
                return Response({'code': 0, 'data': []})
        if game_category_id and (not game_category_id.isascii() or not game_category_id.isdigit() or len(game_category_id) > 18):
            return Response({'code': 400, 'msg': '游戏类目参数无效'}, status=400)
        profiles = EscortProfile.objects.filter(
            is_verified=True,
            account__is_active=True, account__can_login=True, account__can_view=True,
        ).select_related('account', 'user', 'level').prefetch_related(
            'game_categories',
            'service_items',
            'user__schedules', 'qualifications__game_category',
        )
        from .qualifications import filtered_qualifications,serialize_qualification,qualification_rows,service_requirement,scope_matches,service_skill
        if detail:
            profiles = profiles.filter(account_id=provider_id)
        qualification_filter = None if detail else filtered_qualifications(request.query_params)
        if qualification_filter is not None:profiles=profiles.filter(qualification_filter).distinct()
        recent_ids = []
        if shortcuts:
            from .companion_shortcuts import recent_provider_ids
            recent_ids = recent_provider_ids(request.account)
            profiles = profiles.filter(Q(account_id__in=recent_ids) | Q(recommendation_slot__isnull=False))
        if gender != 'ANY' and not detail:
            profiles = profiles.filter(gender=gender)
        from wallet.club_rules import club_income_rules_enabled
        club_rules_enabled = club_income_rules_enabled()
        if not directory:
            deposit_rule = Q(deposit_mode='EARNINGS') | (Q(deposit_paid__gte=20000) if club_rules_enabled else Q(deposit_paid__gte=F('deposit_required')))
            profiles = profiles.filter(deposit_rule, status=EscortProfile.Status.AVAILABLE)
        if detail:
            # A visible profile remains browsable when its current availability or
            # service qualification changes; only the selection action is blocked.
            pass
        elif service_item_id.isdigit():
            # 最细粒度：仅展示可接该服务项的陪玩（未勾选该服务项的陪玩不出现）
            skills=Q(service_items__id=int(service_item_id))
            if service and service.catalog_family:
                skills |= Q(service_items__catalog_family=service.catalog_family,service_items__game_category_id=service.game_category_id,service_items__platform=service.platform)
            profiles = profiles.filter(skills).distinct()
        elif game_category_id.isdigit():
            # 精准筛选：仅展示可接该游戏类目的陪玩（未配置可接游戏的陪玩不出现）
            profiles = profiles.filter(game_categories__id=int(game_category_id), game_categories__is_active=True).distinct()
        elif game:
            profiles = profiles.filter(
                Q(service_area__icontains=game)
                | Q(user__received_orders__service__game_category__name__iexact=game)
            ).distinct()

        from orders.services import active_orders_for
        # Reuse the dispatch capacity predicate, including legacy IDs, double
        # providers and released shares, in one database round trip for the list.
        active_count = active_orders_for(
            account_id=OuterRef('account_id'), provider_id=OuterRef('user_id'),
        ).order_by().values(group=Value(1)).annotate(total=Count('pk', distinct=True)).values('total')
        profiles = profiles.annotate(
            active_order_count=Coalesce(Subquery(active_count, output_field=IntegerField()), 0),
            pass_priority=Case(
                When(pass_expires_at__gt=timezone.now(), pass_tier=EscortProfile.PassTier.BLACK, then=Value(4)),
                When(pass_expires_at__gt=timezone.now(), pass_tier=EscortProfile.PassTier.GOLD, then=Value(3)),
                When(pass_expires_at__gt=timezone.now(), pass_tier=EscortProfile.PassTier.SILVER, then=Value(2)),
                When(pass_expires_at__gt=timezone.now(), pass_tier=EscortProfile.PassTier.BRONZE, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
            review_count=Count('user__received_evaluations', distinct=True),
            positive_review_count=Count(
                'user__received_evaluations',
                filter=Q(user__received_evaluations__score__gte=4),
                distinct=True,
            ),
        ).annotate(
            favorable_rate=Case(
                When(review_count=0, then=Value(0.0)),
                default=ExpressionWrapper(
                    Value(100.0) * F('positive_review_count') / F('review_count'),
                    output_field=FloatField(),
                ),
                output_field=FloatField(),
            )
        ).order_by('-pass_priority', '-favorable_rate', '-rating_avg', '-rating_count', '-completed_order_count', 'id')

        from engagement.models import ProviderNaming
        naming_map = {}
        for naming in ProviderNaming.objects.filter(provider_id__in=profiles.values('account_id'),
                is_visible=True, expires_at__gt=timezone.now()).order_by('-expires_at'):
            naming_map.setdefault(naming.provider_id, []).append({'id': naming.pk, 'title': naming.title, 'expires_at': naming.expires_at})
        data = []
        listed_profiles = []
        for p in profiles:
            # Historical shortcuts may show availability, but never grant a new service qualification.
            if shortcuts and service and (not p.can_take_service(service) or not service_skill(p,service)):
                continue
            acceptance_error = p.order_acceptance_error(service, club_rules_enabled=club_rules_enabled, provider_gender=gender)
            if detail and service and not acceptance_error:
                configured = any(s.pk == service.pk or (service.catalog_family
                    and s.catalog_family == service.catalog_family and s.game_category_id == service.game_category_id
                    and s.platform == service.platform) for s in p.service_items.all())
                if not configured:
                    acceptance_error = '陪玩尚未配置该服务技能'
            if not acceptance_error and p.active_order_count >= Order.MAX_CONCURRENT_ORDERS:
                acceptance_error = '该陪玩当前接单已满，请稍后再约'
            if detail and not acceptance_error and p.status != EscortProfile.Status.AVAILABLE:
                acceptance_error = '该陪玩暂不可接单，请稍后再约'
            can_order = not acceptance_error
            if shortcuts:
                can_order = can_order and p.status == EscortProfile.Status.AVAILABLE
            if not directory and not can_order:
                continue
            identity = p.account or p.user
            qualified=qualification_rows(p)
            requirement=service_requirement(service) if service and not detail else None
            if requirement:qualified=[q for q in qualified if scope_matches(q,requirement)]
            qualified=[q for q in qualified if q.is_active]
            for key in ('provider_kind','platform','security_level'):
                if request.query_params.get(key):qualified=[q for q in qualified if getattr(q,key)==request.query_params[key]]
            if game_category_id:qualified=[q for q in qualified if q.game_category_id==int(game_category_id)]
            reference=qualified[0].price_per_hour if len(qualified)==1 and qualified[0].price_per_hour is not None else p.price_per_hour
            item = {
                'id': p.account_id or p.user_id,
                'nickname': p.display_name or identity.nickname or identity.username,
                'gender': p.gender,
                'recommendation_slot': p.recommendation_slot,
                'avatar': identity.avatar_url or '',
                'rank': p.rank_tier,
                'level_name': p.level.name if p.level_id and p.level.is_active else '',
                'rating': float(p.rating_avg),
                'ratingCount': p.rating_count,
                'favorableRate': round(float(p.favorable_rate or 0), 1),
                'orderCount': p.completed_order_count,
                'winRate': float(p.win_rate),
                'pricePerHour': reference or 0,
                'qualifications': [serialize_qualification(q) for q in qualified],
                'namings': naming_map.get(p.account_id, []),
                'hasReferenceHourlyPrice': reference is not None,
                'bio': p.bio,
                'city': p.city,
                'status': p.status,
                'can_order': can_order,
                'is_verified': p.is_verified,
                'voice_card_url': build_media_url(request, p.voice_card),
                'game_category_ids': [c.id for c in p.game_categories.all()],
                'game_category_names': [c.name for c in p.game_categories.all() if c.is_active],
                'service_item_ids': [s.id for s in p.service_items.all()],
            }
            if detail:
                item['order_unavailable_reason'] = acceptance_error
            data.append(item)
            listed_profiles.append(p)

        from .companion_picker import companion_service_amounts, order_service_companions
        amounts = companion_service_amounts(listed_profiles, include_recent=picker)
        for item in data:
            item['monthly_order_amount'] = amounts['monthly'].get(item['id'], 0)

        if detail:
            if not data:
                return Response({'code': 404, 'msg': '陪玩资料暂不可查看，请返回列表重新选择'}, status=404)
            return Response({'code': 0, 'data': data[0]})
        if shortcuts:
            by_id = {row['id']: row for row in data}
            recent = [by_id[pk] for pk in recent_ids if pk in by_id]
            recommended = sorted((row for row in data if row['recommendation_slot']), key=lambda row: row['recommendation_slot'])[:4]
            return Response({'code': 0, 'data': {'recent': recent, 'recommended': recommended}})
        if picker:
            data = order_service_companions(data, listed_profiles, amounts['recent'])
        return Response({'code': 0, 'data': data})


class EscortProfileDetailView(EscortProfileListView):
    """Use the same public identity, visibility and current metrics as the roster."""


class UpdateEscortStatusView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可操作'})

        try:
            profile = request.account.escort_profile
        except EscortProfile.DoesNotExist:
            return Response({'code': 403, 'msg': '未创建大神资料'})

        status = request.data.get('status', '').upper()
        if status not in ('AVAILABLE', 'BUSY', 'OFFLINE'):
            return Response({'code': 400, 'msg': '状态非法'})

        profile.status = getattr(EscortProfile.Status, status)
        profile.save(update_fields=['status'])
        return Response({'code': 0, 'msg': '状态已更新'})


class ProviderStatsView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    INCOME_TREND_DAYS = 7

    def _income_trend(self, wallet):
        """近 N 日陪玩收益趋势（INCOME+SUCCESS 按自然日聚合，缺失日补零）。"""
        today = timezone.localdate()
        start_date = today - timedelta(days=self.INCOME_TREND_DAYS - 1)
        buckets = {
            (start_date + timedelta(days=i)).isoformat(): 0
            for i in range(self.INCOME_TREND_DAYS)
        }
        if wallet:
            rows = (
                wallet.transactions.filter(
                    tx_type=Transaction.TxType.INCOME,
                    status=Transaction.Status.SUCCESS,
                    created_at__date__gte=start_date,
                    created_at__date__lte=today,
                )
                .annotate(day=TruncDate('created_at'))
                .values('day')
                .annotate(total=Sum('amount'))
            )
            for row in rows:
                buckets[row['day'].isoformat()] = row['total'] or 0
        return [{'date': day, 'income': amount} for day, amount in buckets.items()]

    def get(self, request):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可查看'})

        orders = Order.objects.filter(provider_account=request.account)
        today = Order.objects.filter(
            provider_account=request.account,
            created_at__date=timezone.localdate(),
        )

        profile = EscortProfile.objects.filter(account=request.account).first()

        # 走统一寻址：account 维度查不到时回落 user 维度，
        # 避免历史上 account 未回填的钱包被当成「没有收入」。
        wallet = get_wallet(
            account=request.account, user=request.legacy_user, create=False,
        )
        total_income = 0
        if wallet:
            total_income = wallet.transactions.filter(
                tx_type=Transaction.TxType.INCOME,
                status=Transaction.Status.SUCCESS,
            ).aggregate(total=Sum('amount'))['total'] or 0

        total_completed = orders.filter(status=Order.Status.COMPLETED).count()
        total_cancelled = orders.filter(status=Order.Status.CANCELLED).count()
        finished = total_completed + total_cancelled
        completion_rate = round(total_completed / finished * 100) if finished else 0

        from orders.services import count_active_orders
        active_orders = count_active_orders(request.account)

        return Response({
            'code': 0,
            'data': {
                'today_orders': today.count(),
                'serving': orders.filter(status=Order.Status.IN_SERVICE).count(),
                'active_orders': active_orders,
                'max_concurrent_orders': Order.MAX_CONCURRENT_ORDERS,
                'total_completed': total_completed,
                'pending': orders.filter(status=Order.Status.PENDING).count(),
                'rating_avg': float(profile.rating_avg) if profile else 0,
                'rating_count': profile.rating_count if profile else 0,
                'total_income': total_income,
                'completion_rate': completion_rate,
                'income_trend': self._income_trend(wallet),
                'escort_status': profile.status if profile else EscortProfile.Status.OFFLINE,
            }
        })


class ProviderPassView(APIView):
    """陪玩通行证：余额购买，决定公共单池的提前可见时间。

    档位价格与延迟已下沉到 ``SystemConfig``（``wallet.models`` 的
    ``get_pass_daily_price`` / ``get_pass_delay_seconds``），运营调价即时生效、
    无需发版；未配置时回落出厂默认值，与历史硬编码完全一致。
    """

    permission_classes = [IsClubAccountAuthenticated]
    throttle_scope = 'wallet_write'

    def _profile(self, request):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return None
        return EscortProfile.objects.filter(account=request.account).prefetch_related('qualifications__game_category').first()

    def _data(self, request, profile):
        wallet = get_wallet(account=request.account, user=request.legacy_user)
        products = []
        for tier, label in EscortProfile.PassTier.choices:
            products.append({
                'tier': tier, 'name': label,
                'daily_price': get_pass_daily_price(tier),
                'delay_seconds': get_pass_delay_seconds(tier),
            })
        history = ProviderPassPurchase.objects.filter(account=request.account)[:20]
        return {
            'balance': wallet.balance,
            'active_tier': profile.active_pass_tier,
            'active_tier_display': dict(EscortProfile.PassTier.choices).get(
                profile.active_pass_tier, '无通行证',
            ),
            'expires_at': profile.pass_expires_at,
            'current_delay_seconds': profile.order_visibility_delay_seconds,
            'no_pass_delay_seconds': get_pass_delay_seconds(''),
            'products': products,
            'history': [{
                'id': item.id, 'tier': item.tier,
                'tier_display': item.get_tier_display(), 'days': item.days,
                'paid_amount': item.paid_amount, 'starts_at': item.starts_at,
                'expires_at': item.expires_at, 'created_at': item.created_at,
            } for item in history],
        }

    def get(self, request):
        profile = self._profile(request)
        if profile is None:
            return Response({'code': 403, 'msg': '仅陪玩可购买通行证'})
        return Response({'code': 0, 'data': self._data(request, profile)})

    @recover_fund_result('provider.pass')
    def post(self, request):
        profile = self._profile(request)
        if profile is None:
            return Response({'code': 403, 'msg': '仅陪玩可购买通行证'})
        tier = str(request.data.get('tier') or '').upper()
        if tier not in get_pass_daily_prices():
            return Response({'code': 400, 'msg': '请选择有效通行证'})
        try:
            from rest_framework import serializers
            days = serializers.IntegerField(min_value=1, max_value=1).run_validation(request.data.get('days', 1))
        except serializers.ValidationError:
            return Response({'code': 400, 'msg': '购买天数非法'})
        if days != 1:
            return Response({'code': 400, 'msg': '通行证按日采购，每次有效期 1 天'})

        daily_price = get_pass_daily_price(tier)
        if daily_price < 1 or daily_price > 2147483647:
            return Response({'code': 400, 'msg': '通行证价格配置异常，请联系客服'})
        if 'expected_daily_price' in request.data and request.data['expected_daily_price'] != daily_price:
            return Response({'code': 400, 'msg': '通行证价格已更新，请刷新后确认购买'})
        original_amount = daily_price * days
        paid_amount = original_amount
        now = timezone.now()

        with transaction.atomic():
            locked_profile = EscortProfile.objects.select_for_update().get(pk=profile.pk)
            replayed, reason = peek_fund_write(account=request.account, request_id=request.data.get('client_request_id'))
            if replayed:
                return Response({'code': 409, 'msg': reason})
            if locked_profile.active_pass_tier and locked_profile.active_pass_tier != tier:
                return Response({
                    'code': 400,
                    'msg': '当前通行证尚未到期，仅支持续费同等级通行证',
                })
            wallet = get_wallet(
                account=request.account, user=request.legacy_user, for_update=True,
            )
            if not wallet.is_active:
                return Response({'code': 403, 'msg': '钱包不可用'})
            if wallet.balance < paid_amount:
                return Response({'code': 400, 'msg': '兴安币不足，请联系企业微信客服充值'})
            platform_wallet = get_platform_wallet(for_update=True)
            if platform_wallet.balance + paid_amount > 2147483647:
                return Response({'code': 400, 'msg': '平台入账金额超过系统范围，请联系客服'})
            allowed, reason = claim_fund_write(
                account=request.account, request_id=request.data.get('client_request_id'),
                scope='provider.pass', fingerprint=f'{tier}:{days}:{paid_amount}',
            )
            if not allowed:
                return Response({'code': 409, 'msg': reason})
            starts_at = max(now, locked_profile.pass_expires_at or now)
            expires_at = starts_at + timedelta(days=days)
            before = wallet.balance
            wallet.balance -= paid_amount
            wallet.save(update_fields=['balance'])
            tx = Transaction.objects.create(
                wallet=wallet, amount=-paid_amount,
                tx_type=Transaction.TxType.PASS_PURCHASE,
                balance_before=before, balance_after=wallet.balance,
                remark=f'{dict(EscortProfile.PassTier.choices)[tier]} {days}天',
            )
            purchase = ProviderPassPurchase.objects.create(
                provider=request.legacy_user,
                account=request.account,
                tier=tier,
                days=days,
                daily_price=daily_price, original_amount=original_amount,
                discount_amount=original_amount - paid_amount,
                paid_amount=paid_amount, starts_at=starts_at,
                expires_at=expires_at, transaction=tx,
            )
            platform_before = platform_wallet.balance
            platform_wallet.balance += paid_amount
            platform_wallet.save(update_fields=['balance'])
            Transaction.objects.create(
                wallet=platform_wallet, amount=paid_amount,
                tx_type=Transaction.TxType.PASS_INCOME,
                balance_before=platform_before, balance_after=platform_wallet.balance,
                external_tx_id=tx.tx_no, remark=f'通行证购买 #{purchase.pk}：{tx.remark}',
            )
            locked_profile.pass_tier = tier
            locked_profile.pass_expires_at = expires_at
            locked_profile.save(update_fields=['pass_tier', 'pass_expires_at'])
            result = fund_success(request, 'provider.pass', msg='通行证购买成功',
                                  data=self._data(request, locked_profile))

        return result


class EscortScheduleView(APIView):
    """陪玩每周循环档期：GET 读取已设时段，PUT 整表覆盖。

    采用四段制：00-06 / 06-12 / 12-18 / 18-24（分钟边界见 VALID_SEGMENTS）。
    PUT 接收 slots 列表，每项 {weekday, start_minute, end_minute}，
    在 atomic 内先清空本人全部时段再批量写入。
    """

    permission_classes = [IsClubAccountAuthenticated]

    VALID_SEGMENTS = {(0, 360), (360, 720), (720, 1080), (1080, 1440)}

    def _serialize(self, account):
        return [
            {
                'weekday': s.weekday,
                'start_minute': s.start_minute,
                'end_minute': s.end_minute,
            }
            for s in EscortSchedule.objects.filter(account=account)
        ]

    def get(self, request):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可查看档期'})
        return Response({'code': 0, 'data': self._serialize(request.account)})

    def put(self, request):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可设置档期'})

        slots = request.data.get('slots')
        if not isinstance(slots, list):
            return Response({'code': 400, 'msg': '档期数据格式非法'})

        cleaned = set()
        for slot in slots:
            try:
                weekday = int(slot['weekday'])
                start_minute = int(slot['start_minute'])
                end_minute = int(slot['end_minute'])
            except (KeyError, TypeError, ValueError):
                return Response({'code': 400, 'msg': '档期数据格式非法'})
            if weekday < 0 or weekday > 6:
                return Response({'code': 400, 'msg': '星期取值非法'})
            if (start_minute, end_minute) not in self.VALID_SEGMENTS:
                return Response({'code': 400, 'msg': '时段必须为四段制之一'})
            cleaned.add((weekday, start_minute, end_minute))

        with transaction.atomic():
            EscortSchedule.objects.filter(account=request.account).delete()
            EscortSchedule.objects.bulk_create([
                EscortSchedule(
                    provider=request.legacy_user,
                    account=request.account,
                    weekday=weekday,
                    start_minute=start_minute,
                    end_minute=end_minute,
                )
                for weekday, start_minute, end_minute in sorted(cleaned)
            ])

        return Response({
            'code': 0,
            'msg': '档期已保存',
            'data': self._serialize(request.account),
        })


class EscortMeView(APIView):
    """陪玩本人资料：GET 读取，PATCH 更新可自助维护字段。

    可编辑：display_name/bio/city/service_area/gender。
    受保护（仅展示）：price_per_hour/level/rating/抽成/认证/押金等由后台维护。
    """
    permission_classes = [IsClubAccountAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    GENDER_VALUES = {choice.value for choice in EscortProfile.Gender}

    def _get_profile(self, request):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return None
        return EscortProfile.objects.filter(account=request.account).prefetch_related('qualifications__game_category').first()

    def _file_url(self, field, request=None):
        return build_media_url(request, field)

    def _serialize(self, profile, request=None):
        from .qualifications import qualification_rows,serialize_qualification,notification_options
        return {
            'dispatch_notification_options':notification_options(profile),
            'dispatch_notification_preferences':profile.dispatch_notification_preferences,
            'qualifications':[serialize_qualification(q) for q in qualification_rows(profile)],
            'active_pass_tier':profile.active_pass_tier,
            'display_name': profile.display_name,
            'gender': profile.gender,
            'bio': profile.bio,
            'city': profile.city,
            'service_area': profile.service_area,
            'price_per_hour': profile.price_per_hour or 0,
            'rank_tier': profile.rank_tier,
            'status': profile.status,
            'is_verified': profile.is_verified,
            'rating_avg': float(profile.rating_avg),
            'rating_count': profile.rating_count,
            'completed_order_count': profile.completed_order_count,
            'avatar_url': self._file_url(profile.avatar, request),
            'intro_video_url': self._file_url(profile.intro_video, request),
            'voice_card_url': self._file_url(profile.voice_card, request),
            'cheat_proof_url': self._file_url(profile.cheat_proof, request),
            'game_category_ids': list(profile.game_categories.values_list('id', flat=True)),
            'service_item_ids': list(profile.service_items.values_list('id', flat=True)),
        }

    def get(self, request):
        profile = self._get_profile(request)
        if profile is None:
            return Response({'code': 403, 'msg': '当前账号未创建大神资料'})
        return Response({'code': 0, 'data': self._serialize(profile, request)})

    def patch(self, request):
        profile = self._get_profile(request)
        if profile is None:
            return Response({'code': 403, 'msg': '当前账号未创建大神资料'})

        if 'qualifications' in request.data:
            return Response({'code':403,'msg':'接单资格只能由管理后台配置'},status=403)
        # 可编辑文本字段及最大长度
        editable = {
            'display_name': 50,
            'bio': 500,
            'city': 50,
            'service_area': 255,
        }
        changes = {}
        if 'dispatch_notification_preferences' in request.data:
            from .qualifications import notification_options
            raw=request.data['dispatch_notification_preferences']
            allowed={row['key']:row['eligible'] for row in notification_options(profile)}
            if not isinstance(raw,dict) or any(k not in allowed or type(v) is not bool for k,v in raw.items()):
                return Response({'code':400,'msg':'派单通知设置无效'},status=400)
            if any(value and not allowed[key] for key,value in raw.items()):
                return Response({'code':400,'msg':'只能开启已认证的护航类型通知'},status=400)
            changes['dispatch_notification_preferences']=raw
        for field, max_len in editable.items():
            if field not in request.data:
                continue
            value = request.data.get(field)
            if not isinstance(value, str):
                return Response({'code': 400, 'msg': f'{field} 必须为文本'})
            value = value.strip()
            if len(value) > max_len:
                return Response({'code': 400, 'msg': f'{field} 长度不能超过 {max_len}'})
            if field == 'display_name' and not value:
                return Response({'code': 400, 'msg': '昵称不能为空'})
            changes[field] = value

        if 'gender' in request.data:
            gender = request.data.get('gender')
            if not isinstance(gender, str) or gender.strip().upper() not in self.GENDER_VALUES:
                return Response({'code': 400, 'msg': '性别取值非法'})
            changes['gender'] = gender.strip().upper()

        try:
            remove_voice = serializers.BooleanField().run_validation(request.data.get('remove_voice_card', False))
        except serializers.ValidationError:
            return Response({'code': 400, 'msg': '移除语音卡参数无效'})
        if remove_voice:
            if 'voice_card' in request.FILES:
                return Response({'code': 400, 'msg': '更换和移除语音卡不能同时操作'})
            changes['voice_card'] = None

        upload_rules = {
            'avatar': (5 * 1024 * 1024, '头像不能超过 5MB'),
            'intro_video': (50 * 1024 * 1024, '介绍视频不能超过 50MB'),
            'voice_card': (10 * 1024 * 1024, '语音卡不能超过 10MB'),
            'cheat_proof': (10 * 1024 * 1024, '查外挂凭证不能超过 10MB'),
        }
        for field, (max_size, error_message) in upload_rules.items():
            upload = request.FILES.get(field)
            if upload is None:
                continue
            if upload.size > max_size:
                return Response({'code': 400, 'msg': error_message})
            try:
                if field == 'avatar':
                    _validate_avatar(upload)
                else:
                    validator = serializers.ImageField() if field == 'cheat_proof' else serializers.FileField()
                    validator.run_validation(upload)
                EscortProfile._meta.get_field(field).run_validators(upload)
            except (ValueError, serializers.ValidationError, DjangoValidationError):
                return Response({'code': 400, 'msg': (
                    '语音卡请使用 MP3、M4A、WAV、AAC 或 OGG 文件' if field == 'voice_card'
                    else '请上传有效的图片或文件'
                )})
            changes[field] = upload

        # 可接服务项：陪玩自助勾选。传入服务项 ID 列表后覆盖设置，
        # 并同步把这些服务项所属游戏并入 game_categories（勾服务项即隐含可接该游戏）。
        item_ids = None
        if 'service_items' in request.data:
            raw = request.data.get('service_items')
            if isinstance(raw, str):
                # multipart/表单可能以逗号分隔字符串传入
                raw = [seg for seg in raw.split(',') if seg.strip()]
            if not isinstance(raw, (list, tuple)) or any(
                not isinstance(v, (int, str)) or isinstance(v, bool)
                or not str(v).strip().isascii() or not str(v).strip().isdigit()
                or len(str(v).strip()) > 19 or int(v) <= 0 or int(v) > 9223372036854775807
                for v in raw
            ):
                return Response({'code': 400, 'msg': '请选择有效的服务项目'})
            item_ids = {int(v) for v in raw}

        # 全部校验通过后一次写入；头像同步账号，保证老板端立即显示最新头像。
        with transaction.atomic():
            profile = EscortProfile.objects.select_for_update().get(pk=profile.pk)
            if item_ids is not None:
                items = list(ServiceItem.objects.select_for_update().filter(skill_service_q(), id__in=item_ids).order_by('id'))
                previous=set(profile.service_items.values_list('pk',flat=True))
                if any(item.pk not in previous and not profile.can_take_service(item) for item in items):
                    return Response({'code':400,'msg':'所选服务超出当前接单资格，请联系运营认证'})
                if len(items) != len(item_ids):
                    return Response({'code': 400, 'msg': '部分服务已下架或不存在，请刷新后重选'})
            for field, value in changes.items():
                if field=='dispatch_notification_preferences':value=dict(profile.dispatch_notification_preferences,**value)
                setattr(profile, field, value)
            if changes:
                profile.save(update_fields=list(changes))
            if 'avatar' in changes:
                avatar_url = build_media_url(request, profile.avatar)
                ClubAccount.objects.filter(pk=profile.account_id).update(avatar_url=avatar_url)
                if profile.user_id:
                    User.objects.filter(pk=profile.user_id).update(avatar_url=avatar_url)
            if item_ids is not None:
                profile.service_items.set(items)
                profile.game_categories.set({item.game_category_id for item in items if item.game_category_id}|set(profile.qualifications.values_list('game_category_id',flat=True)))

        return Response({'code': 0, 'msg': '已更新', 'data': self._serialize(profile, request)})

    # Taro.uploadFile 使用 POST 上传 multipart；与 PATCH 共用同一更新逻辑。
    post = patch


class EscortSkillOptionsView(APIView):
    """陪玩「我的技能」可选项树：按游戏分组返回其下启用的服务项，供陪玩勾选可接项目。"""
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        items = (
            ServiceItem.objects.filter(skill_service_q())
            .select_related('game_category', 'required_level')
            .order_by('game_category__sort_order', 'game_category_id', 'sort_order', 'id')
        )
        profile=EscortProfile.objects.filter(account=request.account).prefetch_related('qualifications__game_category').first()
        from .qualifications import public_requirement
        groups = {}
        order = []
        for item in items:
            game = item.game_category
            if game.id not in groups:
                groups[game.id] = {
                    'game_category_id': game.id,
                    'game_category_name': game.name,
                    'items': [],
                }
                order.append(game.id)
            groups[game.id]['items'].append({
                'id': item.id,
                'name': item.name,
                'price': item.price,
                'price_unit': item.price_unit,
                'provider_requirement':public_requirement(item),
                'qualified':bool(profile and profile.can_take_service(item)),
                'required_level_name': item.required_level.name if item.required_level_id else '',
                'platform': item.platform,
                'platform_name': item.get_platform_display() if item.platform else '',
                'security_level': item.security_level,
                'security_level_name': item.get_security_level_display() if item.security_level else '',
            })
        return Response({'code': 0, 'data': [groups[gid] for gid in order]})


class CustomerAchievementView(APIView):
    """老板成就馆：成就清单由后台配置，根据已完成订单实时计算解锁状态。"""
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        completed = Order.objects.filter(
            customer_account=request.account,
            status=Order.Status.COMPLETED,
        )
        from orders.refunds import with_net_amount
        agg = with_net_amount(completed.filter(payment_status__in=['PAID','PARTIALLY_REFUNDED'])).aggregate(order_count=Count('id'),total=Sum('_net_paid_amount'))
        metrics = {
            Achievement.Metric.ORDERS: agg['order_count'] or 0,
            Achievement.Metric.AMOUNT: agg['total'] or 0,
        }

        data = []
        for item in Achievement.objects.filter(is_active=True):
            current = metrics.get(item.metric, 0)
            data.append({
                'code': item.code,
                'title': item.title,
                'desc': item.desc,
                'icon': item.icon,
                'metric': item.metric,
                'unlocked': current >= item.target,
                'current': current,
                'target': item.target,
            })

        return Response({'code': 0, 'data': data})


# 每月签到奖励档位（兴安币内部账务单位）：按本月累计签到次数取档，超出则循环
MONTHLY_CHECKIN_REWARDS = [10, 20, 30, 40, 50, 60, 200]


def _checkin_rule():
    rule, _ = CheckinRuleConfig.objects.get_or_create(pk=1)
    return rule


def resolve_checkin_reward(seq_in_month, gift_map=None):
    """签到奖励的**唯一**口径：返回 ``(gift, reward_amount)``。

    此前 GET（日历展示）和 POST（实际发钱）各自拼了一遍
    「有配置礼物就用礼物金额，否则按七日循环表取」的逻辑。
    两处只要有一处漏改，用户就会看到「日历上写着 200，签下去只到账 20」——
    这种账面与实发不一致的问题，用户会当成平台在耍赖。

    Args:
        seq_in_month: 本月第几次签到（从 1 开始）。
        gift_map: 可选的 ``{checkin_day: CheckinGift}`` 预取字典。
            渲染整月日历时传入可避免 N+1 查询；不传则按需单查。

    Returns:
        tuple[CheckinGift | None, int]: 命中的礼物配置与奖励金额
        （内部账务单位整数）。
    """
    if gift_map is not None:
        gift = gift_map.get(seq_in_month)
    else:
        gift = CheckinGift.objects.filter(
            checkin_day=seq_in_month, is_active=True,
        ).first()
    if gift is not None:
        return gift, gift.reward_amount
    index = (seq_in_month - 1) % len(MONTHLY_CHECKIN_REWARDS)
    return None, MONTHLY_CHECKIN_REWARDS[index]


def checkin_gift_payload(seq_in_month, gift=None):
    """统一的礼物展示结构，保证 GET / POST 两端字段与默认值完全一致。"""
    return {
        'name': gift.name if gift else f'第{seq_in_month}天签到礼物',
        'description': gift.description if gift else '',
        'icon': gift.icon if gift else '🎁',
    }


def _reward_for_seq(seq_in_month):
    """兼容旧调用点：只取奖励金额。"""
    return resolve_checkin_reward(seq_in_month)[1]


def _award_checkin(*, account, legacy_user, target_date, seq_in_month, is_makeup):
    """落一条签到记录并把奖励打进钱包。签到发钱的**唯一**实现。

    必须在 ``transaction.atomic`` 内调用：记录与入账要么一起成功，
    要么一起回滚 —— 否则会出现「记录写了钱没到」或「钱到了记录没写」，
    两种都会引来客诉且无法自证。

    并发保护依赖 ``CheckinRecord`` 上 (account, checkin_date) 的唯一约束：
    同日重复提交由数据库抛 ``IntegrityError``，调用方转成「该日期已签到」。

    Args:
        account: 签到的 ``ClubAccount``。
        legacy_user: 对应的旧 ``CustomUser``。
        target_date: 签到日期（补签时为过去某天）。
        seq_in_month: 本月第几次签到，决定奖励档位。
        is_makeup: 是否补签。

    Returns:
        tuple: ``(gift, reward_amount, wallet)``；wallet 为入账后的钱包实例。
    """
    gift, reward_amount = resolve_checkin_reward(seq_in_month)
    payload = checkin_gift_payload(seq_in_month, gift)

    CheckinRecord.objects.create(
        user=legacy_user,
        account=account,
        checkin_date=target_date,
        seq_in_month=seq_in_month,
        reward_amount=reward_amount,
        gift_name=payload['name'],
        gift_icon=payload['icon'],
        is_makeup=is_makeup,
    )

    wallet = get_wallet(account=account, user=legacy_user, for_update=True)
    if reward_amount > 0:
        balance_before = wallet.balance
        wallet.balance += reward_amount
        wallet.save(update_fields=['balance'])
        Transaction.objects.create(
            wallet=wallet,
            amount=reward_amount,
            tx_type=Transaction.TxType.REWARD,
            balance_before=balance_before,
            balance_after=wallet.balance,
            remark=f'月度签到礼物：{payload["name"]}',
        )
        log_money_event(
            'checkin.reward',
            account_id=account.pk,
            user_id=legacy_user.pk,
            tx_type=Transaction.TxType.REWARD,
            amount=reward_amount,
            balance_before=balance_before,
            balance_after=wallet.balance,
            reason=f'seq={seq_in_month};makeup={is_makeup}',
        )
    return gift, reward_amount, wallet


def _daily_paid_amount(account, target_date):
    """按订单创建日统计当日已支付且未退款的兴安币消费。"""
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(target_date, time.min), tz)
    end = start + timedelta(days=1)
    from orders.refunds import with_net_amount
    return with_net_amount(Order.objects.filter(
        customer_account=account,
        payment_status__in=[Order.PaymentStatus.PAID,Order.PaymentStatus.PARTIALLY_REFUNDED],
        created_at__gte=start,
        created_at__lt=end,
    )).aggregate(total=Sum('_net_paid_amount'))['total'] or 0


def _sync_makeup_card(progress, rule, today_spend, today):
    """消费满档后按天发一张补签卡，月库存最多保留配置上限。"""
    grant_key = today.isoformat()
    grant_days = list(progress.card_grant_days or [])
    if (
        today_spend >= rule.makeup_card_spend_required
        and grant_key not in grant_days
        and progress.makeup_cards < rule.max_makeup_cards
    ):
        progress.makeup_cards += 1
        grant_days.append(grant_key)
        progress.card_grant_days = grant_days
        progress.save(update_fields=['makeup_cards', 'card_grant_days', 'updated_at'])
        return True
    return False


def _continuous_days(checked_days, today):
    """根据本月已签日期集合，计算截至今天的连续签到天数。"""
    if today.day not in checked_days:
        return 0
    days = 0
    cursor = today.day
    while cursor >= 1 and cursor in checked_days:
        days += 1
        cursor -= 1
    return days


class CheckinView(APIView):
    """老板月度消费签到：满额签到、补签卡及全勤 KOOK Tag 奖励。"""
    permission_classes = [IsClubAccountAuthenticated]
    # 签到直接发钱，限流防连点刷奖励（仅作用于 POST，GET 日历不受影响）。
    throttle_scope = 'checkin'

    def _month_records(self, account, today):
        return CheckinRecord.objects.filter(
            account=account,
            checkin_date__year=today.year,
            checkin_date__month=today.month,
        )

    def get(self, request):
        if request.account.account_type != ClubAccount.AccountType.BOSS:
            return Response({'code': 403, 'msg': '仅老板可签到'})

        today = timezone.localdate()
        rule = _checkin_rule()
        records = self._month_records(request.account, today)
        checked_days = sorted(r.checkin_date.day for r in records)
        checked_set = set(checked_days)
        checked_count = len(checked_days)
        today_checked = today.day in checked_set

        wallet = get_wallet(account=request.account, user=request.legacy_user)
        progress, _ = CheckinMonthProgress.objects.get_or_create(
            account=request.account,
            year=today.year,
            month=today.month,
            defaults={'user': request.legacy_user},
        )
        today_spend = _daily_paid_amount(request.account, today)
        card_earned_today = _sync_makeup_card(progress, rule, today_spend, today)

        gifts = {gift.checkin_day: gift for gift in CheckinGift.objects.filter(is_active=True)}
        days_in_month = calendar.monthrange(today.year, today.month)[1]
        rewards = []
        for day in range(1, days_in_month + 1):
            # 与 POST 走同一个 resolve_checkin_reward，日历展示的金额
            # 就是签下去真正会到账的金额。
            gift, amount = resolve_checkin_reward(day, gifts)
            rewards.append({
                'seq': day,
                'amount': amount,
                **checkin_gift_payload(day, gift),
            })
        next_seq = checked_count if today_checked else checked_count + 1
        next_gift, next_reward = resolve_checkin_reward(next_seq, gifts)

        return Response({
            'code': 0,
            'data': {
                'year': today.year,
                'month': today.month,
                'days_in_month': days_in_month,
                'today': today.day,
                'today_checked': today_checked,
                'checked_days': checked_days,
                'checked_count': checked_count,
                'continuous_days': _continuous_days(checked_set, today),
                'rewards': rewards,
                'next_reward': next_reward,
                'next_gift': checkin_gift_payload(next_seq, next_gift),
                'balance': wallet.balance,
                'today_spend': today_spend,
                'daily_spend_required': rule.daily_spend_required,
                'makeup_card_spend_required': rule.makeup_card_spend_required,
                'today_eligible': today_spend >= rule.daily_spend_required,
                'makeup_cards': progress.makeup_cards,
                'max_makeup_cards': rule.max_makeup_cards,
                'card_earned_today': card_earned_today or today.isoformat() in (progress.card_grant_days or []),
                'makeup_available_days': [
                    day for day in range(1, today.day)
                    if day not in checked_set
                ],
                'full_attendance': progress.full_attendance_awarded,
        'full_attendance_reward': {
            'name': rule.full_attendance_reward_name,
            'description': rule.full_attendance_reward_desc,
            'icon': '🏆',
            'tag_code': rule.full_attendance_tag_code,
        },
            }
        })

    def post(self, request):
        if request.account.account_type != ClubAccount.AccountType.BOSS:
            return Response({'code': 403, 'msg': '仅老板可签到'})

        today = timezone.localdate()
        requested_day = request.data.get('day')
        is_makeup = requested_day not in (None, '')
        rule = _checkin_rule()
        full_attendance_just_awarded = False

        try:
            with transaction.atomic():
                progress, _ = CheckinMonthProgress.objects.select_for_update().get_or_create(
                    account=request.account,
                    year=today.year,
                    month=today.month,
                    defaults={'user': request.legacy_user},
                )
                today_spend = _daily_paid_amount(request.account, today)
                _sync_makeup_card(progress, rule, today_spend, today)

                if is_makeup:
                    try:
                        day = int(requested_day)
                        target_date = today.replace(day=day)
                    except (TypeError, ValueError):
                        return Response({'code': 400, 'msg': '请选择有效的补签日期'})
                    if target_date >= today:
                        return Response({'code': 400, 'msg': '只能补签本月今日之前的日期'})
                    if progress.makeup_cards <= 0:
                        return Response({'code': 400, 'msg': '暂无可用补签卡'})
                    if self._month_records(request.account, today).filter(
                        checkin_date=target_date,
                    ).exists():
                        return Response({'code': 400, 'msg': '该日期已签到'})
                    progress.makeup_cards -= 1
                    progress.save(update_fields=['makeup_cards', 'updated_at'])
                else:
                    target_date = today
                    if today_spend < rule.daily_spend_required:
                        needed = max(rule.daily_spend_required - today_spend, 0)
                        return Response({
                            'code': 400,
                            'msg': f'今日还需消费 {needed / 10:g} 兴安币才可签到',
                        })
                    if self._month_records(request.account, today).filter(
                        checkin_date=today,
                    ).exists():
                        return Response({'code': 400, 'msg': '今日已签到'})

                seq_in_month = self._month_records(request.account, today).count() + 1
                gift, reward_amount, wallet = _award_checkin(
                    account=request.account,
                    legacy_user=request.legacy_user,
                    target_date=target_date,
                    seq_in_month=seq_in_month,
                    is_makeup=is_makeup,
                )

                checked_count = self._month_records(request.account, today).count()
                days_in_month = calendar.monthrange(today.year, today.month)[1]
                if checked_count == days_in_month and not progress.full_attendance_awarded:
                    progress.full_attendance_awarded = True
                    progress.full_attendance_reward_name = rule.full_attendance_reward_name
                    progress.full_attendance_tag_code = rule.full_attendance_tag_code
                    progress.full_attendance_awarded_at = timezone.now()
                    progress.save(update_fields=[
                        'full_attendance_awarded', 'full_attendance_reward_name',
                        'full_attendance_tag_code', 'full_attendance_awarded_at', 'updated_at',
                    ])
                    full_attendance_just_awarded = True
        except IntegrityError:
            return Response({'code': 400, 'msg': '该日期已签到'})

        if full_attendance_just_awarded:
            create_message(
                recipient_id=request.legacy_user.id,
                title='月度全勤奖励已获得',
                preview=f'恭喜获得 {rule.full_attendance_reward_name}',
                detail=rule.full_attendance_reward_desc,
                action_url='/pages/customer/checkin/index',
            )

        return Response({
            'code': 0,
            'msg': '补签成功' if is_makeup else '签到成功',
            'data': {
                'reward_amount': reward_amount,
                'gift': checkin_gift_payload(seq_in_month, gift),
                'seq_in_month': seq_in_month,
                'checked_count': checked_count,
                'balance': wallet.balance,
                'makeup_cards': progress.makeup_cards,
                'full_attendance_awarded': full_attendance_just_awarded,
            }
        })


class QualificationOptionsView(APIView):
    permission_classes=[IsClubAccountAuthenticated]
    def get(self,request):
        from .qualifications import options_payload
        data=options_payload()
        if request.account.account_type!='STAFF':
            for scope in data['scopes']:
                for grade in scope['grades']:grade.pop('commission_rate',None)
        return Response({'code':0,'data':data})
