from django.db import DatabaseError, IntegrityError, transaction
from django.core.management.base import CommandError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from collections.abc import Mapping
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .context import current_store
from .models import Store
from .permissions import IsPlatformStoreManager, may_manage_stores
from .serializers import (StoreCreateSerializer, StoreResumeSerializer, StoreUpdateSerializer,
                          configured_database_aliases, public_store)


def managed_store(store):
    return {**public_store(store), 'database_alias': store.database_alias,
            'initialized_at': store.initialized_at,
            'updated_at': store.updated_at,
            'initialization_required': store.initialized_at is None}


def _keep_uninitialized_store_closed(store):
    # Another recovery may have completed while this one was failing.
    Store.objects.filter(pk=store.pk, initialized_at__isnull=True).update(
        status=Store.Status.PROVISIONING, updated_at=timezone.now())


class CurrentStoreView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        from .permissions import is_platform_session
        store = current_store()
        allowed = may_manage_stores(getattr(request, 'account', None)) or is_platform_session(request)
        return Response({'code': 0, 'data': {
            **public_store(store), 'can_manage_stores': allowed,
            'is_platform_admin': allowed,
        }})


class AvailableStoresView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        from .permissions import is_platform_session
        from .dynamic_database import alias_for_slug, enabled, materialize
        if enabled():
            for candidate in Store.objects.filter(status=Store.Status.ACTIVE, initialized_at__isnull=False):
                if candidate.database_alias == alias_for_slug(candidate.slug):
                    materialize(candidate.slug, expected_alias=candidate.database_alias)
        stores = Store.objects.filter(
            status=Store.Status.ACTIVE, initialized_at__isnull=False,
            database_alias__in=['default', *configured_database_aliases()],
        )
        if not may_manage_stores(getattr(request, 'account', None)) and not is_platform_session(request):
            stores = stores.filter(pk=current_store().pk)
        return Response({'code': 0, 'data': {'results': [public_store(store) for store in stores]}})


class StoreManageView(APIView):
    permission_classes = [IsPlatformStoreManager]

    def get(self, request):
        from .dynamic_database import configured
        used = set(Store.objects.values_list('database_alias', flat=True))
        from .database_identity import assert_distinct_store_database
        available = []
        for alias in configured_database_aliases():
            if alias in used:
                continue
            try:
                assert_distinct_store_database(alias)
            except ValueError:
                continue
            available.append(alias)
        return Response({'code': 0, 'data': {
            'results': [managed_store(store) for store in Store.objects.all()],
            'available_database_aliases': available,
            'dynamic_database_capability': configured(),
            'resume_initialization_capability': 'OWNERSHIP_V1',
        }})

    def post(self, request):
        unknown = set(request.data) - set(StoreCreateSerializer.Meta.fields)
        if unknown:
            return Response({'code': 400, 'msg': '包含不支持的门店字段'}, status=400)
        serializer = StoreCreateSerializer(data=request.data, context={'request': request})
        try:
            with transaction.atomic(using='default'):
                from .database_identity import lock_store_registration
                lock_store_registration()
                serializer.is_valid(raise_exception=True)
                admin = {field: serializer.validated_data.pop(field, None) for field in
                         ('admin_username', 'admin_password', 'admin_phone', 'admin_nickname')}
                store = serializer.save(status=Store.Status.PROVISIONING)
        except ValueError:
            return Response({'code': 503, 'msg': '平台门店目录暂不可用，无法安全登记'}, status=503)
        except IntegrityError:
            return Response({'code': 409, 'msg': '门店标识或数据库已被使用，请刷新后重试'}, status=409)
        if admin['admin_username']:
            from .provisioning import create_database, initialize_store
            try:
                create_database(store.database_alias, admin_username=admin['admin_username'])
                initialize_store(store, admin_username=admin['admin_username'],
                                 admin_password=admin['admin_password'], admin_phone=admin['admin_phone'],
                                 admin_nickname=admin['admin_nickname'], new_database_created=True)
            except CommandError as exc:
                _keep_uninitialized_store_closed(store)
                return Response({'code': 409, 'msg': str(exc),
                                 'errorCode': 'STORE_INITIALIZATION_REQUIRES_REVIEW'}, status=409)
            except Exception:
                _keep_uninitialized_store_closed(store)
                return Response({'code': 500, 'msg': '门店登记已保存，但初始化未完成，请由平台运维检查后继续',
                                 'errorCode': 'STORE_INITIALIZATION_FAILED'}, status=500)
            return Response({'code': 0, 'msg': '门店与首位管理员已初始化',
                             'data': managed_store(store)}, status=201)
        return Response({'code': 0, 'msg': '门店已登记，请由部署人员执行 provision_store 完成初始化',
                         'data': managed_store(store)}, status=201)


