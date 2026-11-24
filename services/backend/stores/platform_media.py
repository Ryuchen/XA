"""Independent platform upload of public store branding."""
from django.core.exceptions import ValidationError as ImageValidationError
from django.db import DatabaseError, transaction
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from .context import store_context
from .hosts import is_admin_host
from .media import save_store_background_upload, save_store_logo_upload
from .models import PlatformAuditEvent
from .platform_accounts import AccountChangeError, _locked_actor
from .platform_auth import IsPlatformAuthenticated, PlatformAuthentication
from .platform_permissions import platform_may
from .platform_store_setup import PlatformSetupBase, SetupError, _store


def _validated_image(request):
    try:
        upload = serializers.ImageField().run_validation(request.FILES.get('image'))
    except ImageValidationError as exc:
        raise serializers.ValidationError(exc.messages) from exc
    if upload.size > 10 * 1024 * 1024:
        raise serializers.ValidationError('图片大小不能超过 10MB')
    return upload


class PlatformStoreLogoUploadView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def post(self, request):
        if not is_admin_host(request.get_host()) or not platform_may(request.platform_account, 'stores:manage'):
            return Response({'code': 403, 'msg': '需要平台超级管理员权限'}, status=403)
        upload = _validated_image(request)
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


class PlatformStoreBackgroundUploadView(PlatformSetupBase):
    def post(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        upload = _validated_image(request)
        try:
            with transaction.atomic(using='default'):
                actor = _locked_actor(request.auth)
                store = _store(pk, lock=True)
                with store_context(store):
                    data = save_store_background_upload(upload)
                PlatformAuditEvent.objects.create(account=actor, action='store.background.upload',
                    target_store_slug=store.slug, target_type='store', target_id=str(store.pk),
                    detail={field: data[field] for field in ('url', 'width', 'height', 'bytes')})
        except (SetupError, AccountChangeError) as exc:
            return Response({'code': exc.status, 'errorCode': getattr(exc, 'code', 'SETUP_UNAVAILABLE'),
                             'msg': str(exc)}, status=exc.status)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        except (DatabaseError, OSError):
            return Response({'code': 503, 'msg': '门店背景暂不可保存，请稍后重试'}, status=503)
        return Response({'code': 0, 'data': data})
