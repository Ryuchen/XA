import calendar
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, time, timedelta

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Case, Count, ExpressionWrapper, F, FloatField, IntegerField, Q, Sum, Value, When
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
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
from site_messages.utils import create_message
from wallet.models import Transaction, Wallet
from wallet.services import get_wallet
from common.media import build_media_url

from .models import (
    Achievement, CheckinGift, CheckinMonthProgress, CheckinRecord, CheckinRuleConfig,
    CustomerGameProfile, EscortProfile, EscortSchedule, ProviderPassPurchase,
)

User = get_user_model()

ROLE_REVERSE_MAP = {
    ClubAccount.AccountType.BOSS: 'customer',
    ClubAccount.AccountType.PROVIDER: 'provider',
    ClubAccount.AccountType.STAFF: 'support',
}

WECHAT_API_TIMEOUT = 5


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
        raise ValueError(data.get('errmsg') or '微信登录失败')
    openid = data.get('openid')
    if not openid:
        raise ValueError('未获取到 openid')
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
        raise ValueError(data.get('errmsg') or '获取 access_token 失败')
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
        raise ValueError(data.get('errmsg') or '获取手机号失败')
    return (data.get('phone_info') or {}).get('purePhoneNumber', '')


class WechatLoginView(APIView):
    """老板端微信快捷登录：仅服务 CUSTOMER 角色。

    陪玩改走 AccountLoginView（账号密码），客服走后台 /api/admin/auth/login，
    故此端点不再接收 role / bindCode，openid 命中的用户必须是老板。
    """

    permission_classes = [AllowAny]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @staticmethod
    def _resolve_customer(openid, phone, defaults, allow_phone_binding):
        """按稳定微信身份解析老板，手机号仅用于真实微信登录的首次绑定。

        匹配顺序固定为：openid -> 唯一手机号老板 -> 新建。手机号冲突时不
        猜测归属，交由后台人工整理，避免把两个历史老板错误合并。
        """
        with transaction.atomic():
            account = ClubAccount.objects.select_for_update().filter(openid=openid).first()
            if account is not None:
                return account, False, 'openid'

            legacy_openid_user = (
                User.objects.select_for_update()
                .filter(openid=openid)
                .first()
            )
            if legacy_openid_user is not None:
                account = get_or_create_account_for_legacy_user(legacy_openid_user)
                return account, False, 'openid'

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
                    raise ValueError('该手机号关联了多个老板账号，请联系客服处理')
                if account_candidates or legacy_candidates:
                    legacy_user = legacy_candidates[0] if legacy_candidates else None
                    account = (
                        account_candidates[0]
                        if account_candidates
                        else get_or_create_account_for_legacy_user(legacy_user)
                    )
                    account.openid = openid
                    account.is_openid_bound = True
                    account.is_phone_verified = True
                    account.save(update_fields=[
                        'openid', 'is_openid_bound', 'is_phone_verified', 'updated_at',
                    ])
                    if legacy_user is None:
                        legacy_user = User.objects.filter(
                            pk=account.legacy_mapping.legacy_user_id,
                        ).first()
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
                    account = ClubAccount.objects.create_account(
                        password=None,
                        openid=openid,
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
                account = ClubAccount.objects.select_for_update().get(openid=openid)
                return account, False, 'openid'

    def post(self, request):
        code = request.data.get('code')
        phone_code = (request.data.get('phoneCode') or '').strip()
        nickname = (request.data.get('nickname') or '').strip()[:50]
        avatar_file = request.FILES.get('avatar')

        if not code:
            return Response({'code': 400, 'msg': '缺少微信登录code'})

        if settings.WECHAT_MOCK_LOGIN:
            openid = f"wx_mock_{code[:10]}"
            phone = f"138{code[:8].zfill(8)[-8:]}" if phone_code else ''
        else:
            if not settings.WECHAT_MINIAPP_APPID or not settings.WECHAT_MINIAPP_SECRET:
                return Response({'code': 503, 'msg': '微信登录尚未完成配置，请联系管理员'})
            try:
                openid = _wechat_code2session(code)
                phone = _wechat_get_phone(phone_code) if phone_code else ''
            except (ValueError, urllib.error.URLError) as exc:
                return Response({'code': 400, 'msg': f'微信登录失败：{exc}'})

        try:
            account, created, bind_status = self._resolve_customer(
                openid=openid,
                phone=phone,
                allow_phone_binding=not settings.WECHAT_MOCK_LOGIN,
                defaults={
                    'username': f"xa_{uuid.uuid4().hex[:8]}",
                    'account_type': ClubAccount.AccountType.BOSS,
                    'phone': phone,
                    'nickname': nickname,
                    'is_phone_verified': bool(phone),
                    'is_openid_bound': True,
                },
            )
        except ValueError as exc:
            return Response({'code': 409, 'msg': str(exc)})

        # 老板端仅允许老板登录：已存在的非老板账号（陪玩/客服/管理员）拒绝从此入口进入。
        if not created and account.account_type != ClubAccount.AccountType.BOSS:
            return Response({'code': 403, 'msg': '该微信号非老板账号，请使用对应端登录'})

        if not account.is_active or not account.can_login:
            return Response({'code': 403, 'msg': '该老板账号已被禁用，请联系客服'})

        updated_fields = []
        if phone and account.phone != phone:
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

        legacy_user = User.objects.filter(
            pk=account.legacy_mapping.legacy_user_id,
        ).first()
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
                'userInfo': {
                    'id': str(response_id),
                    'username': account.username,
                    'nickname': account.nickname or account.username,
                    'avatar': account.avatar_url or '',
                    'role': 'customer',
                    'backendRole': account.account_type,
                    'openid': account.openid,
                    'phone': account.phone,
                    'bindCode': None,
                    'bindStatus': bind_status,
                }
            }
        })


