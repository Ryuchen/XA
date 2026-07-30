from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError

from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated
from club_accounts.services import authenticate_account
from club_accounts.tokens import issue_account_tokens, refresh_account_access_token

from .permissions import get_account_permissions, is_console_account


def _profile_payload(account):
    membership = getattr(account, 'admin_membership', None)
    roles = list(membership.roles.all()) if membership else []
    role_list = [{'id': r.id, 'name': r.name, 'code': r.code} for r in roles]
    is_super_admin = any(role['code'] == 'super_admin' for role in role_list)
    return {
        'id': account.id,
        'username': account.username,
        'nickname': account.nickname or account.username,
        'avatar': account.avatar_url or '',
        'is_superuser': is_super_admin,
        'roles': role_list,
        # 兼容旧前端：取首个角色
        'role': role_list[0] if role_list else None,
        'permissions': sorted(get_account_permissions(account)),
    }


class ConsoleLoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        username = (request.data.get('username') or '').strip()
        password = request.data.get('password') or ''
        if not username or not password:
            return Response({'code': 400, 'msg': '请输入账号和密码'})

        account = authenticate_account(
            username,
            password,
            account_type=ClubAccount.AccountType.STAFF,
        )
        if account is None:
            return Response({'code': 400, 'msg': '账号或密码错误'})
        if not is_console_account(account):
            return Response({'code': 403, 'msg': '该账号无后台访问权限'})

        access_token, refresh_token = issue_account_tokens(account)
        return Response({
            'code': 0,
            'data': {
                'token': access_token,
                'refresh': refresh_token,
                'profile': _profile_payload(account),
            },
        })


class ConsoleRefreshView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        token = request.data.get('refresh')
        if not token:
            return Response({'code': 400, 'msg': '缺少 refresh token'})
        try:
            access_token = refresh_account_access_token(token)
        except TokenError:
            return Response({'code': 401, 'msg': '登录已过期，请重新登录'})
        return Response({'code': 0, 'data': {'token': access_token}})


class ConsoleProfileView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    def get(self, request):
        if not is_console_account(request.account):
            return Response({'code': 403, 'msg': '无后台访问权限'})
        return Response({'code': 0, 'data': _profile_payload(request.account)})
