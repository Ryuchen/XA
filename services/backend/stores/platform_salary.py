"""Platform-only salary and monthly ledger; no tenant-side salary writes."""
import calendar
from datetime import date
from uuid import UUID

from django.conf import settings
from django.db import DatabaseError, transaction
from django.utils.dateparse import parse_date
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError

from .context import store_context
from .hosts import is_admin_host
from .models import (PlatformAccount, PlatformAuditEvent, PlatformRoleGrant,
                     PlatformSalaryConfirmation, PlatformSalaryPeriod, PlatformSalaryPlan, PlatformSalarySupplement, Store)
from .platform_auth import IsPlatformAuthenticated, PlatformAuthentication, account_for_token
from .platform_permissions import platform_may, platform_stores_for


def month_value(value):
    if not isinstance(value, str) or len(value) != 7:
        raise ValueError('请使用 YYYY-MM 月份')
    month = parse_date(value + '-01')
    if month is None or month.strftime('%Y-%m') != value:
        raise ValueError('请使用 YYYY-MM 月份')
    return month


def amount(value):
    if type(value) is not int or not 0 <= value <= 10**12:
        raise ValueError('薪资金额须为非负整数分')
    return value


def can_access(request, action):
    return is_admin_host(request.get_host()) and platform_may(request.platform_account, action)


def locked_actor_and_target(request, action, target_id):
    accounts = {row.pk: row for row in PlatformAccount.objects.select_for_update().filter(
        pk__in=[request.platform_account.pk, target_id]).order_by('pk')}
    actor = accounts.get(request.platform_account.pk)
    target = accounts.get(target_id)
    if actor is None or target is None:
        raise ValueError('平台账号不存在')
    list(PlatformRoleGrant.objects.select_for_update().filter(account=actor))
    # Re-check token and current authorization in the control-plane transaction.
    account_for_token(request.auth)
    if not platform_may(actor, action):
        raise PermissionError
    return actor, target


def serialize(plan, confirmed):
    return {'account_id': str(plan.account_id), 'month': plan.month.strftime('%Y-%m'),
            'base_fen': plan.base_fen, 'performance_fen': plan.performance_fen,
            'confirmed': confirmed is not None,
            'adjustment_increase_fen': confirmed.adjustment_increase_fen if confirmed else None,
            'adjustment_deduction_fen': confirmed.adjustment_deduction_fen if confirmed else None,
            'net_fen': confirmed.net_fen if confirmed else None,
            'confirmed_at': confirmed.confirmed_at.isoformat() if confirmed else None}


def lock_period(month):
    # Platform account/grant management uses this same directory lock first.
    from .database_identity import lock_store_registration
    lock_store_registration()
    PlatformSalaryPeriod.objects.get_or_create(month=month)
    return PlatformSalaryPeriod.objects.select_for_update().get(month=month)


class PlatformSalaryView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def get(self, request, account_id=None):
        if not can_access(request, 'platform_salary:view'):
            return Response({'code': 403, 'msg': '无平台薪资权限'}, status=403)
        try:
            month = month_value(request.query_params.get('month'))
        except ValueError as exc:
            return Response({'code': 400, 'msg': str(exc)}, status=400)
        confirmations = {row.account_id: row for row in PlatformSalaryConfirmation.objects.filter(month=month)}
        plans = PlatformSalaryPlan.objects.filter(month=month).order_by('account_id')
        period = PlatformSalaryPeriod.objects.filter(month=month).first()
        roster = period.roster if period and period.roster_recorded else None
        planned = set(str(value) for value in plans.values_list('account_id', flat=True))
        confirmed_ids = set(str(value) for value in confirmations)
        if account_id is not None:
            plans = plans.filter(account_id=account_id)
        response = Response({'code': 0, 'data': {'month': month.strftime('%Y-%m'), 'roster': roster,
                            'missing_plans': sorted(set(roster or []) - planned),
                            'unconfirmed_accounts': sorted(set(roster or []) - confirmed_ids),
                            'results': [serialize(row, confirmations.get(row.account_id)) for row in plans]}})
        response['Cache-Control'] = 'no-store'
        return response

    def put(self, request, account_id):
        if not can_access(request, 'platform_salary:configure'):
            return Response({'code': 403, 'msg': '无平台薪资配置权限'}, status=403)
        try:
            month = month_value(request.data.get('month'))
            base, performance = amount(request.data.get('base_fen')), amount(request.data.get('performance_fen'))
            with transaction.atomic(using='default'):
                period = lock_period(month)
                if period.closed_at:
                    return Response({'code': 409, 'msg': '该月平台工资已关账'}, status=409)
                actor, target = locked_actor_and_target(request, 'platform_salary:configure', account_id)
                if hasattr(target, 'salary_separation'):
                    return Response({'code': 409, 'msg': '该人员已离职结清，不能新增工资方案'}, status=409)
                if not PlatformRoleGrant.objects.filter(account=target, role='FINANCE', scope_slug='*').exists():
                    raise ValueError('仅可为有全门店授权的平台财务配置公共薪资')
                if PlatformSalaryConfirmation.objects.filter(account=target, month=month).exists():
                    return Response({'code': 409, 'msg': '该月工资已确认'}, status=409)
                plan, created = PlatformSalaryPlan.objects.get_or_create(account=target, month=month,
                    defaults={'base_fen': base, 'performance_fen': performance, 'created_by': actor, 'updated_by': actor})
                if not created:
                    plan.base_fen, plan.performance_fen, plan.updated_by = base, performance, actor
                    plan.save(update_fields=['base_fen', 'performance_fen', 'updated_by', 'updated_at'])
                PlatformAuditEvent.objects.create(account=actor, action='platform_salary.configure',
                    target_type='platform_salary', target_id=str(plan.pk),
                    detail={'account_id': str(target.pk), 'month': month.isoformat(), 'base_fen': base,
                            'performance_fen': performance})
            return Response({'code': 0, 'data': serialize(plan, None)})
        except (ValueError, PlatformAccount.DoesNotExist) as exc:
            return Response({'code': 400, 'msg': str(exc) or '平台账号不存在'}, status=400)
        except (PermissionError, TokenError):
            return Response({'code': 403, 'msg': '无平台薪资配置权限'}, status=403)