class AccountLoginView(APIView):
    """陪玩端账号密码登录：仅服务 PROVIDER 角色。

    陪玩账号由客服在后台开户（见 console 的创建陪玩账号接口），此端点只负责校验登录，
    不再接收 role / bindCode，也不会改写既有账号角色。
    开发期 WECHAT_MOCK_LOGIN=True 时按用户名自动创建陪玩账号、不强校验密码；
    生产环境用 ClubAccount 密码哈希校验真实密码。
    """
    permission_classes = [AllowAny]

    def post(self, request):
        username = (request.data.get('username') or '').strip()
        password = request.data.get('password') or ''

        if not username:
            return Response({'code': 400, 'msg': '请输入账号'})

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

        access_token, refresh_token = issue_account_tokens(account)

        return Response({
            'code': 0,
            'msg': 'success',
            'data': {
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
            'avatar': account.avatar_url or '',
            'game_region': account.game_region,
            'game_nickname': account.game_nickname,
            'game_uid': account.game_uid,
            'game_profiles': game_profiles,
        }

    def get(self, request):
        return Response({'code': 0, 'data': self._serialize(request.account)})

    def patch(self, request):
        account = request.account
        legacy_user = request.legacy_user
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
                    value = (item.get(source) or '').strip()
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
            setattr(account, field, value)
            updated_fields.append(field)

        if updated_fields:
            account.save(update_fields=updated_fields)
            if legacy_user:
                for field in updated_fields:
                    setattr(legacy_user, field, getattr(account, field))
                legacy_user.save(update_fields=updated_fields)

        return Response({'code': 0, 'msg': '已更新', 'data': self._serialize(account)})


class EscortProfileListView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        game = request.query_params.get('game', '').strip()
        game_category_id = request.query_params.get('game_category', '').strip()
        service_item_id = request.query_params.get('service_item', '').strip()
        profiles = EscortProfile.objects.filter(
            status=EscortProfile.Status.AVAILABLE,
            is_verified=True,
        ).select_related('account', 'user').prefetch_related(
            'game_categories',
            'service_items',
        )
        if service_item_id.isdigit():
            # 最细粒度：仅展示可接该服务项的陪玩（未勾选该服务项的陪玩不出现）
            profiles = profiles.filter(service_items__id=int(service_item_id)).distinct()
        elif game_category_id.isdigit():
            # 精准筛选：仅展示可接该游戏类目的陪玩（未配置可接游戏的陪玩不出现）
            profiles = profiles.filter(game_categories__id=int(game_category_id)).distinct()
        elif game:
            profiles = profiles.filter(
                Q(service_area__icontains=game)
                | Q(user__received_orders__service__game_category__name__iexact=game)
            ).distinct()

        profiles = profiles.annotate(
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

        data = []
        for p in profiles:
            identity = p.account or p.user
            item = {
                'id': p.account_id or p.user_id,
                'nickname': p.display_name or identity.nickname or identity.username,
                'avatar': identity.avatar_url or '',
                'rank': p.rank_tier,
                'rating': float(p.rating_avg),
                'ratingCount': p.rating_count,
                'favorableRate': round(float(p.favorable_rate or 0), 1),
                'orderCount': p.completed_order_count,
                'winRate': float(p.win_rate),
                'pricePerHour': p.price_per_hour or 0,
                'bio': p.bio,
                'city': p.city,
                'status': p.status,
                'is_verified': p.is_verified,
                'voice_card_url': build_media_url(request, p.voice_card),
                'game_category_ids': [c.id for c in p.game_categories.all()],
                'service_item_ids': [s.id for s in p.service_items.all()],
            }
            data.append(item)

        return Response({'code': 0, 'data': data})


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
            created_at__date=timezone.now().date(),
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

        active_orders = (
            Order.objects.filter(
                Q(provider_account=request.account)
                | Q(providers__provider_account=request.account),
                status__in=[Order.Status.GRABBED, Order.Status.IN_SERVICE],
            )
            .distinct()
            .count()
        )

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
    """陪玩通行证：余额购买，决定公共单池的提前可见时间。"""

    permission_classes = [IsClubAccountAuthenticated]
    DAILY_PRICES = {
        EscortProfile.PassTier.BLACK: 5000,
        EscortProfile.PassTier.GOLD: 3000,
        EscortProfile.PassTier.SILVER: 1000,
        EscortProfile.PassTier.BRONZE: 200,
    }
    DELAYS = {
        EscortProfile.PassTier.BLACK: 0,
        EscortProfile.PassTier.GOLD: 30,
        EscortProfile.PassTier.SILVER: 60,
        EscortProfile.PassTier.BRONZE: 120,
        '': 300,
    }

    def _profile(self, request):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return None
        return EscortProfile.objects.filter(account=request.account).first()

    def _data(self, request, profile):
        wallet = get_wallet(account=request.account, user=request.legacy_user)
        products = []
        for tier, label in EscortProfile.PassTier.choices:
            daily = self.DAILY_PRICES[tier]
            products.append({
                'tier': tier, 'name': label, 'daily_price': daily,
                'delay_seconds': self.DELAYS[tier],
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
            'no_pass_delay_seconds': self.DELAYS[''],
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

    def post(self, request):
        profile = self._profile(request)
        if profile is None:
            return Response({'code': 403, 'msg': '仅陪玩可购买通行证'})
        tier = (request.data.get('tier') or '').upper()
        if tier not in self.DAILY_PRICES:
            return Response({'code': 400, 'msg': '请选择有效通行证'})
        try:
            days = int(request.data.get('days', 1))
        except (TypeError, ValueError):
            return Response({'code': 400, 'msg': '购买天数非法'})
        if days != 1:
            return Response({'code': 400, 'msg': '通行证按日采购，每次有效期 1 天'})

        daily_price = self.DAILY_PRICES[tier]
        original_amount = daily_price * days
        paid_amount = original_amount
        now = timezone.now()

        with transaction.atomic():
            locked_profile = EscortProfile.objects.select_for_update().get(pk=profile.pk)
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
            ProviderPassPurchase.objects.create(
                provider=request.legacy_user,
                account=request.account,
                tier=tier,
                days=days,
                daily_price=daily_price, original_amount=original_amount,
                discount_amount=original_amount - paid_amount,
                paid_amount=paid_amount, starts_at=starts_at,
                expires_at=expires_at, transaction=tx,
            )
            locked_profile.pass_tier = tier
            locked_profile.pass_expires_at = expires_at
            locked_profile.save(update_fields=['pass_tier', 'pass_expires_at'])

        return Response({
            'code': 0, 'msg': '通行证购买成功',
            'data': self._data(request, locked_profile),
        })


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
        return EscortProfile.objects.filter(account=request.account).first()

    def _file_url(self, field, request=None):
        return build_media_url(request, field)

    def _serialize(self, profile, request=None):
        return {
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

        # 可编辑文本字段及最大长度
        editable = {
            'display_name': 50,
            'bio': 500,
            'city': 50,
            'service_area': 255,
        }
        updated_fields = []
        for field, max_len in editable.items():
            if field not in request.data:
                continue
            value = (request.data.get(field) or '').strip()
            if len(value) > max_len:
                return Response({'code': 400, 'msg': f'{field} 长度不能超过 {max_len}'})
            if field == 'display_name' and not value:
                return Response({'code': 400, 'msg': '昵称不能为空'})
            setattr(profile, field, value)
            updated_fields.append(field)

        if 'gender' in request.data:
            gender = (request.data.get('gender') or '').strip().upper()
            if gender not in self.GENDER_VALUES:
                return Response({'code': 400, 'msg': '性别取值非法'})
            profile.gender = gender
            updated_fields.append('gender')

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
            setattr(profile, field, upload)
            updated_fields.append(field)

        if updated_fields:
            profile.save(update_fields=updated_fields)

        # 可接服务项：陪玩自助勾选。传入服务项 ID 列表后覆盖设置，
        # 并同步把这些服务项所属游戏并入 game_categories（勾服务项即隐含可接该游戏）。
        if 'service_items' in request.data:
            raw = request.data.get('service_items')
            if isinstance(raw, str):
                # multipart/表单可能以逗号分隔字符串传入
                raw = [seg for seg in raw.split(',') if seg.strip()]
            elif not isinstance(raw, (list, tuple)):
                raw = [raw]
            item_ids = [int(v) for v in raw if str(v).strip().isdigit()]
            items = list(ServiceItem.objects.filter(id__in=item_ids))
            profile.service_items.set(items)
            game_ids = {item.game_category_id for item in items if item.game_category_id}
            profile.game_categories.set(list(game_ids))

        return Response({'code': 0, 'msg': '已更新', 'data': self._serialize(profile, request)})

    # Taro.uploadFile 使用 POST 上传 multipart；与 PATCH 共用同一更新逻辑。
    post = patch


class EscortSkillOptionsView(APIView):
    """陪玩「我的技能」可选项树：按游戏分组返回其下启用的服务项，供陪玩勾选可接项目。"""
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        items = (
            ServiceItem.objects.filter(is_active=True, game_category__isnull=False)
            .select_related('game_category')
            .order_by('game_category__sort_order', 'game_category_id', 'sort_order', 'id')
        )
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
            })
        return Response({'code': 0, 'data': [groups[gid] for gid in order]})


class BindCodeGenerateView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def post(self, request):
        if request.account.account_type != ClubAccount.AccountType.STAFF:
            return Response({'code': 403, 'msg': '仅客服可生成绑定码'})

        role = request.data.get('role', 'provider')
        prefix = 'PW' if role == 'provider' else 'KF'
        code = f"{prefix}{uuid.uuid4().hex[:6].upper()}"

        return Response({'code': 0, 'data': {'bindCode': code}})


class CustomerAchievementView(APIView):
    """老板成就馆：成就清单由后台配置，根据已完成订单实时计算解锁状态。"""
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        completed = Order.objects.filter(
            customer_account=request.account,
            status=Order.Status.COMPLETED,
        )
        agg = completed.aggregate(order_count=Count('id'), total=Sum('amount'))
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


def _reward_for_seq(seq_in_month):
    """读取后台配置的当月签到礼物；未配置时回落到原七日循环奖励。"""
    gift = CheckinGift.objects.filter(checkin_day=seq_in_month, is_active=True).first()
    if gift:
        return gift.reward_amount
    index = (seq_in_month - 1) % len(MONTHLY_CHECKIN_REWARDS)
    return MONTHLY_CHECKIN_REWARDS[index]


def _daily_paid_amount(account, target_date):
    """按订单创建日统计当日已支付且未退款的兴安币消费。"""
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(target_date, time.min), tz)
    end = start + timedelta(days=1)
    return Order.objects.filter(
        customer_account=account,
        payment_status=Order.PaymentStatus.PAID,
        created_at__gte=start,
        created_at__lt=end,
    ).aggregate(total=Sum('amount'))['total'] or 0


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
        rewards = []
        for day in range(1, calendar.monthrange(today.year, today.month)[1] + 1):
            gift = gifts.get(day)
            rewards.append({
                'seq': day,
                'amount': gift.reward_amount if gift else _reward_for_seq(day),
                'name': gift.name if gift else f'第{day}天签到礼物',
                'description': gift.description if gift else '',
                'icon': gift.icon if gift else '🎁',
            })
        next_seq = checked_count if today_checked else checked_count + 1
        next_gift = gifts.get(next_seq)
        days_in_month = calendar.monthrange(today.year, today.month)[1]

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
                'next_reward': _reward_for_seq(next_seq),
                'next_gift': {
                    'name': next_gift.name if next_gift else f'第{next_seq}天签到礼物',
                    'description': next_gift.description if next_gift else '',
                    'icon': next_gift.icon if next_gift else '🎁',
                },
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
                gift = CheckinGift.objects.filter(checkin_day=seq_in_month, is_active=True).first()
                reward_amount = gift.reward_amount if gift else _reward_for_seq(seq_in_month)

                CheckinRecord.objects.create(
                    user=request.legacy_user,
                    account=request.account,
                    checkin_date=target_date,
                    seq_in_month=seq_in_month,
                    reward_amount=reward_amount,
                    gift_name=gift.name if gift else f'第{seq_in_month}天签到礼物',
                    gift_icon=gift.icon if gift else '🎁',
                    is_makeup=is_makeup,
                )

                wallet = get_wallet(
                    account=request.account,
                    user=request.legacy_user,
                    for_update=True,
                )
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
                        remark=f'月度签到礼物：{gift.name if gift else seq_in_month}',
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
                'gift': {
                    'name': gift.name if gift else f'第{seq_in_month}天签到礼物',
                    'description': gift.description if gift else '',
                    'icon': gift.icon if gift else '🎁',
                },
                'seq_in_month': seq_in_month,
                'checked_count': checked_count,
                'balance': wallet.balance,
                'makeup_cards': progress.makeup_cards,
                'full_attendance_awarded': full_attendance_just_awarded,
            }
        })
