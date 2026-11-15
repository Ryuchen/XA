"""Post-separation salary expenses and manually recorded offline payments."""
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError

from .models import (PlatformAuditEvent, PlatformSalaryConfirmation, PlatformSalaryPayment, PlatformSalaryPeriod,
                     PlatformSalarySeparation, PlatformSalarySupplement, PlatformSalarySupplementPayment)
from .platform_auth import IsPlatformAuthenticated, PlatformAuthentication
from .platform_salary import amount, can_access, locked_actor_and_target, month_value
from .platform_salary_adjustments import next_month


def serialize(row):
    paid = row.payments.aggregate(total=Sum('amount_fen'))['total'] or 0
    if paid > row.amount_fen:
        raise ValueError('补发付款记录异常，请核对')
    return {'id': str(row.pk), 'request_id': str(row.request_id), 'account_id': str(row.separation.account_id),
            'original_month': row.original_confirmation.month.strftime('%Y-%m') if row.original_confirmation else None,
            'basis_reference': row.basis_reference, 'reason': row.reason, 'amount_fen': row.amount_fen,
            'recognition_month': row.recognition_month.strftime('%Y-%m'), 'paid_fen': paid,
            'unpaid_fen': row.amount_fen - paid, 'created_at': row.created_at.isoformat()}


def serialize_payment(row):
    return {'id': str(row.pk), 'request_id': str(row.request_id), 'supplement_id': str(row.supplement_id),
            'amount_fen': row.amount_fen, 'reference': row.reference, 'paid_on': row.paid_on.isoformat()}


class PlatformSalarySupplementView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def get(self, request):
        if not can_access(request, 'platform_salary:view'):
            return Response({'code': 403, 'msg': '无平台薪资权限'}, status=403)
        from .finance_paging import descending_created
        rows = descending_created(PlatformSalarySupplement.objects.select_related('separation', 'original_confirmation'))
        response = Response({'code': 0, 'data': {'results': [serialize(row) for row in rows]}})
        response['Cache-Control'] = 'no-store'
        return response

    def post(self, request):
        if not can_access(request, 'platform_salary:configure'):
            return Response({'code': 403, 'msg': '无平台薪资配置权限'}, status=403)
        try:
            if set(request.data) != {'request_id', 'account_id', 'original_month', 'basis_reference', 'reason', 'amount_fen'}:
                raise ValueError('补发登记信息不完整')
            request_id, account_id = UUID(str(request.data['request_id'])), UUID(str(request.data['account_id']))
            original_value = request.data['original_month']
            original_month = month_value(original_value) if original_value not in (None, '') else None
            basis, reason, value = request.data['basis_reference'], request.data['reason'], amount(request.data['amount_fen'])
            if (not isinstance(basis, str) or not basis.strip() or len(basis) > 200 or not isinstance(reason, str)
                    or not reason.strip() or len(reason) > 500 or value <= 0):
                raise ValueError('请填写有效的补发依据、原因与金额')
            basis, reason = basis.strip(), reason.strip()
            with transaction.atomic(using='default'):
                from .database_identity import lock_store_registration
                lock_store_registration()
                actor, target = locked_actor_and_target(request, 'platform_salary:configure', account_id)
                prior = PlatformSalarySupplement.objects.select_related('separation', 'original_confirmation').filter(request_id=request_id).first()
                if prior:
                    old_month = prior.original_confirmation.month if prior.original_confirmation else None
                    if (prior.separation.account_id != target.pk or old_month != original_month or prior.basis_reference != basis
                            or prior.reason != reason or prior.amount_fen != value):
                        return Response({'code': 409, 'msg': '请求编号已用于其他补发'}, status=409)
                    return Response({'code': 0, 'data': serialize(prior)})
                separation = PlatformSalarySeparation.objects.filter(account=target).first()
                if not separation:
                    return Response({'code': 409, 'msg': '该人员尚未离职结清'}, status=409)
                original = None
                if original_month is not None:
                    original = PlatformSalaryConfirmation.objects.filter(account=target, month=original_month).first()
                    if original is None or original_month > separation.ended_on:
                        return Response({'code': 409, 'msg': '原月份工资或离职日期无法核对'}, status=409)
                recognition = timezone.localdate().replace(day=1)
                while PlatformSalaryPeriod.objects.filter(month=recognition, closed_at__isnull=False).exists():
                    recognition = next_month(recognition)
                row = PlatformSalarySupplement.objects.create(request_id=request_id, separation=separation,
                    original_confirmation=original, basis_reference=basis, reason=reason, amount_fen=value,
                    recognition_month=recognition, created_by=actor)
                PlatformAuditEvent.objects.create(account=actor, action='platform_salary.supplement',
                    target_type='platform_salary_supplement', target_id=str(row.pk), detail={
                        'account_id': str(target.pk), 'separation_id': str(separation.pk),
                        'original_month': original_month.isoformat() if original_month else None,
                        'basis_reference': basis, 'reason': reason, 'amount_fen': value,
                        'recognition_month': recognition.isoformat()})
            return Response({'code': 0, 'data': serialize(row)})
        except (ValueError, TypeError, AttributeError) as exc:
            return Response({'code': 400, 'msg': str(exc) or '补发登记无效'}, status=400)
        except (PermissionError, TokenError):
            return Response({'code': 403, 'msg': '无平台薪资配置权限'}, status=403)
        except IntegrityError:
            return Response({'code': 409, 'msg': '补发依据或请求编号已登记，请核对'}, status=409)