class PlatformSalaryConfirmView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def post(self, request, account_id):
        if not can_access(request, 'platform_salary:confirm'):
            return Response({'code': 403, 'msg': '无平台薪资确认权限'}, status=403)
        try:
            month = month_value(request.data.get('month'))
            with transaction.atomic(using='default'):
                period = lock_period(month)
                if period.closed_at:
                    return Response({'code': 409, 'msg': '该月平台工资已关账'}, status=409)
                actor, target = locked_actor_and_target(request, 'platform_salary:confirm', account_id)
                plan = PlatformSalaryPlan.objects.filter(account=target, month=month).first()
                if plan is None:
                    return Response({'code': 409, 'msg': '请先配置该月薪资'}, status=409)
                confirmed = PlatformSalaryConfirmation.objects.filter(account=target, month=month).first()
                created = confirmed is None
                if created:
                    confirmed_months = PlatformSalaryConfirmation.objects.filter(account=target, month__lt=month).values('month')
                    if PlatformSalaryPlan.objects.filter(account=target, month__lt=month).exclude(month__in=confirmed_months).exists():
                        return Response({'code': 409, 'msg': '请先确认该人员更早月份的工资'}, status=409)
                    from .platform_salary_adjustments import calculate_applications
                    from .models import PlatformSalaryAdjustmentApplication
                    increase, deduction, net, applications = calculate_applications(target, month, plan.base_fen, plan.performance_fen)
                    confirmed = PlatformSalaryConfirmation.objects.create(account=target, month=month,
                        base_fen=plan.base_fen, performance_fen=plan.performance_fen,
                        adjustment_increase_fen=increase, adjustment_deduction_fen=deduction,
                        net_fen=net, confirmed_by=actor)
                    PlatformSalaryAdjustmentApplication.objects.bulk_create([
                        PlatformSalaryAdjustmentApplication(adjustment=row, confirmation=confirmed, amount_fen=value)
                        for row, value in applications])
                    PlatformAuditEvent.objects.create(account=actor, action='platform_salary.confirm',
                        target_type='platform_salary_confirmation', target_id=str(confirmed.pk),
                        detail={'account_id': str(target.pk), 'month': month.isoformat(),
                                'base_fen': confirmed.base_fen, 'performance_fen': confirmed.performance_fen,
                                'adjustment_increase_fen': increase, 'adjustment_deduction_fen': deduction, 'net_fen': net})
            return Response({'code': 0, 'data': serialize(plan, confirmed)})
        except (ValueError, PlatformAccount.DoesNotExist) as exc:
            return Response({'code': 400, 'msg': str(exc) or '平台账号不存在'}, status=400)
        except (PermissionError, TokenError):
            return Response({'code': 403, 'msg': '无平台薪资确认权限'}, status=403)


