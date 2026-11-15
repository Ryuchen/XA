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

    def post(self, request):
        if not is_admin_host(request.get_host()):
            return Response({'code': 403, 'msg': '请从统一管理后台访问'}, status=403)
        if not settings.WECHAT_MINIAPP_APPID or not settings.WECHAT_MINIAPP_SECRET:
            return Response({'code': 503, 'errorCode': 'WECHAT_NOT_CONFIGURED', 'msg': '微信登录服务尚未配置'}, status=503)
        secret = secrets.token_urlsafe(32)
        try:
            with transaction.atomic(using='default'):
                lock_store_registration()
                account = account_for_token(request.auth)
                if PlatformWechatIdentity.objects.filter(account=account).exists():
                    return Response({'code': 409, 'errorCode': 'ACCOUNT_CONFLICT', 'msg': '平台账号已绑定微信'}, status=409)
                session = AdminLoginHandoff.objects.create(purpose='platform_bind', platform_account=account,
                    poll_token_hash=hashlib.sha256(secret.encode()).hexdigest(), expires_at=timezone.now()+timedelta(minutes=5))
                PlatformWechatBindingIntent.objects.create(handoff=session, account=account, security_version=account.security_version)
                PlatformAuditEvent.objects.create(account=account, action='auth.wechat_binding_started',
                    target_type='platform_account', target_id=str(account.pk))
        except TokenError:
            return Response({'code': 401, 'msg': '平台登录已失效，请重新登录'}, status=401)
        except (DatabaseError, ValueError):
            return Response({'code': 503, 'msg': '平台微信绑定暂不可用'}, status=503)
        try:
            from users.web_login import create_bridge_code
            with store_context(None):
                image, launch = create_bridge_code(session)
                ticket = encode_login_scene(session.ticket)
        except (OSError, ValueError):
            return Response({'code': 503, 'errorCode': 'WECHAT_UNAVAILABLE', 'msg': '微信登录码暂时无法获取'}, status=503)
        response = Response({'code': 0, 'data': {'ticket': ticket, 'poll_token': secret, 'qr_image': image,
            'launch_url': launch, 'expires_in': 300, 'requiresWechatBridge': True}})
        response['Cache-Control'] = 'no-store'
        return response
