"""Independent platform upload of public store branding."""
from django.core.exceptions import ValidationError as ImageValidationError
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from .hosts import is_admin_host
from .media import save_store_logo_upload
from .models import PlatformAuditEvent
from .platform_auth import IsPlatformAuthenticated, PlatformAuthentication
from .platform_permissions import platform_may


class PlatformStoreLogoUploadView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def post(self, request):
        if not is_admin_host(request.get_host()) or not platform_may(request.platform_account, 'stores:manage'):
            return Response({'code': 403, 'msg': '需要平台超级管理员权限'}, status=403)
        try:
            upload = serializers.ImageField().run_validation(request.FILES.get('image'))
        except ImageValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
        if upload.size > 10 * 1024 * 1024:
            raise serializers.ValidationError('图片大小不能超过 10MB')
        try:
            data = save_store_logo_upload(upload)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        from common.media import build_media_url_from_path
        data['url'] = build_media_url_from_path(request, data['url'])
        data['original_url'] = build_media_url_from_path(request, data['original_url'])
        PlatformAuditEvent.objects.create(account=request.platform_account, action='store.logo.upload',
                                          target_type='platform_logo', target_id=data['url'][:100],
                                          detail={'width': data['width'], 'height': data['height'],
                                                  'bytes': data['bytes']})
        return Response({'code': 0, 'data': data})