class StoreDetailView(APIView):
    permission_classes = [IsPlatformStoreManager]

    def patch(self, request, pk):
        if not isinstance(request.data, Mapping):
            return Response({'code': 400, 'msg': '门店请求格式无效'}, status=400)
        expected = request.data.get('expected_updated_at')
        data = request.data.copy()
        data.pop('expected_updated_at', None)
        if expected is not None:
            from django.utils.dateparse import parse_datetime
            try:
                expected = parse_datetime(expected) if isinstance(expected, str) else None
            except ValueError:
                expected = None
            if expected is None or timezone.is_naive(expected):
                return Response({'code': 400, 'msg': '请刷新门店信息后重试'}, status=400)
        unknown = set(data) - set(StoreUpdateSerializer.Meta.fields)
        if unknown:
            return Response({'code': 400, 'msg': '门店标识和数据库不允许修改'}, status=400)
        try:
            with transaction.atomic(using='default'):
                store = get_object_or_404(Store.objects.select_for_update(), pk=pk)
                if expected is not None and store.updated_at != expected:
                    return Response({'code': 409, 'errorCode': 'STORE_CHANGED', 'msg': '门店信息已变化，请刷新后核对'}, status=409)
                serializer = StoreUpdateSerializer(store, data=data, partial=True, context={'request': request})
                serializer.is_valid(raise_exception=True)
                store = serializer.save()
                from django.conf import settings
                if getattr(settings, 'ISOLATED_CONTROL_PLANE', False) and store.initialized_at:
                    from .models import StoreRoutingRevision
                    from .routing_snapshot import capture, latest_revision, publish, schedule
                    revision, _ = StoreRoutingRevision.objects.select_for_update().get_or_create(store=store)
                    revision.version = max(revision.version, latest_revision(store)) + 1
                    revision.save(update_fields=['version'])
                    if store.status == Store.Status.DISABLED:
                        # The tombstone must be durable before the control-plane
                        # disable commits; otherwise an outage could revive it.
                        publish(capture(store, revision.version), require_current=True)
                    elif store.status == Store.Status.ACTIVE:
                        schedule(store, revision.version)
        except (DatabaseError, ValueError):
            return Response({'code': 503, 'errorCode': 'STORE_ROUTE_UPDATE_UNAVAILABLE',
                             'msg': '门店路由状态暂不可安全更新，请稍后重试'}, status=503)
        return Response({'code': 0, 'data': managed_store(store)})


class StoreResumeProvisionView(APIView):
    permission_classes = [IsPlatformStoreManager]

    def post(self, request, pk):
        if not isinstance(request.data, Mapping):
            return Response({'code': 400, 'msg': '初始化请求格式无效'}, status=400)
        unknown = set(request.data) - set(StoreResumeSerializer().fields)
        if unknown:
            return Response({'code': 400, 'msg': '包含不支持的初始化字段'}, status=400)
        serializer = StoreResumeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        store = get_object_or_404(Store, pk=pk)
        from .dynamic_database import alias_for_slug, enabled, materialize
        if enabled() and store.database_alias == alias_for_slug(store.slug):
            try:
                materialize(store.slug, expected_alias=store.database_alias)
            except ValueError:
                return Response({'code': 409, 'msg': '门店数据库配置不可用，请联系平台运维'}, status=409)
        if store.initialized_at is not None:
            return Response({'code': 0, 'msg': '门店已完成初始化，请核对现有账号', 'data': managed_store(store)})
        if store.status != Store.Status.PROVISIONING or store.database_alias not in configured_database_aliases():
            return Response({'code': 409, 'msg': '门店状态或数据库配置不允许恢复初始化'}, status=409)
        from .models import StoreProvisioningClaim
        claim = StoreProvisioningClaim.objects.filter(store=store).first()
        if not serializer.validated_data.get('admin_username') and not (claim and claim.admin_username):
            return Response({'code': 400, 'msg': '请填写首次管理员账号、密码和手机号'}, status=400)
        from .provisioning import create_database, initialize_store
        try:
            create_database(store.database_alias, admin_username=serializer.validated_data.get('admin_username'))
            initialize_store(store, admin_username=serializer.validated_data.get('admin_username'),
                admin_password=serializer.validated_data.get('admin_password'),
                admin_phone=serializer.validated_data.get('admin_phone'),
                admin_nickname=serializer.validated_data.get('admin_nickname'), new_database_created=True)
        except CommandError as exc:
            _keep_uninitialized_store_closed(store)
            return Response({'code': 409, 'msg': str(exc), 'errorCode': 'STORE_INITIALIZATION_REQUIRES_REVIEW'}, status=409)
        except Exception:
            _keep_uninitialized_store_closed(store)
            return Response({'code': 500, 'msg': '初始化未完成，请检查后重试', 'errorCode': 'STORE_INITIALIZATION_FAILED'}, status=500)
        store.refresh_from_db()
        return Response({'code': 0, 'msg': '门店初始化已完成', 'data': managed_store(store)})


class StoreLogoUploadView(APIView):
    permission_classes = [IsPlatformStoreManager]

    def post(self, request):
        from rest_framework import serializers
        from django.core.exceptions import ValidationError as ImageValidationError
        from .media import save_store_logo_upload
        try:
            upload = serializers.ImageField().run_validation(request.FILES.get('image'))
        except ImageValidationError as error:
            raise serializers.ValidationError(error.messages) from error
        if upload.size > 10 * 1024 * 1024:
            raise serializers.ValidationError('图片大小不能超过 10MB')
        try:
            data = save_store_logo_upload(upload)
        except ValueError as error:
            raise serializers.ValidationError(str(error)) from error
        from common.media import build_media_url_from_path
        data['url'] = build_media_url_from_path(request, data['url'])
        data['original_url'] = build_media_url_from_path(request, data['original_url'])
        return Response({'code': 0, 'data': data})


class StoreSwitchView(APIView):
    """A store employee identity must be established by a fresh login."""
    permission_classes = [IsPlatformStoreManager]

    def post(self, request):
        return Response({'code': 409, 'errorCode': 'STORE_LOGIN_REQUIRED',
                         'msg': '切换门店员工身份请重新登录对应账号'}, status=409)
