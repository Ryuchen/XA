"""Platform-managed, non-transferable tenant host bindings."""
import ipaddress
import re

from django.conf import settings
from django.db import DatabaseError, transaction
from django.http.request import validate_host
from rest_framework.response import Response
from rest_framework.views import APIView

from .hosts import DEFAULT_HOST_BINDINGS, is_admin_host
from .models import PlatformAuditEvent, StoreDomain, StoreRoutingRevision
from .platform_accounts import AccountChangeError, _locked_actor
from .platform_auth import IsPlatformAuthenticated, PlatformAuthentication
from .platform_permissions import platform_may
from .platform_store_setup import SetupError, _store


def _hostname(value):
    if not isinstance(value, str) or value != value.strip() or value != value.lower() or len(value) > 253:
        raise ValueError('域名须为小写 ASCII 主机名')
    if not value.isascii() or '.' not in value or value.endswith('.'):
        raise ValueError('请输入完整的 ASCII 域名')
    labels = value.split('.')
    if any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels):
        raise ValueError('域名格式无效')
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        raise ValueError('不能使用 IP 地址作为门店域名')
    if is_admin_host(value) or value in getattr(settings, 'STORE_HOST_BINDINGS', DEFAULT_HOST_BINDINGS):
        raise ValueError('平台及部署保留域名不能在此修改')
    allowed = settings.ALLOWED_HOSTS or (['.localhost', '127.0.0.1', '[::1]'] if settings.DEBUG else [])
    if not validate_host(value, allowed):
        raise ValueError('域名不在部署允许的 ALLOWED_HOSTS 中，请先完成部署配置')
    return value


class PlatformStoreDomainsView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def allowed(self, request):
        return is_admin_host(request.get_host()) and platform_may(request.platform_account, 'stores:manage')

    def get(self, request, pk):
        if not self.allowed(request):
            return Response({'code': 403, 'msg': '需要平台超级管理员权限'}, status=403)
        try:
            store = _store(pk)
            from .domain_index import _directory
            try:
                _directory()
                available = True
            except ValueError:
                available = False
            return Response({'code': 0, 'data': {'store_id': store.pk, 'index_configured': available,
                'domains': list(StoreDomain.objects.filter(store=store).order_by('hostname')
                                .values('hostname', 'active', 'revision', 'updated_at'))}})
        except SetupError as exc:
            return Response({'code': exc.status, 'msg': str(exc)}, status=exc.status)
        except DatabaseError:
            return Response({'code': 503, 'msg': '门店域名暂不可读取'}, status=503)

    def put(self, request, pk):
        if not self.allowed(request):
            return Response({'code': 403, 'msg': '需要平台超级管理员权限'}, status=403)
        data = request.data
        if (not isinstance(data, dict) or set(data) != {'hostname', 'active', 'revision'}
                or type(data['active']) is not bool or type(data['revision']) is not int
                or data['revision'] < 0):
            return Response({'code': 400, 'msg': '请填写域名、状态和配置版本'}, status=400)
        try:
            host = _hostname(data['hostname'])
            from .domain_index import read, write
            with transaction.atomic(using='default'):
                actor = _locked_actor(request.auth)
                store = _store(pk, lock=True)
                row = StoreDomain.objects.select_for_update().filter(hostname=host).first()
                if row and row.store_id != store.pk:
                    raise ValueError('该域名已保留给其他门店，不能转移')
                if (row.revision if row else 0) != data['revision']:
                    raise ValueError('域名配置已变化，请刷新后核对')
                if not row and not data['active']:
                    raise ValueError('尚未绑定的域名不能停用')
                revision = (row.revision if row else 0) + 1
                # Persist a signed denial before the directory can commit. If
                # commit or publication is uncertain, offline access stays shut.
                write(host, slug=store.slug, alias=store.database_alias, store_id=store.pk,
                      revision=revision, active=False)
                if row:
                    row.active, row.revision = data['active'], revision
                    row.save(update_fields=['active', 'revision', 'updated_at'])
                else:
                    row = StoreDomain.objects.create(hostname=host, store=store,
                                                     active=True, revision=revision)
                route, _ = StoreRoutingRevision.objects.select_for_update().get_or_create(store=store)
                from .routing_snapshot import latest_revision, schedule
                route.version = max(route.version, latest_revision(store)) + 1
                route.save(update_fields=['version'])
                PlatformAuditEvent.objects.create(account=actor, action='store.domain.updated',
                    target_store_slug=store.slug, target_type='store_domain', target_id=str(row.pk),
                    detail={'hostname': host, 'active': row.active, 'revision': revision})
                if store.status in ('ACTIVE', 'DISABLED'):
                    schedule(store, route.version)
            index = read(host)
            available = bool(index and index['revision'] == revision and index['active'])
            if data['active'] and store.status == 'ACTIVE' and not available:
                return Response({'code': 503, 'errorCode': 'DOMAIN_PUBLICATION_PENDING',
                    'msg': '域名已保存，但离线路由发布尚未确认，请重新读取并联系平台核对'}, status=503)
            return Response({'code': 0, 'data': {'hostname': host, 'active': row.active,
                'revision': revision, 'offline_available': available}})
        except (ValueError, OSError, AccountChangeError, SetupError) as exc:
            return Response({'code': 409, 'msg': str(exc)}, status=409)
        except DatabaseError:
            return Response({'code': 503, 'errorCode': 'DOMAIN_RESULT_UNCERTAIN',
                'msg': '域名配置结果暂不能确认，请重新读取后核对'}, status=503)
