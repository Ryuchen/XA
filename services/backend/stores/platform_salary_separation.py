"""Manual offline-pay facts and independently attested platform separation."""
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError

from .models import (PlatformAccount, PlatformAuditEvent, PlatformSalaryAdjustment, PlatformSalaryAdjustmentWriteOff,
                     PlatformSalaryConfirmation, PlatformSalaryPayment, PlatformSalaryPeriod, PlatformSalaryPlan,
                     PlatformSalarySeparation, PlatformSalarySupplementPayment)
from .platform_auth import IsPlatformAuthenticated, PlatformAuthentication
from .platform_salary import amount, can_access, locked_actor_and_target, month_value
from .platform_salary_adjustments import remaining


def payment_row(row):
    return {'id': str(row.pk), 'request_id': str(row.request_id), 'account_id': str(row.confirmation.account_id),
            'month': row.confirmation.month.strftime('%Y-%m'), 'amount_fen': row.amount_fen,
            'reference': row.reference, 'paid_on': row.paid_on.isoformat()}


def reconcile(account):
    """Only recorded evidence; no claim that external money moved automatically."""
    target = str(account.pk)
    periods = {row.month: row for row in PlatformSalaryPeriod.objects.all()}
    required = set(PlatformSalaryPlan.objects.filter(account=account).values_list('month', flat=True))
    required.update(row.month for row in periods.values() if target in row.roster)
    for event in PlatformAuditEvent.objects.filter(action='platform_salary.roster'):
        detail = event.detail if isinstance(event.detail, dict) else {}
        if target in detail.get('roster', []):
            try:
                required.add(parse_date(detail['month']))
            except (KeyError, TypeError, ValueError):
                raise ValueError('历史工资名单记录无法核对')
    if None in required:
        raise ValueError('历史工资名单记录无法核对')
    confirmations = {row.month: row for row in PlatformSalaryConfirmation.objects.filter(account=account)}
    required.update(confirmations)
    missing, unpaid = [], []
    for month in sorted(required):
        confirmation = confirmations.get(month)
        period = periods.get(month)
        if not confirmation or not period or not period.closed_at:
            missing.append(month.strftime('%Y-%m'))
            continue
        paid = confirmation.payments.aggregate(value=Sum('amount_fen'))['value'] or 0
        if paid > confirmation.net_fen:
            raise ValueError('工资付款记录超过应付，请人工核对')
        if paid < confirmation.net_fen:
            unpaid.append({'month': month.strftime('%Y-%m'), 'net_fen': confirmation.net_fen,
                           'paid_fen': paid, 'unpaid_fen': confirmation.net_fen - paid})
    refunds, other = [], []
    for row in PlatformSalaryAdjustment.objects.filter(account=account).order_by('created_at', 'pk'):
        value = remaining(row)
        if not value:
            continue
        entry = {'id': str(row.pk), 'kind': row.kind, 'remaining_fen': value, 'source': row.deduction_source}
        (refunds if row.kind == 'DEBIT' and row.deduction_source == 'REFUND_COMMISSION' else other).append(entry)
    return {'has_salary_evidence': bool(confirmations), 'missing_periods': missing, 'unpaid_salaries': unpaid,
            'refund_commission_debts': refunds, 'other_outstanding_adjustments': other,
            'account_disabled': not account.is_active}


class PlatformSalaryPaymentView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def get(self, request):
        if not can_access(request, 'platform_salary:view'):
            return Response({'code': 403, 'msg': '无平台薪资权限'}, status=403)
        from .finance_paging import descending_created
        rows = descending_created(PlatformSalaryPayment.objects.select_related('confirmation'))
        response = Response({'code': 0, 'data': {'results': [payment_row(row) for row in rows]}})
        response['Cache-Control'] = 'no-store'
        return response

    def post(self, request):
        if not can_access(request, 'platform_salary:confirm'):
            return Response({'code': 403, 'msg': '无平台薪资确认权限'}, status=403)
        try:
            if set(request.data) != {'request_id', 'account_id', 'month', 'amount_fen', 'reference', 'paid_on', 'payment_confirmed'}:
                raise ValueError('付款登记信息不完整')
            request_id, account_id = UUID(str(request.data['request_id'])), UUID(str(request.data['account_id']))
            month, value = month_value(request.data['month']), amount(request.data['amount_fen'])
            reference, paid_on = request.data['reference'], parse_date(request.data['paid_on'])
            if (value <= 0 or not isinstance(reference, str) or not reference.strip() or len(reference) > 200
                    or paid_on is None or paid_on > timezone.localdate() or request.data['payment_confirmed'] is not True):
                raise ValueError('请确认有效的线下付款金额、日期和凭证')
            reference = reference.strip()
            with transaction.atomic(using='default'):
                from .database_identity import lock_store_registration
                lock_store_registration()
                actor, target = locked_actor_and_target(request, 'platform_salary:confirm', account_id)
                existing = PlatformSalaryPayment.objects.select_related('confirmation').filter(request_id=request_id).first()
                if existing:
                    if (existing.confirmation.account_id != target.pk or existing.confirmation.month != month
                            or existing.amount_fen != value or existing.reference != reference or existing.paid_on != paid_on):
                        return Response({'code': 409, 'msg': '请求编号已用于其他付款'}, status=409)
                    return Response({'code': 0, 'data': payment_row(existing)})
                if PlatformSalarySeparation.objects.filter(account=target).exists():
                    return Response({'code': 409, 'msg': '该人员已离职结清'}, status=409)
                confirmation = PlatformSalaryConfirmation.objects.filter(account=target, month=month).first()
                if not confirmation or not PlatformSalaryPeriod.objects.filter(month=month, closed_at__isnull=False).exists():
                    return Response({'code': 409, 'msg': '该月工资尚未确认关账'}, status=409)
                paid = confirmation.payments.aggregate(value=Sum('amount_fen'))['value'] or 0
                if PlatformSalarySupplementPayment.objects.filter(reference=reference).exists():
                    return Response({'code': 409, 'msg': '付款凭证已登记，请核对'}, status=409)
                if paid + value > confirmation.net_fen:
                    return Response({'code': 409, 'msg': '付款金额超过该月未付工资'}, status=409)
                row = PlatformSalaryPayment.objects.create(request_id=request_id, confirmation=confirmation,
                    amount_fen=value, reference=reference, paid_on=paid_on, recorded_by=actor)
                PlatformAuditEvent.objects.create(account=actor, action='platform_salary.payment_recorded',
                    target_type='platform_salary_payment', target_id=str(row.pk), detail={'account_id': str(target.pk),
                    'month': month.isoformat(), 'amount_fen': value, 'reference': reference, 'paid_on': paid_on.isoformat()})
            return Response({'code': 0, 'data': payment_row(row)})
        except (ValueError, TypeError, AttributeError) as exc:
            return Response({'code': 400, 'msg': str(exc) or '付款登记无效'}, status=400)
        except (PermissionError, TokenError):
            return Response({'code': 403, 'msg': '无平台薪资确认权限'}, status=403)
        except IntegrityError:
            return Response({'code': 409, 'msg': '付款凭证或请求编号已登记，请核对'}, status=409)


