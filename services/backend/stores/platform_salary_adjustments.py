"""Audited platform salary corrections and applications, stored in control DB."""
from datetime import date
from uuid import UUID

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError

from .models import (PlatformAccount, PlatformAuditEvent, PlatformSalaryAdjustment, PlatformSalaryAdjustmentWriteOff,
                     PlatformSalaryAdjustmentApplication, PlatformSalaryConfirmation, PlatformSalaryPeriod)
from .platform_auth import IsPlatformAuthenticated, PlatformAuthentication
from .platform_salary import amount, can_access, locked_actor_and_target, month_value


def next_month(value):
    return date(value.year + (value.month == 12), 1 if value.month == 12 else value.month + 1, 1)


def remaining(row):
    applied = row.applications.aggregate(total=Sum('amount_fen'))['total'] or 0
    written_off = PlatformSalaryAdjustmentWriteOff.objects.filter(adjustment=row).aggregate(total=Sum('amount_fen'))['total'] or 0
    if applied + written_off > row.amount_fen:
        raise ValueError('薪资调整记录异常，请核对')
    return row.amount_fen - applied - written_off


def due_adjustments(account, month):
    rows = PlatformSalaryAdjustment.objects.select_for_update().filter(account=account, due_month__lte=month).order_by('created_at', 'pk')
    return [(row, value) for row in rows if (value := remaining(row)) > 0]


def calculate_applications(account, month, base_fen, performance_fen):
    rows = due_adjustments(account, month)
    credits = [(row, value) for row, value in rows if row.kind == 'CREDIT']
    deductions = [(row, value) for row, value in rows if row.kind == 'DEBIT']
    increase = sum(value for _, value in credits)
    available = base_fen + performance_fen + increase
    allocations = list(credits)
    deducted = 0
    for row, value in deductions:
        used = min(value, available)
        if used:
            allocations.append((row, used))
            available -= used
            deducted += used
    return increase, deducted, available, allocations


def first_open_month(original_month, account):
    month = max(next_month(original_month), timezone.localdate().replace(day=1))
    while (PlatformSalaryPeriod.objects.filter(month=month, closed_at__isnull=False).exists()
           or PlatformSalaryConfirmation.objects.filter(month=month, account=account).exists()):
        month = next_month(month)
    return month


def serialize_adjustment(row):
    return {'id': str(row.pk), 'request_id': str(row.request_id), 'account_id': str(row.account_id),
            'original_month': row.original.month.strftime('%Y-%m'), 'due_month': row.due_month.strftime('%Y-%m'),
            'kind': row.kind, 'amount_fen': row.amount_fen, 'remaining_fen': remaining(row),
            'deduction_source': row.deduction_source, 'reason': row.reason, 'created_at': row.created_at.isoformat()}


class PlatformSalaryAdjustmentView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def get(self, request):
        if not can_access(request, 'platform_salary:view'):
            return Response({'code': 403, 'msg': '无平台薪资权限'}, status=403)
        from .finance_paging import descending_created
        rows = descending_created(PlatformSalaryAdjustment.objects.select_related('original'))
        response = Response({'code': 0, 'data': {'results': [serialize_adjustment(row) for row in rows]}})
        response['Cache-Control'] = 'no-store'
        return response

    def post(self, request):
        if not can_access(request, 'platform_salary:configure'):
            return Response({'code': 403, 'msg': '无平台薪资配置权限'}, status=403)
        try:
            if set(request.data) != {'request_id', 'account_id', 'original_month', 'kind', 'amount_fen', 'reason', 'deduction_source'}:
                raise ValueError('调整信息不完整')
            request_id = UUID(str(request.data['request_id']))
            account_id = UUID(str(request.data['account_id']))
            original_month = month_value(request.data['original_month'])
            kind, value, reason = request.data['kind'], amount(request.data['amount_fen']), request.data['reason']
            source = request.data['deduction_source']
            if kind not in ('CREDIT', 'DEBIT') or value <= 0 or not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
                raise ValueError('请填写有效调整类型、金额和原因')
            if (kind == 'CREDIT' and source != '') or (kind == 'DEBIT' and source not in ('REFUND_COMMISSION', 'OTHER')):
                raise ValueError('请明确扣减来源')
            reason = reason.strip()
            with transaction.atomic(using='default'):
                from .database_identity import lock_store_registration
                lock_store_registration()
                actor, target = locked_actor_and_target(request, 'platform_salary:configure', account_id)
                existing = PlatformSalaryAdjustment.objects.select_related('original').filter(request_id=request_id).first()
                if existing:
                    if (existing.account_id != target.pk or existing.original.month != original_month
                            or existing.kind != kind or existing.amount_fen != value or existing.reason != reason
                            or existing.deduction_source != source):
                        return Response({'code': 409, 'msg': '请求编号已用于其他调整'}, status=409)
                    return Response({'code': 0, 'data': serialize_adjustment(existing)})
                if hasattr(target, 'salary_separation'):
                    return Response({'code': 409, 'msg': '该人员已离职结清，不能新增工资调整'}, status=409)
                original = PlatformSalaryConfirmation.objects.filter(account=target, month=original_month).first()
                if not original or not PlatformSalaryPeriod.objects.filter(month=original_month, closed_at__isnull=False).exists():
                    return Response({'code': 409, 'msg': '原月份须已有该人员已关账工资快照'}, status=409)
                due = first_open_month(original_month, target)
                row = PlatformSalaryAdjustment.objects.create(request_id=request_id, account=target, original=original,
                    kind=kind, amount_fen=value, due_month=due, reason=reason, deduction_source=source, created_by=actor)
                PlatformAuditEvent.objects.create(account=actor, action='platform_salary.adjust',
                    target_type='platform_salary_adjustment', target_id=str(row.pk),
                    detail={'account_id': str(target.pk), 'original_month': original_month.isoformat(),
                            'due_month': due.isoformat(), 'kind': kind, 'amount_fen': value,
                            'deduction_source': source, 'reason': reason})
            return Response({'code': 0, 'data': serialize_adjustment(row)})
        except (ValueError, TypeError, AttributeError) as exc:
            return Response({'code': 400, 'msg': str(exc) or '调整信息无效'}, status=400)
        except (PermissionError, TokenError):
            return Response({'code': 403, 'msg': '无平台薪资配置权限'}, status=403)
