"""Per-store configuration for reviewed service/add-on associations.

These records configure associations; the order-specific purchase flow still
checks eligibility, customer confirmation and available funding capability.
"""
from django.db import IntegrityError
from rest_framework.response import Response
from rest_framework.views import APIView

from stores.db import transaction
from orders.models import ServiceItem, ServiceAddonConfiguration, ServiceAddonPriceOption
from .models import AdminAuditLog
from .permissions import IsConsoleUser, get_account_permissions


def _item(row):
    return {'service_id': row.service_id, 'addon_id': row.addon_id, 'enabled': row.enabled,
            'conditions': row.conditions, 'condition_mode': row.condition_mode, 'version': row.version}


def _valid_pair(main, addon):
    return bool(main and addon and main.pk != addon.pk and not main.is_addon and addon.is_addon
                and not (main.service_category_id and main.service_category.is_gift)
                and not (addon.service_category_id and addon.service_category.is_gift)
                and main.game_category_id and main.game_category_id == addon.game_category_id)


class ServiceAddonConfigurationView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request, service_id, addon_id=None):
        if not {'service:view', 'service:edit'}.intersection(
                get_account_permissions(request.account)):
            return Response({'code': 403, 'msg': '无权查看加购配置'}, status=403)
        if not (0 < service_id <= 9223372036854775807 and
                (addon_id is None or 0 < addon_id <= 9223372036854775807)):
            return Response({'code': 400, 'msg': '服务编号无效'}, status=400)
        service = ServiceItem.objects.filter(pk=service_id, is_addon=False).first()
        if not service:
            return Response({'code': 404, 'msg': '主服务不存在'}, status=404)
        rows = ServiceAddonConfiguration.objects.filter(service_id=service_id).select_related('addon').order_by('addon_id')
        if addon_id is not None:
            rows = rows.filter(addon_id=addon_id)
        return Response({'code': 0, 'data': {'service_id': service_id, 'status': 'CONFIGURATION_ONLY',
            'items': [{**_item(row), 'addon_name': row.addon.name, 'addon_active': row.addon.is_active,
                       'price': row.addon.price, 'price_unit': row.addon.price_unit} for row in rows]}})

    def put(self, request, service_id, addon_id=None):
        if 'service:edit' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无权修改加购配置'}, status=403)
        if addon_id is None:
            return Response({'code': 400, 'msg': '请选择加购项目'}, status=400)
        if not (0 < service_id <= 9223372036854775807 and 0 < addon_id <= 9223372036854775807):
            return Response({'code': 400, 'msg': '服务编号无效'}, status=400)
        if (not isinstance(request.data, dict) or not {'version', 'enabled', 'conditions'} <= set(request.data)
                or set(request.data) - {'version', 'enabled', 'conditions', 'condition_mode'}):
            return Response({'code': 400, 'msg': '请提供配置版本、启停状态和适用条件'}, status=400)
        version, enabled, conditions = (request.data[k] for k in ('version', 'enabled', 'conditions'))
        condition_mode = request.data.get('condition_mode')
        if (type(version) is not int or not 0 <= version < 2147483647 or type(enabled) is not bool
                or not isinstance(conditions, str) or len(conditions) > 500
                or ('condition_mode' in request.data and condition_mode not in ServiceAddonConfiguration.ConditionMode.values)):
            return Response({'code': 400, 'msg': '配置版本、状态或条件格式无效'}, status=400)
        conditions = conditions.strip()
        try:
            with transaction.atomic():
                # Stable order avoids lock inversion when two staff edit crossed pairs.
                items = {row.pk: row for row in ServiceItem.objects.select_for_update().filter(
                    pk__in=[service_id, addon_id]).select_related('service_category').order_by('pk')}
                main, addon = items.get(service_id), items.get(addon_id)
                if not main or not addon:
                    return Response({'code': 404, 'msg': '主服务或加购项目不存在'}, status=404)
                if not _valid_pair(main, addon):
                    return Response({'code': 400, 'msg': '请选择同一游戏的主服务和加购项目'}, status=400)
                row = ServiceAddonConfiguration.objects.select_for_update().filter(service=main, addon=addon).first()
                if (row.version if row else 0) != version:
                    return Response({'code': 409, 'errorCode': 'CONFIG_CHANGED', 'msg': '配置已变化，请刷新后重试'}, status=409)
                before = _item(row) if row else None
                if row is None:
                    row = ServiceAddonConfiguration(service=main, addon=addon, version=1)
                else:
                    row.version += 1
                row.enabled, row.conditions = enabled, conditions
                if condition_mode is not None:
                    row.condition_mode = condition_mode
                if row.condition_mode == ServiceAddonConfiguration.ConditionMode.NONE and conditions:
                    return Response({'code': 400, 'msg': '有适用条件时须选择客服核实'}, status=400)
                row.save()
                after = _item(row)
                AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
                    operator_name=(request.account.nickname or request.account.username)[:150], method='PUT',
                    path=request.path[:255], resource='service-addon-configuration', object_id=str(row.pk),
                    request_body={'before': before, 'after': after}, status_code=200)
        except IntegrityError:
            return Response({'code': 409, 'msg': '配置已变化，请刷新后重试'}, status=409)
        request._request._business_audit_recorded = True
        return Response({'code': 0, 'data': {'configuration': after, 'status': 'CONFIGURATION_ONLY'},
                         'msg': '配置已保存，具体订单的加购可用性以实际报价为准'})


