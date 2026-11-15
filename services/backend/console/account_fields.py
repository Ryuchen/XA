"""Audited per-game and per-service order account-field configuration."""
import json
from collections.abc import Mapping

from rest_framework.response import Response
from rest_framework.views import APIView
from stores.db import transaction
from wallet.models import SystemConfig
from orders.account_fields import FIELDS, current, key, lock_guard, parse
from orders.models import GameCategory, ServiceItem
from .models import AdminAuditLog
from .permissions import IsConsoleUser, get_account_permissions


class OrderAccountFieldsTargetsView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        if not {'dictionary:view', 'dictionary:edit'}.intersection(get_account_permissions(request.account)):
            return Response({'code': 403, 'msg': '无权查看资料规则'}, status=403)
        scope = request.query_params.get('scope')
        if scope not in ('game', 'service'):
            return Response({'code': 400, 'msg': '请选择游戏或服务'}, status=400)
        try:
            page = int(request.query_params.get('page', 1))
            size = int(request.query_params.get('page_size', 50))
            if not 1 <= page <= 100000 or not 1 <= size <= 100:
                raise ValueError()
        except (TypeError, ValueError):
            return Response({'code': 400, 'msg': '分页参数无效'}, status=400)
        model = GameCategory if scope == 'game' else ServiceItem
        keyword = request.query_params.get('keyword', '').strip()[:100]
        query = model.objects.filter(name__icontains=keyword).order_by('id')
        return Response({'code': 0, 'data': {'list': list(query.values('id', 'name')[(page-1)*size:page*size]),
                                              'total': query.count(), 'page': page, 'page_size': size}})


class OrderAccountFieldsView(APIView):
    permission_classes = [IsConsoleUser]

    def _target(self, scope, pk):
        if scope not in ('game', 'service'):
            return None
        model = GameCategory if scope == 'game' else ServiceItem
        return model.objects.filter(pk=pk).values('id', 'name').first()

    def get(self, request, scope, pk):
        if not {'dictionary:view', 'dictionary:edit'}.intersection(get_account_permissions(request.account)):
            return Response({'code': 403, 'msg': '无权查看资料规则'}, status=403)
        target = self._target(scope, pk)
        if not target:
            return Response({'code': 404, 'msg': '游戏或服务不存在'}, status=404)
        try:
            data = current(scope, pk)
        except ValueError as exc:
            return Response({'code': 409, 'msg': str(exc)}, status=409)
        return Response({'code': 0, 'data': {**data, 'target': target, 'scope': scope}})

    def put(self, request, scope, pk):
        if 'dictionary:edit' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无权修改资料规则'}, status=403)
        data = request.data
        if (scope not in ('game', 'service') or not isinstance(data, Mapping) or set(data) != {'version', 'fields'}
                or type(data['version']) is not int or not 0 <= data['version'] < 2147483647
                or (data['fields'] is None and scope != 'service')
                or (data['fields'] is not None and (not isinstance(data['fields'], list)
                    or any(not isinstance(item, str) or item not in FIELDS for item in data['fields'])
                    or len(data['fields']) != len(set(data['fields']))))):
            return Response({'code': 400, 'msg': '资料规则格式无效'}, status=400)
        with transaction.atomic():
            lock_guard()
            target = self._target(scope, pk)
            if not target:
                return Response({'code': 404, 'msg': '游戏或服务不存在'}, status=404)
            config_key = key(scope, pk)
            row = SystemConfig.objects.select_for_update().filter(key=config_key).first()
            try:
                old = parse(row.value if row else None, scope)
            except ValueError as exc:
                return Response({'code': 409, 'msg': str(exc)}, status=409)
            if old['version'] != data['version']:
                return Response({'code': 409, 'errorCode': 'CONFIG_CHANGED', 'msg': '资料规则已变化，请刷新后重新核对'}, status=409)
            fields = None if data['fields'] is None else [field for field in FIELDS if field in data['fields']]
            new = {'version': old['version'] + 1, 'fields': fields}
            if row:
                row.value = json.dumps(new, separators=(',', ':')); row.save(update_fields=['value', 'updated_at'])
            else:
                row = SystemConfig.objects.create(key=config_key, value=json.dumps(new, separators=(',', ':')),
                                                  remark='订单游戏资料规则')
            AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
                operator_name=(request.account.nickname or request.account.username)[:150], method='PUT',
                path=request.path[:255], resource='order-account-fields', object_id=str(row.pk),
                request_body={'scope': scope, 'target_id': pk, 'before': old, 'after': new}, status_code=200)
        request._request._business_audit_recorded = True
        return Response({'code': 0, 'data': {**new, 'target': target, 'scope': scope}, 'msg': '资料规则已保存'})
