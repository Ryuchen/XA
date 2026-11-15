"""Platform-owned catalogue setup for a store before its first opening."""
import hashlib
import json
from collections.abc import Mapping

from django.core.serializers.json import DjangoJSONEncoder
from django.db import DatabaseError
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from console.models import AdminAuditLog
from console.dictionaries import DictionaryItemSerializer
from console.dictionary_models import BusinessDictionaryItem
from console.gameplay import GameplaySerializer
from console.serializers import (AdminGameCategorySerializer, AdminServiceCategorySerializer,
                                 AdminServiceItemSerializer, AdminEscortLevelSerializer)
from orders.models import GameCategory, Gameplay, ServiceCategory, ServiceItem
from users.models import EscortLevel
from stores.context import store_context
from .models import Store
from .platform_store_setup import PlatformSetupBase, SetupError, _store


KINDS = {
    'game': (GameCategory, AdminGameCategorySerializer),
    'gameplay': (Gameplay, GameplaySerializer),
    'category': (ServiceCategory, AdminServiceCategorySerializer),
    'service': (ServiceItem, AdminServiceItemSerializer),
    'escort-level': (EscortLevel, AdminEscortLevelSerializer),
    'dictionary': (BusinessDictionaryItem, DictionaryItemSerializer),
}


def _snapshot():
    """Hash the actual store catalogue, not a second configuration copy."""
    values = {kind: list(model.objects.order_by('pk').values())
              for kind, (model, _) in KINDS.items()}
    encoded = json.dumps(values, cls=DjangoJSONEncoder, sort_keys=True,
                         separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


class PlatformSetupCatalogView(PlatformSetupBase):
    def get(self, request, pk, kind):
        if not self.allowed(request):
            return self.denied()
        if kind not in KINDS:
            return Response({'code': 404, 'msg': '目录类型不存在'}, status=404)
        try:
            store = _store(pk)
            model, serializer_type = KINDS[kind]
            with store_context(store):
                version = _snapshot()
                rows = serializer_type(model.objects.order_by('pk'), many=True,
                                       context={'request': request}).data
            response = Response({'code': 0, 'data': {'kind': kind, 'version': version, 'items': rows}})
            response['Cache-Control'] = 'no-store'
            return response
        except SetupError as exc:
            return Response({'code': exc.status, 'errorCode': exc.code, 'msg': str(exc)}, status=exc.status)
        except DatabaseError:
            return Response({'code': 503, 'msg': '门店商品目录暂不可读取'}, status=503)

    def put(self, request, pk, kind):
        if not self.allowed(request):
            return self.denied()
        if (kind not in KINDS or not isinstance(request.data, Mapping)
                or set(request.data) != {'version', 'id', 'configuration'}
                or not isinstance(request.data['version'], str) or len(request.data['version']) != 64
                or (request.data['id'] is not None and
                    (type(request.data['id']) is not int or request.data['id'] <= 0))
                or not isinstance(request.data['configuration'], Mapping)):
            return Response({'code': 400, 'msg': '请填写目录类型、版本、目标和配置'}, status=400)

        def change(store, actor, operation_id):
            if store.status != Store.Status.CONFIGURING:
                raise SetupError('此入口仅用于门店首次开业前的目录配置')
            from console.catalog_write_lock import CatalogUnavailable, lock_catalog
            try:
                lock_catalog()
            except CatalogUnavailable as exc:
                raise SetupError('门店目录暂不可编辑，请稍后重试或联系平台', 503, 'CATALOG_UNAVAILABLE') from exc
            before = _snapshot()
            if request.data['version'] != before:
                raise SetupError('目录已变化，请刷新后重新核对', 409, 'CONFIG_CHANGED')
            model, serializer_type = KINDS[kind]
            target_id = request.data['id']
            row = model.objects.select_for_update().filter(pk=target_id).first() if target_id else None
            if target_id and row is None:
                raise SetupError('目录记录不存在', 404, 'CONFIG_NOT_FOUND')
            serializer = serializer_type(row, data=request.data['configuration'], partial=row is not None,
                                         context={'request': request})
            try:
                serializer.is_valid(raise_exception=True)
                saved = serializer.save()
            except ValidationError as exc:
                raise SetupError(str(exc.detail), 400, 'CONFIG_INVALID') from exc
            after = _snapshot()
            AdminAuditLog.objects.create(operator_name=f'平台超级管理员：{actor.username}'[:150],
                method='PUT', path=request.path[:255], resource=f'initial-catalog-{kind}',
                object_id=str(saved.pk), request_body={'before_version': before, 'after_version': after,
                    'configuration': dict(request.data['configuration']), 'actor_kind': 'platform',
                    'platform_account_id': str(actor.pk), 'operation_id': operation_id}, status_code=200)
            return {'kind': kind, 'id': saved.pk, 'version': after, 'item': serializer.data}

        return self.mutate(request, pk, f'catalog_{kind}', change)