class PlatformSalaryCloseView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def post(self, request):
        if not can_access(request, 'platform_salary:confirm'):
            return Response({'code': 403, 'msg': '无平台薪资确认权限'}, status=403)
        try:
            month = month_value(request.data.get('month'))
            roster = request.data.get('account_ids')
            if (request.data.get('roster_confirmed') is not True or not isinstance(roster, list)
                    or any(not isinstance(value, str) for value in roster) or len(roster) != len(set(roster))):
                raise ValueError('请明确确认本月全部应发人员名单')
            with transaction.atomic(using='default'):
                period = lock_period(month)
                actor, _ = locked_actor_and_target(request, 'platform_salary:confirm', request.platform_account.pk)
                roster = sorted(roster)
                if period.closed_at:
                    if period.roster != roster:
                        return Response({'code': 409, 'msg': '本月已关账且人员名单不同'}, status=409)
                    return Response({'code': 0, 'data': {'month': month.strftime('%Y-%m'), 'roster': roster, 'closed': True}})
                plans = set(str(value) for value in PlatformSalaryPlan.objects.filter(month=month).values_list('account_id', flat=True))
                confirmed = set(str(value) for value in PlatformSalaryConfirmation.objects.filter(month=month).values_list('account_id', flat=True))
                if not period.roster_recorded or period.roster != roster:
                    return Response({'code': 409, 'msg': '请先确认本月应发人员名单'}, status=409)
                from .models import PlatformSalaryAdjustment
                from .platform_salary_adjustments import remaining
                pending = PlatformSalaryAdjustment.objects.filter(due_month__lte=month).select_related('account')
                if any(remaining(row) and str(row.account_id) not in roster for row in pending):
                    return Response({'code': 409, 'msg': '存在待处理薪资调整，请核对本月名单'}, status=409)
                if month == timezone.localdate().replace(day=1):
                    active = set(str(value) for value in PlatformRoleGrant.objects.filter(
                        role='FINANCE', scope_slug='*', is_active=True, account__salary_separation__isnull=True).values_list('account_id', flat=True))
                    if not active.issubset(roster):
                        return Response({'code': 409, 'msg': '本月新增平台财务，请先核对名单'}, status=409)
                if set(roster) != plans or plans != confirmed:
                    return Response({'code': 409, 'msg': '名单须覆盖全部已配置人员且均已确认工资'}, status=409)
                period.roster, period.closed_by, period.closed_at = roster, actor, timezone.now()
                period.save(update_fields=['roster', 'closed_by', 'closed_at'])
                PlatformAuditEvent.objects.create(account=actor, action='platform_salary.close',
                    target_type='platform_salary_period', target_id=str(period.pk),
                    detail={'month': month.isoformat(), 'roster': roster, 'scope_confirmed': True})
            return Response({'code': 0, 'data': {'month': month.strftime('%Y-%m'), 'roster': roster, 'closed': True}})
        except ValueError as exc:
            return Response({'code': 400, 'msg': str(exc)}, status=400)
        except (PermissionError, TokenError):
            return Response({'code': 403, 'msg': '无平台薪资确认权限'}, status=403)


class PlatformSalaryRosterView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def put(self, request):
        if not can_access(request, 'platform_salary:configure'):
            return Response({'code': 403, 'msg': '无平台薪资配置权限'}, status=403)
        try:
            month = month_value(request.data.get('month'))
            raw = request.data.get('account_ids')
            if request.data.get('scope_confirmed') is not True or not isinstance(raw, list):
                raise ValueError('请明确确认本月全部应发人员名单')
            roster = sorted(str(UUID(value)) for value in raw if isinstance(value, str))
            if len(roster) != len(raw) or len(set(roster)) != len(roster):
                raise ValueError('人员名单无效或重复')
            with transaction.atomic(using='default'):
                period = lock_period(month)
                if period.closed_at:
                    return Response({'code': 409, 'msg': '该月平台工资已关账'}, status=409)
                actor, _ = locked_actor_and_target(request, 'platform_salary:configure', request.platform_account.pk)
                eligible = set(str(value) for value in PlatformRoleGrant.objects.filter(
                    role='FINANCE', scope_slug='*', account_id__in=roster,
                    account__salary_separation__isnull=True).values_list('account_id', flat=True))
                if eligible != set(roster):
                    raise ValueError('名单中存在非全门店授权平台财务账号')
                existing = set(str(value) for value in PlatformSalaryPlan.objects.filter(month=month).values_list('account_id', flat=True))
                if not existing.issubset(roster):
                    raise ValueError('名单不能遗漏已有工资方案的人员')
                if month == timezone.localdate().replace(day=1):
                    active = set(str(value) for value in PlatformRoleGrant.objects.filter(
                        role='FINANCE', scope_slug='*', is_active=True, account__salary_separation__isnull=True).values_list('account_id', flat=True))
                    if not active.issubset(roster):
                        raise ValueError('本月名单须包含当前已授权的平台财务')
                period.roster, period.roster_recorded = roster, True
                period.save(update_fields=['roster', 'roster_recorded'])
                PlatformAuditEvent.objects.create(account=actor, action='platform_salary.roster',
                    target_type='platform_salary_period', target_id=str(period.pk),
                    detail={'month': month.isoformat(), 'roster': roster, 'scope_confirmed': True})
            return Response({'code': 0, 'data': {'month': month.strftime('%Y-%m'), 'roster': roster}})
        except (ValueError, TypeError) as exc:
            return Response({'code': 400, 'msg': str(exc) or '人员名单无效'}, status=400)
        except (PermissionError, TokenError):
            return Response({'code': 403, 'msg': '无平台薪资配置权限'}, status=403)


