"""Scoped platform access to a store's payroll without a store-user identity."""
import hashlib
import json
from collections.abc import Mapping
from uuid import UUID, uuid4

from django.conf import settings
from django.db import DatabaseError, transaction as directory_transaction
from rest_framework import serializers
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError

from console.models import AdminAuditLog
from console.payroll_event_barrier import PayrollEventBarrierError
from console.payroll import (PayrollConfigSerializer, SalaryAssignmentSerializer, SalaryPlanSerializer,
                             assert_open, confirm_payroll_locked, costing_lock, employee_is_payroll_eligible, employee_queryset)
from console.payroll_calculation import calculate_payroll, name_of, parse_month, plan_snapshot, refund_accounting_enabled
from console.payroll_models import PayrollConfig, PayrollRun, SalaryAssignment, SalaryPlan
from stores.context import store_context
from stores.db import transaction as tenant_transaction
from .database_identity import lock_store_registration
from .hosts import is_admin_host
from .license_payroll import require_payroll_configuration
from .models import PlatformAuditEvent, Store
from .platform_auth import IsPlatformAuthenticated, PlatformAuthentication, account_for_token
from .platform_permissions import platform_may


class StorePayrollError(Exception):
    def __init__(self, message, status=409):
        self.message, self.status = message, status


def _authorized(token, slug, action):
    lock_store_registration()
    try:
        actor = account_for_token(token)
    except TokenError:
        raise StorePayrollError('平台登录已失效', 401) from None
    store = Store.objects.select_for_update().filter(slug=slug).first()
    if not store or not platform_may(actor, action, store_slug=slug):
        raise StorePayrollError('门店不存在或无权访问', 404)
    from .dynamic_database import store_connection_ready
    if not store_connection_ready(store):
        raise StorePayrollError('门店数据暂不可用', 503)
    return actor, store


def _response(data):
    response = Response({'code': 0, 'data': data})
    response['Cache-Control'] = 'no-store'
    return response


