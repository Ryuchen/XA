"""Platform-account WeChat binding uses only the control database."""
import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError, transaction
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError

from .context import store_context
from .database_identity import lock_store_registration
from .hosts import is_admin_host
from .media import encode_login_scene
from .models import AdminLoginHandoff, PlatformAuditEvent, PlatformWechatBindingIntent, PlatformWechatIdentity
from .platform_auth import IsPlatformAuthenticated, PlatformAuthentication, account_for_token


class PlatformWechatBindingView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]
    throttle_scope = 'login'

    def get(self, request):
        if not is_admin_host(request.get_host()):
            return Response({'code': 403, 'msg': '请从统一管理后台访问'}, status=403)
        bound = PlatformWechatIdentity.objects.filter(account=request.platform_account).exists()
        response = Response({'code': 0, 'data': {'isWechatBound': bound}})
        response['Cache-Control'] = 'no-store'
        return response