class PlatformSalarySeparationView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def get(self, request, account_id):
        if not can_access(request, 'platform_salary:view'):
            return Response({'code': 403, 'msg': '无平台薪资权限'}, status=403)
        target = PlatformAccount.objects.filter(pk=account_id).first()
        if target is None:
            return Response({'code': 404, 'msg': '平台账号不存在'}, status=404)
        try:
            data = reconcile(target)
        except ValueError as exc:
            return Response({'code': 409, 'msg': str(exc)}, status=409)
        data['separated'] = PlatformSalarySeparation.objects.filter(account=target).exists()
        response = Response({'code': 0, 'data': data})
        response['Cache-Control'] = 'no-store'
        return response

    def post(self, request, account_id):
        if not can_access(request, 'platform_salary:confirm'):
            return Response({'code': 403, 'msg': '无平台薪资确认权限'}, status=403)
        try:
            if set(request.data) != {'request_id', 'ended_on', 'reason', 'scope_confirmed'}:
                raise ValueError('离职结清信息不完整')
            request_id, ended_on, reason = UUID(str(request.data['request_id'])), parse_date(request.data['ended_on']), request.data['reason']
            if (ended_on is None or ended_on > timezone.localdate() or not isinstance(reason, str)
                    or not reason.strip() or len(reason) > 500 or request.data['scope_confirmed'] is not True):
                raise ValueError('请明确确认离职日期及全部工资核对范围')
            reason = reason.strip()
            with transaction.atomic(using='default'):
                from .database_identity import lock_store_registration
                lock_store_registration()
                actor, target = locked_actor_and_target(request, 'platform_salary:confirm', account_id)
                existing = PlatformSalarySeparation.objects.filter(request_id=request_id).first()
                if existing:
                    if existing.account_id != target.pk or existing.ended_on != ended_on or existing.reason != reason:
                        return Response({'code': 409, 'msg': '请求编号已用于其他结清'}, status=409)
                    return Response({'code': 0, 'data': {'id': str(existing.pk), 'separated': True}})
                if PlatformSalarySeparation.objects.filter(account=target).exists():
                    return Response({'code': 409, 'msg': '该人员已离职结清'}, status=409)
                state = reconcile(target)
                if (not state['account_disabled'] or not state['has_salary_evidence'] or state['missing_periods']
                        or state['unpaid_salaries'] or state['other_outstanding_adjustments']):
                    return Response({'code': 409, 'msg': '工资或调整尚未结清，或账号尚未停用', 'data': state}, status=409)
                separation = PlatformSalarySeparation.objects.create(request_id=request_id, account=target,
                    ended_on=ended_on, reason=reason, confirmed_by=actor)
                for item in state['refund_commission_debts']:
                    PlatformSalaryAdjustmentWriteOff.objects.create(adjustment_id=item['id'], separation=separation,
                                                                   amount_fen=item['remaining_fen'])
                PlatformAuditEvent.objects.create(account=actor, action='platform_salary.separation',
                    target_type='platform_salary_separation', target_id=str(separation.pk),
                    detail={'account_id': str(target.pk), 'ended_on': ended_on.isoformat(), 'reason': reason,
                            'scope_confirmed': True, 'refund_commission_writeoffs': state['refund_commission_debts']})
            return Response({'code': 0, 'data': {'id': str(separation.pk), 'separated': True}})
        except (ValueError, TypeError, AttributeError) as exc:
            return Response({'code': 400, 'msg': str(exc) or '离职结清信息无效'}, status=400)
        except (PermissionError, TokenError):
            return Response({'code': 403, 'msg': '无平台薪资确认权限'}, status=403)
        except IntegrityError:
            return Response({'code': 409, 'msg': '结清记录已存在，请核对'}, status=409)