class StorePayrollView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def get(self, request, slug, resource, plan_id=None):
        if not is_admin_host(request.get_host()):
            return Response({'code': 403, 'msg': '请从管理后台入口访问'}, status=403)
        actions = {'plans': 'salary:view', 'config': 'salary:view', 'assignments': 'salary:view',
                   'employees': 'salary:view', 'runs': 'salary:view', 'preview': 'salary:calculate'}
        action = actions.get(resource)
        if not action or plan_id is not None:
            return Response({'code': 404, 'msg': '资源不存在'}, status=404)
        try:
            with directory_transaction.atomic(using='default'):
                actor, store = _authorized(request.auth, slug, action)
                with store_context(store), tenant_transaction.atomic():
                    if resource == 'plans':
                        data = {'list': SalaryPlanSerializer(SalaryPlan.objects.select_related('position').all(), many=True).data}
                    elif resource == 'config':
                        config = PayrollConfig.objects.filter(pk=1).first()
                        data = PayrollConfigSerializer(config).data if config else {'night_start': None, 'night_end': None, 'revision': 0}
                    elif resource == 'assignments':
                        month = parse_month(request.query_params.get('month'))
                        rows = SalaryAssignment.objects.filter(month=month).select_related('account', 'plan__position')
                        data = {'month': month.strftime('%Y-%m'), 'list': SalaryAssignmentSerializer(rows, many=True).data}
                    elif resource == 'employees':
                        data = {'list': [{'id': row.pk, 'name': name_of(row), 'username': row.username,
                                          'account_no': row.account_no} for row in employee_queryset()]}
                    elif resource == 'preview':
                        data = calculate_payroll(parse_month(request.query_params.get('month')))
                    else:
                        month = parse_month(request.query_params.get('month'))
                        run = PayrollRun.objects.filter(month=month).first()
                        data = None if not run else {'id': run.pk, 'month': month.strftime('%Y-%m'),
                            'fingerprint': run.fingerprint, 'snapshot': run.snapshot,
                            'confirmed_at': run.confirmed_at.isoformat(),
                            'confirmed_by_name': name_of(run.confirmed_by) if run.confirmed_by_id else f'平台账号 {run.confirmed_by_platform_id}'}
                PlatformAuditEvent.objects.create(account=actor, action='store_payroll.view',
                    target_store_slug=slug, target_type='store_payroll', target_id=resource,
                    detail={'resource': resource, 'month': request.query_params.get('month', '')[:7]})
            return _response(data)
        except StorePayrollError as exc:
            return Response({'code': exc.status, 'msg': exc.message}, status=exc.status)
        except (DatabaseError, ValueError):
            return Response({'code': 503, 'msg': '门店工资数据暂不可用'}, status=503)

    def post(self, request, slug, resource, plan_id=None):
        if not is_admin_host(request.get_host()):
            return Response({'code': 403, 'msg': '请从管理后台入口访问'}, status=403)
        if plan_id is None and resource == 'confirm':
            return self._confirm(request, slug)
        if plan_id is not None or resource not in ('plans', 'assignments'):
            return Response({'code': 404, 'msg': '资源不存在'}, status=404)
        if not isinstance(request.data, Mapping):
            raise ValidationError('请求格式无效')
        try:
            request_id = UUID(str(request.data.get('request_id')))
        except (ValueError, TypeError, AttributeError):
            raise ValidationError('请求编号须为有效 UUID') from None
        payload = dict(request.data)
        payload.pop('request_id', None)
        if resource == 'plans':
            allowed = {'position', 'name', 'base_fen', 'performance_fen', 'target_fen', 'target_scope',
                       'dispatch_day_bps', 'dispatch_night_bps', 'recharge_bps', 'aftersale_fee_fen',
                       'exam_fee_fen', 'exam_commission_bps', 'remark', 'is_active'}
        else:
            allowed = {'month', 'account', 'plan', 'expected_plan_revision'}
            if 'expected_plan_revision' not in payload:
                raise ValidationError('请选择并核对薪资方案版本')
        if not set(payload).issubset(allowed):
            raise ValidationError('请求含有不支持的字段')
        operation = str(request_id)
        digest = hashlib.sha256(json.dumps([slug, resource, str(request.platform_account.pk), payload],
            sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
        try:
            with directory_transaction.atomic(using='default'):
                actor, store = _authorized(request.auth, slug, 'salary:configure')
                previous = PlatformAuditEvent.objects.filter(action__in=('store_payroll.create_requested',
                    'store_payroll.confirm_requested'), detail__operation_id=operation).first()
                if previous and (previous.action != 'store_payroll.create_requested' or previous.account_id != actor.pk or previous.target_store_slug != slug
                        or previous.target_id != resource or previous.detail.get('request_hash') != digest):
                    raise StorePayrollError('请求编号已用于其他工资操作')
                PlatformAuditEvent.objects.create(account=actor, action='store_payroll.create_requested',
                    target_store_slug=slug, target_type='store_payroll', target_id=resource,
                    detail={'operation_id': operation, 'request_hash': digest})
            with directory_transaction.atomic(using='default'):
                actor, store = _authorized(request.auth, slug, 'salary:configure')
                with store_context(store), tenant_transaction.atomic():
                    costing_lock()
                    model = SalaryPlan if resource == 'plans' else SalaryAssignment
                    row = model.objects.select_for_update().filter(platform_request_id=request_id).first()
                    creation_audit = None
                    if row:
                        if row.created_by_platform_id != actor.pk or row.platform_request_hash != digest:
                            raise StorePayrollError('请求编号已用于其他工资操作')
                        audits = list(AdminAuditLog.objects.filter(resource='platform-store-payroll-create',
                            object_id=str(row.pk), request_body__operation_id=operation))
                        if (len(audits) != 1 or audits[0].request_body.get('platform_account_id') != str(actor.pk)
                                or audits[0].request_body.get('request_hash') != digest
                                or audits[0].request_body.get('resource') != resource
                                or not isinstance(audits[0].request_body.get('after'), dict)
                                or audits[0].request_body['after'].get('id') != row.pk):
                            raise StorePayrollError('创建审计凭证待核对')
                        creation_audit = audits[0]
                    elif resource == 'plans':
                        require_payroll_configuration()
                        serializer = SalaryPlanSerializer(data=payload)
                        serializer.is_valid(raise_exception=True)
                        values = dict(serializer.validated_data)
                        values.pop('revision', None)
                        row = SalaryPlan.objects.create(**values, created_by_platform_id=actor.pk,
                            created_by_system=False, platform_request_id=request_id, platform_request_hash=digest)
                    else:
                        serializer = SalaryAssignmentSerializer(data=payload)
                        serializer.is_valid(raise_exception=True)
                        values = dict(serializer.validated_data)
                        month, employee = values['month'], values['account']
                        if not employee_is_payroll_eligible(employee):
                            raise StorePayrollError('该员工已结清或不再符合薪资核算条件')
                        assert_open(month)
                        if SalaryAssignment.objects.filter(month=month, account=employee).exists():
                            raise StorePayrollError('该员工在该月已有薪资安排')
                        plan = SalaryPlan.objects.select_for_update().select_related('position').get(pk=values['plan'].pk)
                        expected = values.pop('expected_plan_revision', None)
                        if expected != plan.revision:
                            raise StorePayrollError('所选薪资方案已更新，请刷新核对')
                        values.pop('revision', None)
                        values.pop('adopt_current_plan', None)
                        values['plan'] = plan
                        row = SalaryAssignment.objects.create(**values, plan_snapshot=plan_snapshot(plan),
                            created_by_platform_id=actor.pk, platform_request_id=request_id, platform_request_hash=digest)
                    if creation_audit:
                        result = creation_audit.request_body['after']
                    else:
                        serializer = SalaryPlanSerializer(row) if resource == 'plans' else SalaryAssignmentSerializer(row)
                        result = dict(serializer.data)
                        AdminAuditLog.objects.create(operator_name=f'平台账号 {actor.pk}', method='POST',
                            path=request.path[:255], resource='platform-store-payroll-create', object_id=str(row.pk),
                            request_body={'platform_account_id': str(actor.pk), 'operation_id': operation,
                                          'request_hash': digest, 'resource': resource, 'after': result}, status_code=200)
                PlatformAuditEvent.objects.create(account=actor, action='store_payroll.create_completed',
                    target_store_slug=slug, target_type='store_payroll', target_id=resource,
                    detail={'operation_id': operation, 'request_hash': digest, 'object_id': row.pk})
            return _response(result)
        except StorePayrollError as exc:
            return Response({'code': exc.status, 'msg': exc.message}, status=exc.status)
        except (ValidationError, APIException):
            raise
        except (DatabaseError, ValueError):
            return Response({'code': 503, 'errorCode': 'PAYROLL_RESULT_UNCERTAIN',
                             'msg': '结果需要核对，请使用原请求编号重试'}, status=503)

    def _confirm(self, request, slug):
        if not getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False) or not refund_accounting_enabled():
            return Response({'code': 503, 'msg': '工资事件屏障或退款核算尚未启用'}, status=503)
        if not isinstance(request.data, Mapping) or set(request.data) != {'request_id', 'month', 'fingerprint'}:
            raise ValidationError('工资确认参数无效')
        try:
            request_id = UUID(str(request.data['request_id']))
        except (ValueError, TypeError, AttributeError):
            raise ValidationError('请求编号须为有效 UUID') from None
        month = parse_month(request.data['month'])
        supplied = serializers.CharField(min_length=64, max_length=64).run_validation(request.data['fingerprint'])
        operation = str(request_id)
        digest = hashlib.sha256(json.dumps([slug, 'confirm', str(request.platform_account.pk), month.isoformat(), supplied],
            separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
        try:
            with directory_transaction.atomic(using='default'):
                actor, store = _authorized(request.auth, slug, 'salary:calculate')
                previous = PlatformAuditEvent.objects.filter(
                    action__in=('store_payroll.create_requested', 'store_payroll.confirm_requested'),
                    detail__operation_id=operation).first()
                if previous and (previous.action != 'store_payroll.confirm_requested' or previous.account_id != actor.pk
                        or previous.target_store_slug != slug or previous.detail.get('request_hash') != digest):
                    raise StorePayrollError('请求编号已用于其他工资操作')
                PlatformAuditEvent.objects.create(account=actor, action='store_payroll.confirm_requested',
                    target_store_slug=slug, target_type='store_payroll', target_id='confirm',
                    detail={'operation_id': operation, 'request_hash': digest, 'month': month.isoformat()})
            with directory_transaction.atomic(using='default'):
                actor, store = _authorized(request.auth, slug, 'salary:calculate')
                with store_context(store), tenant_transaction.atomic():
                    row, result, created = confirm_payroll_locked(month, supplied, platform_id=actor.pk,
                        request_id=request_id, request_hash=digest)
                    audits = list(AdminAuditLog.objects.filter(resource='platform-store-payroll-confirm',
                        object_id=str(row.pk), request_body__operation_id=operation))
                    if audits:
                        if (len(audits) != 1 or audits[0].request_body.get('platform_account_id') != str(actor.pk)
                                or audits[0].request_body.get('request_hash') != digest
                                or audits[0].request_body.get('fingerprint') != supplied):
                            raise StorePayrollError('工资确认审计凭证待核对')
                    else:
                        if not created:
                            raise StorePayrollError('工资确认审计凭证缺失')
                        AdminAuditLog.objects.create(operator_name=f'平台账号 {actor.pk}', method='POST',
                            path=request.path[:255], resource='platform-store-payroll-confirm', object_id=str(row.pk),
                            request_body={'platform_account_id': str(actor.pk), 'operation_id': operation,
                                          'request_hash': digest, 'month': month.isoformat(),
                                          'fingerprint': supplied}, status_code=200)
                PlatformAuditEvent.objects.create(account=actor, action='store_payroll.confirm_completed',
                    target_store_slug=slug, target_type='store_payroll', target_id=str(row.pk),
                    detail={'operation_id': operation, 'request_hash': digest, 'month': month.isoformat(),
                            'fingerprint': supplied})
            return _response(result)
        except StorePayrollError as exc:
            return Response({'code': exc.status, 'msg': exc.message}, status=exc.status)
        except (ValidationError, APIException):
            raise
        except (DatabaseError, ValueError, PayrollEventBarrierError):
            return Response({'code': 503, 'errorCode': 'PAYROLL_RESULT_UNCERTAIN',
                             'msg': '结果需要核对，请使用原请求编号重试'}, status=503)

    def put(self, request, slug, resource, plan_id=None):
        if not is_admin_host(request.get_host()):
            return Response({'code': 403, 'msg': '请从管理后台入口访问'}, status=403)
        if not ((resource == 'config' and plan_id is None) or (resource == 'plans' and plan_id is not None)):
            return Response({'code': 404, 'msg': '资源不存在'}, status=404)
        operation = uuid4().hex
        try:
            with directory_transaction.atomic(using='default'):
                actor, store = _authorized(request.auth, slug, 'salary:configure')
                PlatformAuditEvent.objects.create(account=actor, action='store_payroll.configure_requested',
                    target_store_slug=slug, target_type='store_payroll', target_id=resource,
                    detail={'operation_id': operation, 'plan_id': plan_id})
            with directory_transaction.atomic(using='default'):
                actor, store = _authorized(request.auth, slug, 'salary:configure')
                with store_context(store), tenant_transaction.atomic():
                    require_payroll_configuration()
                    costing_lock()
                    if resource == 'config':
                        row = PayrollConfig.objects.select_for_update().filter(pk=1).first()
                        serializer = PayrollConfigSerializer(data=request.data)
                        serializer.is_valid(raise_exception=True)
                        values = dict(serializer.validated_data)
                        revision = values.pop('revision')
                        if revision != (row.revision if row else 0):
                            raise StorePayrollError('薪资配置已更新，请刷新核对')
                        before = dict(PayrollConfigSerializer(row).data) if row else {'night_start': None, 'night_end': None, 'revision': 0}
                        if row is None:
                            row = PayrollConfig.objects.create(pk=1, **values)
                        else:
                            row.night_start, row.night_end = values['night_start'], values['night_end']
                            row.revision += 1
                            row.save()
                        result = dict(PayrollConfigSerializer(row).data)
                    else:
                        row = SalaryPlan.objects.select_for_update().select_related('position').filter(pk=plan_id).first()
                        if row is None:
                            raise StorePayrollError('薪资方案不存在', 404)
                        serializer = SalaryPlanSerializer(row, data=request.data, partial=True)
                        serializer.is_valid(raise_exception=True)
                        values = dict(serializer.validated_data)
                        if values.pop('revision', None) != row.revision:
                            raise StorePayrollError('薪资方案已更新，请刷新核对')
                        before = dict(SalaryPlanSerializer(row).data)
                        for key, value in values.items():
                            setattr(row, key, value)
                        row.revision += 1
                        row.save()
                        result = dict(SalaryPlanSerializer(row).data)
                    # IDs and immutable platform identity remain evidence; no store account is impersonated.
                    AdminAuditLog.objects.create(operator_name=f'平台账号 {actor.pk}', method='PUT',
                        path=request.path[:255], resource='platform-store-payroll', object_id=str(row.pk),
                        request_body={'platform_account_id': str(actor.pk), 'operation_id': operation,
                                      'before': before, 'after': result}, status_code=200)
                PlatformAuditEvent.objects.create(account=actor, action='store_payroll.configure_completed',
                    target_store_slug=slug, target_type='store_payroll', target_id=resource,
                    detail={'operation_id': operation, 'plan_id': plan_id, 'revision': row.revision})
            return _response(result)
        except StorePayrollError as exc:
            return Response({'code': exc.status, 'msg': exc.message}, status=exc.status)
        except (ValidationError, APIException):
            raise
        except (DatabaseError, ValueError):
            return Response({'code': 503, 'errorCode': 'PAYROLL_RESULT_UNCERTAIN',
                             'msg': '结果需要核对，请重新读取门店配置'}, status=503)