class PlatformSalarySupplementPaymentView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def get(self, request, supplement_id):
        if not can_access(request, 'platform_salary:view'):
            return Response({'code': 403, 'msg': '无平台薪资权限'}, status=403)
        rows = PlatformSalarySupplementPayment.objects.filter(supplement_id=supplement_id).order_by('created_at')
        response = Response({'code': 0, 'data': {'results': [serialize_payment(row) for row in rows]}})
        response['Cache-Control'] = 'no-store'
        return response

    def post(self, request, supplement_id):
        if not can_access(request, 'platform_salary:confirm'):
            return Response({'code': 403, 'msg': '无平台薪资确认权限'}, status=403)
        try:
            if set(request.data) != {'request_id', 'amount_fen', 'reference', 'paid_on', 'payment_confirmed'}:
                raise ValueError('付款登记信息不完整')
            request_id, value = UUID(str(request.data['request_id'])), amount(request.data['amount_fen'])
            reference, paid_on = request.data['reference'], parse_date(request.data['paid_on'])
            if (value <= 0 or not isinstance(reference, str) or not reference.strip() or len(reference) > 200
                    or paid_on is None or paid_on > timezone.localdate() or request.data['payment_confirmed'] is not True):
                raise ValueError('请确认有效的线下付款金额、日期和凭证')
            reference = reference.strip()
            with transaction.atomic(using='default'):
                from .database_identity import lock_store_registration
                lock_store_registration()
                supplement = PlatformSalarySupplement.objects.select_related('separation').filter(pk=supplement_id).first()
                if not supplement:
                    return Response({'code': 404, 'msg': '补发记录不存在'}, status=404)
                actor, _ = locked_actor_and_target(request, 'platform_salary:confirm', supplement.separation.account_id)
                prior = PlatformSalarySupplementPayment.objects.filter(request_id=request_id).first()
                if prior:
                    if (prior.supplement_id != supplement.pk or prior.amount_fen != value
                            or prior.reference != reference or prior.paid_on != paid_on):
                        return Response({'code': 409, 'msg': '请求编号已用于其他付款'}, status=409)
                    return Response({'code': 0, 'data': serialize_payment(prior)})
                if PlatformSalaryPayment.objects.filter(reference=reference).exists():
                    return Response({'code': 409, 'msg': '付款凭证已登记，请核对'}, status=409)
                paid = supplement.payments.aggregate(total=Sum('amount_fen'))['total'] or 0
                if paid + value > supplement.amount_fen:
                    return Response({'code': 409, 'msg': '付款金额超过待支付补发'}, status=409)
                row = PlatformSalarySupplementPayment.objects.create(request_id=request_id, supplement=supplement,
                    amount_fen=value, reference=reference, paid_on=paid_on, recorded_by=actor)
                PlatformAuditEvent.objects.create(account=actor, action='platform_salary.supplement_payment',
                    target_type='platform_salary_supplement_payment', target_id=str(row.pk), detail={
                        'account_id': str(supplement.separation.account_id), 'supplement_id': str(supplement.pk),
                        'amount_fen': value, 'reference': reference, 'paid_on': paid_on.isoformat()})
            return Response({'code': 0, 'data': serialize_payment(row)})
        except (ValueError, TypeError, AttributeError) as exc:
            return Response({'code': 400, 'msg': str(exc) or '付款登记无效'}, status=400)
        except (PermissionError, TokenError):
            return Response({'code': 403, 'msg': '无平台薪资确认权限'}, status=403)
        except IntegrityError:
            return Response({'code': 409, 'msg': '付款凭证或请求编号已登记，请核对'}, status=409)