class PlatformLedgerView(APIView):
    authentication_classes = [PlatformAuthentication]
    permission_classes = [IsPlatformAuthenticated]

    def get(self, request):
        if not can_access(request, 'platform_ledger:view'):
            return Response({'code': 403, 'msg': '无平台总账权限'}, status=403)
        try:
            first = month_value(request.query_params.get('month'))
        except ValueError as exc:
            return Response({'code': 400, 'msg': str(exc)}, status=400)
        last = date(first.year, first.month, calendar.monthrange(first.year, first.month)[1])
        confirmations = list(PlatformSalaryConfirmation.objects.filter(month=first))
        confirmed_ids = {row.account_id for row in confirmations}
        planned_ids = set(PlatformSalaryPlan.objects.filter(month=first).values_list('account_id', flat=True))
        period = PlatformSalaryPeriod.objects.filter(month=first).first()
        roster = set(period.roster) if period and period.roster_recorded else set()
        planned_text = set(str(value) for value in planned_ids)
        missing_plans = sorted(roster - planned_text)
        missing = sorted((roster | planned_text) - set(str(value) for value in confirmed_ids))
        salary_complete = bool(period is not None and period.roster_recorded and period.closed_at
                               and set(period.roster) == set(str(value) for value in confirmed_ids)
                               and confirmed_ids == planned_ids)
        supplemental_salary = sum(PlatformSalarySupplement.objects.filter(recognition_month=first).values_list('amount_fen', flat=True))
        public_salary = sum(row.net_fen for row in confirmations) + supplemental_salary
        rows, store_profit, store_cost = [], 0, 0
        stores = platform_stores_for(request.platform_account, 'salary:view').order_by('id')
        from console.payroll_calculation import operating_roi
        for store in stores:
            row = {'slug': store.slug, 'name': store.name, 'status': 'UNAVAILABLE', 'operating': None}
            try:
                if not store.initialized_at:
                    row['status'] = 'NOT_INITIALIZED'
                    rows.append(row)
                    continue
                with store_context(store):
                    if store.database_alias not in settings.DATABASES:
                        raise ValueError('数据库不可用')
                    data = operating_roi(first, last)['operating']
                row['operating'] = data
                row['status'] = 'OK' if data['complete'] and data['profit_fen'] is not None and data['total_cost_fen'] is not None else 'INCOMPLETE'
                if row['status'] == 'OK':
                    store_profit += data['profit_fen']
                    store_cost += data['total_cost_fen']
            except (DatabaseError, ValueError):
                pass
            rows.append(row)
        complete = bool(rows) and salary_complete and not missing and all(row['status'] == 'OK' for row in rows)
        # Recheck live access after the cross-database scan.
        if not can_access(request, 'platform_ledger:view'):
            return Response({'code': 403, 'msg': '无平台总账权限'}, status=403)
        total_cost = store_cost + public_salary if complete else None
        profit = store_profit - public_salary if complete else None
        response = Response({'code': 0, 'data': {'month': first.strftime('%Y-%m'), 'currency': 'CNY',
            'stores': rows, 'platform_salary_confirmed_fen': public_salary,
            'platform_salary_supplement_fen': supplemental_salary,
            'platform_salary_roster_recorded': bool(period and period.roster_recorded),
            'platform_salary_missing_plans': missing_plans, 'platform_salary_unconfirmed_accounts': missing,
            'platform_salary_period_closed': salary_complete, 'complete': complete,
            'total_cost_fen': total_cost, 'profit_fen': profit,
            'roi': profit / total_cost if total_cost else None}})
        response['Cache-Control'] = 'no-store'
        return response
