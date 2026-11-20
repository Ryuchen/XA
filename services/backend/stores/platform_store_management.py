"""Independent platform super-admin access to the existing store lifecycle."""
from collections.abc import Mapping

from django.db import DatabaseError
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .hosts import is_admin_host
from .models import PlatformAuditEvent
from .platform_auth import IsPlatformAuthenticated, PlatformAuthentication
from .platform_permissions import platform_may
from .views import StoreDetailView, StoreManageView, StoreResumeProvisionView


class PlatformStoreManagementBase(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def allowed(self, request):
        return is_admin_host(request.get_host()) and platform_may(request.platform_account, 'stores:manage')

    def denied(self):
        return Response({'code': 403, 'errorCode': 'PLATFORM_FORBIDDEN', 'msg': '需要平台超级管理员权限'}, status=403)

    def perform(self, request, action, callback, *, slug='', target_id=''):
        if not self.allowed(request):
            return self.denied()
        if not isinstance(request.data, Mapping):
            return Response({'code': 400, 'msg': '门店请求格式无效'}, status=400)
        # Never put request bodies, credentials, database connection details or
        # arbitrary error strings into the platform audit record.
        field_names = sorted(set(request.data).intersection({
            'slug', 'name', 'entity_prefix', 'provider_name_prefix', 'currency_name', 'database_alias', 'status', 'logo_url',
            'background_url', 'introduction',
            'boss_url', 'provider_url', 'admin_url', 'admin_username', 'admin_phone', 'admin_nickname',
        }))
        try:
            PlatformAuditEvent.objects.create(account=request.platform_account, action=f'store.{action}_requested',
                target_store_slug=slug[:48], target_type='store', target_id=str(target_id)[:100],
                detail={'fields': field_names})
        except DatabaseError:
            return Response({'code': 503, 'msg': '平台审计不可用，未执行操作'}, status=503)
        try:
            response = callback()
        except ValidationError as exc:
            try:
                PlatformAuditEvent.objects.create(account=request.platform_account, action=f'store.{action}_result',
                    target_store_slug=slug[:48], target_type='store', target_id=str(target_id)[:100],
                    detail={'status_code': exc.status_code, 'error_code': 'VALIDATION_ERROR'})
            except DatabaseError:
                pass  # The previously recorded request remains available for review.
            labels = {'slug': '门店标识', 'name': '门店名称', 'entity_prefix': '编号前缀', 'provider_name_prefix': '陪玩展示简称',
                      'currency_name': '店币名称', 'database_alias': '业务库', 'status': '门店状态',
                      'background_url': '门店背景', 'introduction': '门店介绍',
                      'boss_url': '老板端入口', 'provider_url': '陪玩端入口', 'admin_url': '后台入口',
                      'admin_username': '管理员账号', 'admin_password': '管理员密码',
                      'admin_phone': '管理员手机号', 'admin_nickname': '管理员昵称', 'non_field_errors': '门店信息'}
            if isinstance(exc.detail, Mapping):
                key = next(iter(exc.detail), '')
                detail = exc.detail.get(key, '')
                if isinstance(detail, (tuple, list)) and detail:
                    detail = detail[0]
                if key in labels and isinstance(detail, str):
                    return Response({'code': 400, 'msg': f'{labels[key]}：{detail}'}, status=400)
            return Response({'code': 400, 'msg': '请检查门店信息后重试'}, status=400)
        except APIException:
            raise
        result = response.data if isinstance(response.data, dict) else {}
        data = result.get('data') if isinstance(result.get('data'), dict) else {}
        try:
            PlatformAuditEvent.objects.create(account=request.platform_account, action=f'store.{action}_result',
                target_store_slug=str(data.get('slug') or slug)[:48], target_type='store',
                target_id=str(data.get('id') or target_id)[:100],
                detail={'status_code': response.status_code, 'error_code': str(result.get('errorCode') or '')[:80]})
        except DatabaseError:
            return Response({'code': 503, 'errorCode': 'STORE_RESULT_UNCERTAIN',
                'msg': '操作结果需要核对，请刷新门店列表后检查'}, status=503)
        return response


class PlatformStoreManagementView(PlatformStoreManagementBase):
    def get(self, request):
        if not self.allowed(request):
            return self.denied()
        return StoreManageView().get(request)

    def post(self, request):
        slug = request.data.get('slug', '') if isinstance(request.data, Mapping) else ''
        return self.perform(request, 'create', lambda: StoreManageView().post(request),
                            slug=slug if isinstance(slug, str) else '')


class PlatformStoreManagementDetailView(PlatformStoreManagementBase):
    def patch(self, request, pk):
        if not self.allowed(request):
            return self.denied()
        if not isinstance(request.data, Mapping) or not isinstance(request.data.get('expected_updated_at'), str):
            return Response({'code': 400, 'msg': '请刷新门店信息后重试'}, status=400)
        return self.perform(request, 'update', lambda: StoreDetailView().patch(request, pk), target_id=pk)


class PlatformStoreManagementResumeView(PlatformStoreManagementBase):
    def post(self, request, pk):
        return self.perform(request, 'initialize', lambda: StoreResumeProvisionView().post(request, pk), target_id=pk)
