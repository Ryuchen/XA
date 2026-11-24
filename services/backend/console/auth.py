from django.db import DatabaseError, InterfaceError
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError

from club_accounts.authentication import ClubAccountAuthenticationUnavailable
from club_accounts.permissions import IsClubAccountAuthenticated
from club_accounts.tokens import refresh_account_access_token

from .permissions import get_account_permissions, is_console_account
from stores.context import current_store
from stores.serializers import public_store


def _store_payload(store):
    return {'store_slug': store.slug, 'store': public_store(store)}


def _profile_payload(account):
    from identifiers.services import get_entity_number
    from rest_framework.exceptions import PermissionDenied
    from stores.context import current_database
    if current_store() is None or account._state.db != current_database():
        raise PermissionDenied('后台账号所属门店不一致，请重新登录')
    membership = getattr(account, 'admin_membership', None)
    roles = [r for r in membership.roles.all() if r.is_active] if membership else []
    role_list = [{'id': r.id, 'name': r.name, 'code': r.code} for r in roles]
    is_super_admin = any(role['code'] == 'super_admin' for role in role_list)
    return {
        'id': account.id,
        'entity_no': get_entity_number(account),
        'username': account.username,
        'nickname': account.nickname or account.username,
        'avatar': account.avatar_url or '',
        'phone': account.phone or '',
        'isWechatBound': bool(account.is_openid_bound and (account.openid or account.wechat_identities.exists())),
        'isPhoneVerified': account.is_phone_verified,
        'is_superuser': is_super_admin,
        'roles': role_list,
        # 兼容旧前端：取首个角色
        'role': role_list[0] if role_list else None,
        'permissions': sorted(get_account_permissions(account)),
    }


class ConsoleLoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    # 口令爆破的第一现场，必须限流（仅作用于 POST）。
    throttle_scope = 'login'

    def post(self, request):
        # The legacy URL must not bypass password and SMS verification.
        from .dual_auth import ConsolePasswordLoginView
        return ConsolePasswordLoginView().post(request)


class ConsoleRefreshView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        token = request.data.get('refresh')
        if not token:
            return Response({'code': 400, 'msg': '缺少 refresh token'})
        try:
            access_token = refresh_account_access_token(token, require_console=True)
        except (DatabaseError, InterfaceError) as exc:
            raise ClubAccountAuthenticationUnavailable() from exc
        except TokenError:
            # HTTP 状态码必须真的是 401：信封里的 code=401 只有业务代码看得懂，
            # 而浏览器 fetch 封装、网关、监控告警看的都是 HTTP 状态码。
            # 之前这里返回 HTTP 200，前端的「401 自动跳登录」拦截器完全不触发，
            # 用户 token 过期后只会看到一个静默失败的页面。
            return Response(
                {'code': 401, 'msg': '登录已过期，请重新登录'},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        return Response({'code': 0, 'data': {'token': access_token,
            **_store_payload(current_store())}})


class ConsoleProfileView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        if not is_console_account(request.account):
            return Response({'code': 401, 'msg': '后台访问权限已停用，请重新登录'}, status=401)
        return Response({'code': 0, 'data': _profile_payload(request.account)})
