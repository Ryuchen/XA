"""Store-scoped audited terms; saving terms never turns on payment."""
import json
from collections.abc import Mapping

from django.db import IntegrityError
from rest_framework.response import Response
from rest_framework.views import APIView
from stores.db import transaction

from orders.points_redemption_config import CONFIG_KEY, MAX_STORED_AMOUNT, current_rules, parse_rules
from wallet.models import SystemConfig
from .models import AdminAuditLog
from .permissions import IsConsoleUser, get_account_permissions


class PointsConfigError(ValueError):
    def __init__(self, message, status=409, code='CONFIG_CHANGED'):
        super().__init__(message)
        self.status, self.code = status, code


def pass_redemption_status(rules):
    """Store-level pass policy; each purchase still checks its own eligibility."""
    from engagement.points_usage import FIELDS, current_usage
    from users.pass_license import status as pass_license_status
    from users.pass_points import available

    if not rules['version'] or not rules['max_ratio_basis_points'] or not available():
        return 'DISABLED'
    try:
        issuance, switches = current_usage()
    except ValueError:
        return 'UNKNOWN'
    if issuance is None:
        return 'UNKNOWN'
    if issuance is False or not all(switches[field] for field in FIELDS):
        return 'DISABLED'
    license_status = pass_license_status()
    if license_status == 'UNKNOWN':
        return 'UNKNOWN'
    return 'ENABLED' if license_status == 'ALLOW' else 'DISABLED'


def service_redemption_status(rules):
    """Report store-level prerequisites; each checkout still validates its order."""
    from django.conf import settings
    from engagement.points_usage import FIELDS, current_usage
    from orders.points_service_payment import _enabled as receipt_enabled
    from orders.segment_financial_capture import enabled as segment_enabled
    from stores.context import current_store

    store = current_store()
    connection = transaction.get_connection()
    if not store or store.database_alias != connection.alias:
        return 'UNKNOWN'
    if (not rules['version'] or not rules['max_ratio_basis_points']
            or connection.vendor != 'mysql'
            or getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is not True
            or getattr(settings, 'BOSS_FUNDING_SELECTION_ENABLED', False) is not True
            or not receipt_enabled() or not segment_enabled()):
        return 'DISABLED'
    try:
        issuance, switches = current_usage()
    except ValueError:
        return 'UNKNOWN'
    if issuance is None:
        return 'UNKNOWN'
    return 'ENABLED' if issuance and all(switches[field] for field in FIELDS) else 'DISABLED'


def redemption_configuration_data(rules):
    status = service_redemption_status(rules)
    return {**rules, 'redemption_available': status == 'ENABLED',
            'service_redemption_status': status, 'pass_redemption_status': pass_redemption_status(rules)}


def save_points_terms(*, group_point_hundredths, group_coin_units, max_ratio_basis_points, version, audit):
    if (type(group_point_hundredths) is not int or not 1 <= group_point_hundredths <= MAX_STORED_AMOUNT
            or type(group_coin_units) is not int or not 10 <= group_coin_units <= MAX_STORED_AMOUNT
            or group_coin_units % 10 or type(max_ratio_basis_points) is not int
            or not 0 <= max_ratio_basis_points < 10000 or type(version) is not int
            or not 0 <= version < MAX_STORED_AMOUNT):
        raise PointsConfigError('兑换组及抵扣上限无效，请检查积分、整数店币和低于100%的比例', 400, 'INVALID_CONFIG')
    new = {'group_point_hundredths': group_point_hundredths, 'group_coin_units': group_coin_units,
           'max_ratio_basis_points': max_ratio_basis_points, 'version': version + 1}
    try:
        with transaction.atomic():
            if version and not SystemConfig.objects.filter(key=CONFIG_KEY).exists():
                raise PointsConfigError('积分抵扣规则已变化，请刷新后重新核对')
            row, created = SystemConfig.objects.get_or_create(key=CONFIG_KEY, defaults={
                'value': json.dumps(new, separators=(',', ':')), 'remark': '本店积分抵扣规则'})
            row = SystemConfig.objects.select_for_update().get(pk=row.pk)
            old = parse_rules(None if created else row.value)
            if old['version'] != version:
                raise PointsConfigError('积分抵扣规则已变化，请刷新后重新核对')
            if not created:
                row.value = json.dumps(new, separators=(',', ':'))
                row.save(update_fields=['value', 'updated_at'])
            audit(row, old, new)
            return new
    except IntegrityError:
        raise PointsConfigError('积分抵扣规则已变化，请刷新后重新核对') from None


class PointsRedemptionConfigView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        if not {'dictionary:view', 'dictionary:edit'}.intersection(get_account_permissions(request.account)):
            return Response({'code': 403, 'msg': '无权查看积分抵扣配置'}, status=403)
        try:
            data = current_rules()
        except ValueError as exc:
            return Response({'code': 409, 'msg': str(exc)}, status=409)
        return Response({'code': 0, 'data': redemption_configuration_data(data)})

    def put(self, request):
        if 'dictionary:edit' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无权修改积分抵扣配置'}, status=403)
        keys = {'group_point_hundredths', 'group_coin_units', 'max_ratio_basis_points', 'version'}
        if not isinstance(request.data, Mapping) or set(request.data) != keys:
            return Response({'code': 400, 'msg': '请完整填写兑换组、抵扣上限和版本'}, status=400)
        try:
            def audit(row, old, new):
                AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
                    operator_name=(request.account.nickname or request.account.username)[:150], method='PUT',
                    path=request.path[:255], resource='points-redemption-terms', object_id=str(row.pk),
                    request_body={'before': old, 'after': new}, status_code=200)
            data = save_points_terms(**request.data, audit=audit)
        except PointsConfigError as exc:
            return Response({'code': exc.status, 'errorCode': exc.code, 'msg': str(exc)}, status=exc.status)
        except ValueError as exc:
            return Response({'code': 409, 'msg': str(exc)}, status=409)
        request._request._business_audit_recorded = True
        return Response({'code': 0, 'data': redemption_configuration_data(data),
                         'msg': '抵扣规则已保存'})
