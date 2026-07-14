from django.contrib.auth import authenticate, get_user_model
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from .permissions import get_user_permissions, is_console_user

User = get_user_model()


def _profile_payload(user):
    membership = getattr(user, 'admin_membership', None)
    role = membership.role if membership else None
    return {
        'id': user.id,
        'username': user.username,
        'nickname': user.nickname or user.username,
        'avatar': user.avatar_url or '',
        'is_superuser': user.is_superuser,
        'role': {
            'id': role.id,
            'name': role.name,
            'code': role.code,
        } if role else None,
        'permissions': sorted(get_user_permissions(user)),
    }


class ConsoleLoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        username = (request.data.get('username') or '').strip()
        password = request.data.get('password') or ''
        if not username or not password:
            return Response({'code': 400, 'msg': '请输入账号和密码'})

        user = authenticate(request, username=username, password=password)
        if user is None:
            return Response({'code': 400, 'msg': '账号或密码错误'})
        if not user.is_active:
            return Response({'code': 403, 'msg': '账号已被禁用'})
        if not is_console_user(user):
            return Response({'code': 403, 'msg': '该账号无后台访问权限'})

        refresh = RefreshToken.for_user(user)
        return Response({
            'code': 0,
            'data': {
                'token': str(refresh.access_token),
                'refresh': str(refresh),
                'profile': _profile_payload(user),
            },
        })


class ConsoleRefreshView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        token = request.data.get('refresh')
        if not token:
            return Response({'code': 400, 'msg': '缺少 refresh token'})
        try:
            refresh = RefreshToken(token)
        except TokenError:
            return Response({'code': 401, 'msg': '登录已过期，请重新登录'})
        return Response({'code': 0, 'data': {'token': str(refresh.access_token)}})


class ConsoleProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not is_console_user(request.user):
            return Response({'code': 403, 'msg': '无后台访问权限'})
        return Response({'code': 0, 'data': _profile_payload(request.user)})
