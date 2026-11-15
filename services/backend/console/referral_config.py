"""Store referral commission draft; it does not change order pricing."""
import json

from django.db import IntegrityError
from rest_framework.response import Response
from rest_framework.views import APIView

from stores.db import transaction
from wallet.models import SystemConfig
from .models import AdminAuditLog
from .permissions import IsConsoleUser, get_account_permissions


CONFIG_KEY = 'referral_commission_draft'


def _read(raw):
    if raw is None:
        return {'version': 0, 'rate': None}
    try:
        value = json.loads(raw)
        if (not isinstance(value, dict) or set(value) != {'version', 'rate'}
                or type(value['version']) is not int or value['version'] < 1
                or type(value['rate']) is not int or not 0 <= value['rate'] <= 100):
            raise ValueError
        return value
    except (ValueError, TypeError, KeyError):
        raise ValueError('推荐佣金草稿无效，请联系管理员核对') from None


def _response(value):
    return {**value, 'state': 'DRAFT', 'applies_to_orders': False}


class ReferralConfigView(APIView):
    permission_classes = [IsConsoleUser]

    def get(self, request):
        if not {'dictionary:view', 'dictionary:edit'}.intersection(get_account_permissions(request.account)):
            return Response({'code': 403, 'msg': '无操作权限'}, status=403)
        raw = SystemConfig.objects.filter(key=CONFIG_KEY).values_list('value', flat=True).first()
        try:
            value = _read(raw)
        except ValueError as exc:
            return Response({'code': 409, 'msg': str(exc)}, status=409)
        return Response({'code': 0, 'data': _response(value)})

    def put(self, request):
        if 'dictionary:edit' not in get_account_permissions(request.account):
            return Response({'code': 403, 'msg': '无操作权限'}, status=403)
        rate = request.data.get('rate')
        expected = request.data.get('expected_version')
        if (type(rate) is not int or not 0 <= rate <= 100
                or type(expected) is not int or expected < 0):
            return Response({'code': 400, 'msg': '请填写 0 至 100 的整数比例及有效版本'}, status=400)
        try:
            with transaction.atomic():
                row, created = SystemConfig.objects.get_or_create(
                    key=CONFIG_KEY, defaults={'value': '', 'remark': '推荐佣金配置草稿（尚未生效）'})
                row = SystemConfig.objects.select_for_update().get(pk=row.pk)
                before = _read(None if created else row.value)
                if before['version'] != expected:
                    raise ValueError('配置已变化，请刷新后重试')
                after = {'version': before['version'] + 1, 'rate': rate}
                row.value = json.dumps(after, separators=(',', ':'))
                row.save(update_fields=['value', 'updated_at'])
                AdminAuditLog.objects.create(operator=request.legacy_user, operator_account=request.account,
                    operator_name=(request.account.nickname or request.account.username)[:150], method='PUT',
                    path=request.path[:255], resource='referral-commission-draft', object_id=str(row.pk),
                    request_body={'before': before, 'after': after}, status_code=200)
        except (IntegrityError, ValueError) as exc:
            return Response({'code': 409, 'msg': str(exc) if isinstance(exc, ValueError)
                             else '配置已变化，请刷新后重试'}, status=409)
        request._request._business_audit_recorded = True
        return Response({'code': 0, 'data': _response(after), 'msg': '草稿已保存，暂不影响订单'})