def _price_options(configuration):
    return list(configuration.price_options.order_by('unit').values('unit', 'price', 'enabled'))


class ServiceAddonPriceOptionsView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request, service_id, addon_id):
        if not {'service:view', 'service:edit'}.intersection(get_account_permissions(request.account)):
            return Response({'code': 403, 'msg': '无权查看加购计价配置'}, status=403)
        if not (0 < service_id <= 9223372036854775807 and 0 < addon_id <= 9223372036854775807):
            return Response({'code': 400, 'msg': '服务编号无效'}, status=400)
        configuration = ServiceAddonConfiguration.objects.filter(service_id=service_id, addon_id=addon_id).first()
        if not configuration:
            return Response({'code': 404, 'msg': '请先配置主服务与加购关系'}, status=404)
        return Response({'code': 0, 'data': {'version': configuration.version,
            'options': _price_options(configuration), 'status': 'CONFIGURATION_ONLY'}})

    def put(self, request, service_id, addon_id):
        if 'service:edit' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无权修改加购计价配置'}, status=403)
        if not (0 < service_id <= 9223372036854775807 and 0 < addon_id <= 9223372036854775807):
            return Response({'code': 400, 'msg': '服务编号无效'}, status=400)
        if not isinstance(request.data, dict) or set(request.data) != {'version', 'options'}:
            return Response({'code': 400, 'msg': '请提供配置版本及计价单位列表'}, status=400)
        version, options = request.data['version'], request.data['options']
        if type(version) is not int or not 1 <= version < 2147483647 or not isinstance(options, list) or len(options) > 4:
            return Response({'code': 400, 'msg': '配置版本或计价单位列表无效'}, status=400)
        clean, units = [], set()
        for row in options:
            if (not isinstance(row, dict) or set(row) != {'unit', 'price', 'enabled'}
                    or not isinstance(row['unit'], str) or row['unit'] not in ServiceAddonPriceOption.Unit.values
                    or row['unit'] in units or type(row['price']) is not int or not 0 < row['price'] <= 2147483600
                    or row['price'] % 100 or type(row['enabled']) is not bool):
                return Response({'code': 400, 'msg': '单位不能重复，单价须为大于零的 10 店币整数倍'}, status=400)
            units.add(row['unit'])
            clean.append({'unit': row['unit'], 'price': row['price'], 'enabled': row['enabled']})
        clean.sort(key=lambda row: row['unit'])
        with transaction.atomic():
            # Follow the same service-then-configuration lock order as relationship edits.
            items = {row.pk: row for row in ServiceItem.objects.select_for_update().filter(
                pk__in=[service_id, addon_id]).select_related('service_category').order_by('pk')}
            main, addon = items.get(service_id), items.get(addon_id)
            if not main or not addon:
                return Response({'code': 404, 'msg': '主服务或加购项目不存在'}, status=404)
            if not _valid_pair(main, addon):
                return Response({'code': 400, 'msg': '主服务或加购项目已发生变化，请先核对'}, status=400)
            configuration = ServiceAddonConfiguration.objects.select_for_update().filter(
                service_id=service_id, addon_id=addon_id).first()
            if not configuration:
                return Response({'code': 404, 'msg': '请先配置主服务与加购关系'}, status=404)
            if configuration.version != version:
                return Response({'code': 409, 'errorCode': 'CONFIG_CHANGED', 'msg': '配置已变化，请刷新后重试'}, status=409)
            before = {'version': configuration.version, 'options': _price_options(configuration)}
            configuration.price_options.all().delete()
            ServiceAddonPriceOption.objects.bulk_create([
                ServiceAddonPriceOption(configuration=configuration, **option) for option in clean])
            configuration.version += 1
            configuration.save(update_fields=['version', 'updated_at'])
            after = {'version': configuration.version, 'options': _price_options(configuration)}
            AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
                operator_name=(request.account.nickname or request.account.username)[:150], method='PUT',
                path=request.path[:255], resource='service-addon-price-options', object_id=str(configuration.pk),
                request_body={'before': before, 'after': after}, status_code=200)
        request._request._business_audit_recorded = True
        return Response({'code': 0, 'data': {**after, 'status': 'CONFIGURATION_ONLY'},
                         'msg': '计价单位已保存，具体订单的加购可用性以实际报价为准'})
