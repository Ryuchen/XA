"""Traceable refund commission deductions and separation write-offs."""
from datetime import time
from types import SimpleNamespace
from collections import defaultdict

from django.conf import settings
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from club_accounts.models import ClubAccount
from .permissions import get_account_permissions
from orders.models import Order, OrderRefund, OrderAddonRefund
from orders.refunds import PAID_STATES
from .payroll_calculation import is_night, month_bounds, raw_to_fen, round_ratio
from .payroll_models import (PayrollLock, PayrollRefundApplication, PayrollRefundDeduction,
                             PayrollRefundPerformanceAdjustment, PayrollRefundPerformanceApplication,
                             PayrollRefundPerformanceRevision,
                             PayrollRefundWriteOff, PayrollSeparationSettlement, PayrollPerformanceSeparationWaiver, PayrollReemployment, PayrollRun,
                             SalaryActivity, SalaryAssignment)


def _lock():
    PayrollLock.objects.get_or_create(pk=1)
    PayrollLock.objects.select_for_update().get(pk=1)


def _line(run, account_id):
    line = next((item for item in run.snapshot.get('lines', []) if item.get('account') == account_id), None)
    if line is None:
        raise ValidationError('已确认工资未包含该员工')
    return line


def _performance_separation(account_id, original_month, refund_at, *, cutoff=None):
    """Choose the latest closeout already complete when this refund posted."""
    query = PayrollSeparationSettlement.objects.filter(account_id=account_id,
        final_run__month__gte=original_month, confirmed_at__lt=refund_at)
    if cutoff is not None:
        query = query.filter(confirmed_at__lte=cutoff)
    return query.order_by('-confirmed_at', '-pk').first()


def derive_confirmed_refunds(run_id, account_id, *, cutoff=None, period_end=None):
    """Compute stable per-refund effects without mutating a payroll preview."""
    from .payroll_refund_performance import (verify_addon_refund_wallet_event, verify_refund_wallet_event,
                                              refund_event_queryset, refund_event_time)

    def verify(refund):
        if refund.amount:
            tx = (verify_addon_refund_wallet_event if isinstance(refund, OrderAddonRefund)
                  else verify_refund_wallet_event)(refund)
            if ((cutoff is not None and tx.created_at > cutoff)
                    or (period_end is not None and tx.created_at >= period_end)):
                raise ValidationError('退款实际入账跨越工资核算截止时间，请先核对')

    run = PayrollRun.objects.get(pk=run_id)
    line = _line(run, account_id)
    plan = line['plan_snapshot']
    config = run.snapshot['config']
    night = SimpleNamespace(night_start=time.fromisoformat(config['night_start']) if config['night_start'] else None,
                            night_end=time.fromisoformat(config['night_end']) if config['night_end'] else None)
    groups = {'DAY': [], 'NIGHT': []}
    activities = SalaryActivity.objects.filter(pk__in=run.snapshot['activity_ids'], account_id=account_id,
        kind='DISPATCH', is_void=False, recognition_month__isnull=True).order_by('pk')
    _, original_end = month_bounds(run.month)
    def applicable(refunds):
        refunds = refund_event_queryset(refunds, addon=refunds.model is OrderAddonRefund).filter(
            Q(payroll_refund_time__gt=run.confirmed_at) | Q(payroll_refund_time__gte=original_end))
        if cutoff is not None:
            refunds = refunds.filter(payroll_refund_time__lte=cutoff)
        if period_end is not None:
            refunds = refunds.filter(payroll_refund_time__lt=period_end)
        return refunds

    for activity in activities:
        shift = is_night(activity.performed_at, night)
        if shift is None and plan['dispatch_day_bps'] != plan['dispatch_night_bps']:
            raise ValidationError('原工资缺少日夜班规则，无法计算退款提成')
        bucket = 'NIGHT' if shift else 'DAY'
        for refund in applicable(OrderRefund.objects.filter(order_id=activity.order_id)):
            groups[bucket].append((refund, activity))
    # Add-on revenue is recognized in the add-on payment month even when the
    # underlying order was dispatched and paid in an earlier month.
    for source in line.get('addon_sources', []):
        if source['bucket'] not in groups:
            continue
        activity = SalaryActivity.objects.get(pk=source['activity_id'], account_id=account_id, is_void=False)
        for refund in applicable(OrderAddonRefund.objects.filter(payment_id=source['payment_id'])):
            groups[source['bucket']].append((refund, activity))
    rows = []
    for bucket, events in groups.items():
        baseline = line['dispatch_night_amount_fen' if bucket == 'NIGHT' else 'dispatch_day_amount_fen']
        rate = plan['dispatch_night_bps' if bucket == 'NIGHT' else 'dispatch_day_bps']
        previous, total_refunded = 0, 0
        for refund, activity in sorted(events, key=lambda item: (refund_event_time(item[0]), item[0]._meta.model_name, item[0].pk)):
            verify(refund)
            value = raw_to_fen(refund.amount)
            total_refunded += value
            if total_refunded > baseline:
                raise ValidationError('退款超过已确认的对应派单业绩，请核对原工资')
            cumulative = round_ratio(baseline, rate, 10000) - round_ratio(baseline - total_refunded, rate, 10000)
            rows.append({'refund': refund, 'activity': activity, 'original_run_id': run.pk,
                         'account_id': account_id, 'bucket': bucket, 'rate_bps': rate,
                         'refund_fen': value, 'amount_fen': cumulative - previous})
            previous = cumulative
    # A late dispatch is paid in the recognition run, not in the original run.
    # Its recorded source basis and original rate remain fixed after confirmation.
    late_groups = defaultdict(list)
    for terms in line.get('late_adjustments', []):
        if terms.get('kind') != 'DISPATCH':
            continue
        activity = SalaryActivity.objects.get(pk=terms['activity_id'], account_id=account_id, is_void=False)
        late_groups[(terms['original_run_id'], terms['bucket'], terms['rate_bps'], terms.get('reemployment_plan_id'))].append((activity, terms))
    for terms in line.get('late_addon_sources', []):
        if terms['bucket'] == 'NONE':
            continue
        activity = SalaryActivity.objects.get(pk=terms['activity_id'], account_id=account_id, is_void=False)
        late_groups[(terms['original_run_id'], terms['bucket'], terms['rate_bps'], terms.get('reemployment_plan_id'))].append(
            (activity, {**terms, 'kind': 'ADDON'}))
    for (_, bucket, rate, _), items in late_groups.items():
        items.sort(key=lambda item: (item[1].get('kind') == 'ADDON',
                                     item[1].get('payment_id', item[0].pk)))
        offset = items[0][1]['original_basis_fen']
        baseline = sum(terms['commission_basis_fen'] for _, terms in items)
        events, remaining = [], {}
        for activity, terms in items:
            addon = terms.get('kind') == 'ADDON'
            source_key = ('ADDON', terms['payment_id']) if addon else ('ORDER', activity.pk)
            remaining[source_key] = terms['commission_basis_fen']
            refunds = (OrderAddonRefund.objects.filter(payment_id=terms['payment_id']) if addon
                       else OrderRefund.objects.filter(order_id=activity.order_id))
            events.extend((refund, activity, source_key) for refund in applicable(refunds))
        previous, total_refunded = 0, 0
        for refund, activity, source_key in sorted(events, key=lambda item: (refund_event_time(item[0]), item[0]._meta.model_name, item[0].pk)):
            verify(refund)
            value = raw_to_fen(refund.amount)
            remaining[source_key] -= value
            if remaining[source_key] < 0:
                raise ValidationError('退款超过迟核派单已补发的实付金额')
            total_refunded += value
            cumulative = (round_ratio(offset + baseline, rate, 10000)
                          - round_ratio(offset + baseline - total_refunded, rate, 10000))
            rows.append({'refund': refund, 'activity': activity, 'original_run_id': run.pk,
                         'account_id': account_id, 'bucket': bucket, 'rate_bps': rate,
                         'refund_fen': value, 'amount_fen': cumulative - previous})
            previous = cumulative
    return rows


@transaction.atomic
def capture_confirmed_refunds(run_id, account_id, *, cutoff=None, period_end=None):
    """Record each subsequent refund against the original confirmed commission."""
    _lock()
    run = PayrollRun.objects.get(pk=run_id)
    rows = []
    for item in derive_confirmed_refunds(run_id, account_id, cutoff=cutoff, period_end=period_end):
        from .payroll_refund_performance import refund_event_time
        occurred = refund_event_time(item['refund'])
        if (cutoff is not None and occurred > cutoff) or (period_end is not None and occurred >= period_end):
            continue
        expected = {key: value for key, value in item.items() if key not in ('refund', 'activity')}
        source = {'addon_refund': item['refund']} if isinstance(item['refund'], OrderAddonRefund) else {'refund': item['refund']}
        row, created = PayrollRefundDeduction.objects.get_or_create(
            **source, activity=item['activity'], defaults=expected)
        if not created and any(getattr(row, key) != value for key, value in expected.items()):
            raise ValidationError('已登记退款提成与原工资记录不一致')
        settlement = PayrollSeparationSettlement.objects.filter(account_id=account_id,
            final_run__month__gte=run.month).order_by('final_run__month').first()
        if settlement:
            _write_off(row, settlement)
        rows.append(row)
    return rows


def _plan(account_id, month, gross_fen, *, run_id=None):
    from .payroll_refund_performance import refund_event_time
    if type(gross_fen) is not int or gross_fen < 0 or month.day != 1:
        raise ValidationError('工资金额或月份无效')
    if PayrollRun.objects.filter(month=month).exclude(pk=run_id).exists():
        raise ValidationError('该月工资已确认')
    if PayrollRefundApplication.objects.filter(deduction__account_id=account_id, run__month__gte=month).exclude(run_id=run_id).exists():
        raise ValidationError('请按工资月份顺序核算退款扣回')
    if PayrollRefundPerformanceApplication.objects.filter(adjustment__account_id=account_id,
            run__month__gte=month).exclude(run_id=run_id).exists():
        raise ValidationError('请按工资月份顺序核算退款绩效调整')
    _, end = month_bounds(month)
    cutoff = PayrollRun.objects.get(pk=run_id).confirmed_at if run_id else timezone.now()
    from .payroll_refund_performance import closed_period_refund_performance
    rows, revisions = [], []
    for original in PayrollRun.objects.filter(month__lt=month).order_by('month'):
        line = next((item for item in original.snapshot.get('lines', []) if item.get('account') == account_id), None)
        if not line or 'plan_snapshot' not in line:
            continue
        for item in derive_confirmed_refunds(original.pk, account_id, cutoff=cutoff, period_end=end):
            refund = item['refund']
            source = {'addon_refund': refund} if isinstance(refund, OrderAddonRefund) else {'refund': refund}
            existing = PayrollRefundDeduction.objects.filter(**source, activity=item['activity']).first()
            settlement = PayrollSeparationSettlement.objects.filter(account_id=account_id,
                final_run__month__gte=original.month, confirmed_at__lte=cutoff).exists()
            rows.append(('COMMISSION', original, refund, item['activity'].pk, item['amount_fen'], existing, settlement))
        for item in closed_period_refund_performance(original.pk, cutoff=cutoff, period_end=end):
            if item['account_id'] != account_id:
                continue
            addon = item['refund_origin'] == 'ADDON'
            refund = (OrderAddonRefund if addon else OrderRefund).objects.get(pk=item['refund_id'])
            separation = _performance_separation(account_id, original.month, refund_event_time(refund), cutoff=cutoff)
            if separation:
                continue
            source = {'addon_refund': refund} if addon else {'refund': refund}
            existing = PayrollRefundPerformanceAdjustment.objects.filter(**source,
                original_run=original, account_id=account_id).first()
            corrected = -item['adjustment_fen']
            processed = False
            if existing:
                processed = existing.applications.exclude(run_id=run_id).exists()
                prior_delta = existing.revisions.exclude(run_id=run_id).aggregate(total=Sum('delta_fen'))['total'] or 0
                delta = existing.amount_fen - prior_delta - corrected
                if delta:
                    paid_before = existing.applications.exclude(run_id=run_id).aggregate(total=Sum('amount_fen'))['total'] or 0
                    applied_before = existing.revisions.exclude(run_id=run_id).aggregate(total=Sum('applied_fen'))['total'] or 0
                    revisions.append({'adjustment_id': existing.pk, 'delta_fen': delta,
                        'corrected_amount_fen': corrected, 'before_fen': item['performance_before_fen'],
                        'after_fen': item['performance_after_fen'], 'processed': processed,
                        'refundable_fen': max(0, paid_before - applied_before)})
            rows.append(('PERFORMANCE', original, refund, None,
                         existing.amount_fen if processed else corrected, existing, False))
    rows = [row for row in rows if refund_event_time(row[2]) < end and refund_event_time(row[2]) <= cutoff]
    # A performance adjustment is only collectible in this run; an unpaid
    # commission deduction remains collectible later. Consume this run's
    # available salary for non-carryable performance first, preserving the
    # original event order within each category.
    rows.sort(key=lambda item: (item[0] != 'PERFORMANCE', refund_event_time(item[2]),
                                item[2]._meta.model_name, item[2].pk, item[1].pk))
    remaining, entries = 0, []
    for item in revisions:
        item['applied_fen'] = (min(item['delta_fen'], max(0, item['refundable_fen'] - item['corrected_amount_fen']))
                               if item['processed'] and item['delta_fen'] > 0 else 0)
        item['waived_fen'] = (item['delta_fen'] - item['applied_fen']
                              if item['processed'] and item['delta_fen'] > 0 else 0)
    credit = sum(max(0, item['applied_fen']) for item in revisions)
    debit = sum(-item['delta_fen'] for item in revisions if item['processed'] and item['delta_fen'] < 0)
    available = gross_fen + credit
    revision_taken = 0
    for item in revisions:
        if item['processed'] and item['delta_fen'] < 0:
            requested = -item['delta_fen']
            taken = min(available, requested)
            item['applied_fen'], item['waived_fen'] = -taken, requested - taken
            available -= taken
            revision_taken += taken
    for kind, original, refund, activity_id, amount, row, settlement in rows:
        paid = row.applications.exclude(run_id=run_id).aggregate(total=Sum('amount_fen'))['total'] or 0 if row else 0
        waived = (row.writeoffs.aggregate(total=Sum('amount_fen'))['total'] or 0) if row and kind == 'COMMISSION' else 0
        outstanding = 0 if settlement else amount - paid - waived
        if kind == 'PERFORMANCE' and row and row.applications.exclude(run_id=run_id).exists():
            outstanding = 0
        take = min(available, outstanding)
        if take or (kind == 'PERFORMANCE' and outstanding >= 0 and not (
                row and row.applications.exclude(run_id=run_id).exists())):
            entries.append({'kind': kind, 'original_run_id': original.pk, 'refund_id': refund.pk,
                            'refund_origin': 'ADDON' if isinstance(refund, OrderAddonRefund) else 'ORDER',
                            'activity_id': activity_id, 'amount_fen': take,
                            **({'waived_fen': outstanding - take} if kind == 'PERFORMANCE' else {})})
            available -= take
        if kind == 'COMMISSION':
            remaining += outstanding - take
    return {'gross_fen': gross_fen, 'deducted_fen': gross_fen + credit - available,
            'net_fen': available, 'carry_fen': remaining, 'entries': entries,
            'performance_revisions': revisions, 'revision_credit_fen': credit,
            'revision_debit_fen': debit, 'revision_taken_fen': revision_taken}


@transaction.atomic
def preview_refund_deductions(account_id, month, gross_fen):
    _lock()
    return _plan(account_id, month, gross_fen)


def attach_refund_deductions(run, account_id):
    """Write the previewed deductions atomically with the caller's new payroll run.

    The new run must already contain the exact resulting plan in its employee
    snapshot.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('工资及退款扣回必须在同一事务确认')
    _lock()
    line = _line(run, account_id)
    saved = line.get('refund_deduction_plan')
    if not isinstance(saved, dict):
        raise ValidationError('工资快照缺少退款扣回计划')
    from .payroll_refund_performance import capture_closed_period_refund_performance
    _, period_end = month_bounds(run.month)
    for separation in PayrollSeparationSettlement.objects.filter(account_id=account_id,
            final_run__month__lt=run.month, confirmed_at__lte=run.confirmed_at):
        capture_separation_performance_waivers(separation, cutoff=run.confirmed_at, period_end=period_end)
    for original in PayrollRun.objects.filter(month__lt=run.month).order_by('month'):
        source_line = next((item for item in original.snapshot.get('lines', []) if item.get('account') == account_id), None)
        if source_line and 'plan_snapshot' in source_line:
            capture_confirmed_refunds(original.pk, account_id, cutoff=run.confirmed_at, period_end=period_end)
            capture_closed_period_refund_performance(original.pk, cutoff=run.confirmed_at, period_end=period_end)
    plan = _plan(account_id, run.month, saved.get('gross_fen'), run_id=run.pk)
    if (saved != plan or line.get('total_fen') != plan['net_fen']):
        raise ValidationError('退款或工资已变化，请重新预览')
    for revision in plan['performance_revisions']:
        PayrollRefundPerformanceRevision.objects.create(
            adjustment_id=revision['adjustment_id'], run=run, delta_fen=revision['delta_fen'],
            corrected_amount_fen=revision['corrected_amount_fen'], applied_fen=revision['applied_fen'],
            waived_fen=revision['waived_fen'],
            evidence={'before_fen': revision['before_fen'], 'after_fen': revision['after_fen'],
                      'previously_processed': revision['processed'],
                      'revision_debit_total_fen': plan['revision_debit_fen'],
                      'revision_debit_taken_fen': plan['revision_taken_fen']})
    for entry in plan['entries']:
        source_field = 'addon_refund_id' if entry['refund_origin'] == 'ADDON' else 'refund_id'
        if entry['kind'] == 'COMMISSION':
            deduction = PayrollRefundDeduction.objects.get(**{source_field: entry['refund_id']}, activity_id=entry['activity_id'],
                                                           original_run_id=entry['original_run_id'])
            row, created = PayrollRefundApplication.objects.get_or_create(
                deduction=deduction, run=run, defaults={'amount_fen': entry['amount_fen']})
        else:
            adjustment = PayrollRefundPerformanceAdjustment.objects.get(**{source_field: entry['refund_id']},
                original_run_id=entry['original_run_id'], account_id=account_id)
            row, created = PayrollRefundPerformanceApplication.objects.get_or_create(
                adjustment=adjustment, run=run, defaults={'amount_fen': entry['amount_fen'],
                                                         'waived_fen': entry['waived_fen']})
        if not created and (row.amount_fen != entry['amount_fen']
                            or (entry['kind'] == 'PERFORMANCE' and row.waived_fen != entry['waived_fen'])):
            raise ValidationError('工资退款扣回记录不一致')
    return plan


def capture_separation_performance_waivers(separation, *, cutoff=None, period_end=None):
    """Persist real post-closeout refunds without requiring another employee payroll."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('离职绩效免追须在工资事务内记录')
    _lock()
    from .payroll_refund_performance import capture_closed_period_refund_performance, refund_event_time
    count = 0
    for original in PayrollRun.objects.filter(month__lte=separation.final_run.month).order_by('month'):
        if not any(item.get('account') == separation.account_id for item in original.snapshot.get('lines', [])):
            continue
        adjustments = capture_closed_period_refund_performance(original.pk, cutoff=cutoff,
            period_end=period_end, account_id=separation.account_id)
        for adjustment in adjustments:
            refund = adjustment.refund or adjustment.addon_refund
            selected = _performance_separation(separation.account_id, original.month,
                refund_event_time(refund), cutoff=cutoff)
            if not selected or selected.pk != separation.pk:
                continue
            if adjustment.applications.exists() or adjustment.revisions.exists():
                raise ValidationError('离职后退款绩效已有工资处理记录，请先核对')
            waiver, created = PayrollPerformanceSeparationWaiver.objects.get_or_create(
                adjustment=adjustment, defaults={'settlement': separation, 'amount_fen': adjustment.amount_fen})
            if waiver.settlement_id != separation.pk or waiver.amount_fen != adjustment.amount_fen:
                raise ValidationError('离职绩效免追记录与退款来源不一致')
            count += int(created)
    return count


def _write_off(deduction, settlement):
    paid = deduction.applications.aggregate(total=Sum('amount_fen'))['total'] or 0
    waived = deduction.writeoffs.aggregate(total=Sum('amount_fen'))['total'] or 0
    remaining = deduction.amount_fen - paid - waived
    if remaining > 0:
        PayrollRefundWriteOff.objects.create(deduction=deduction, settlement=settlement, amount_fen=remaining)


@transaction.atomic
def register_payroll_reemployment(*, account_id, separation_id, actor, reason):
    from .payroll_event_barrier import lock_payroll_events
    from .permissions import is_console_account
    if not getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
        raise ValidationError('重新入职登记需要工资事件屏障')
    lock_payroll_events()
    _lock()
    if not actor or 'salary:confirm' not in get_account_permissions(actor):
        raise ValidationError('无权登记员工重新入职')
    if not isinstance(reason, str) or not 2 <= len(reason.strip()) <= 500:
        raise ValidationError('请填写重新入职原因')
    account = ClubAccount.objects.select_for_update().filter(pk=account_id, account_type='STAFF').first()
    if not account or not account.is_active or not account.can_login or not is_console_account(account):
        raise ValidationError('员工后台账号尚未启用，不能登记重新入职')
    last = PayrollSeparationSettlement.objects.filter(account=account).order_by('-confirmed_at', '-pk').first()
    if not last or last.pk != separation_id:
        raise ValidationError('请核对最近一次离职结清记录')
    existing = PayrollReemployment.objects.filter(separation=last).first()
    if existing:
        if existing.account_id != account.pk or existing.registered_by_id != actor.pk or existing.reason != reason.strip():
            raise ValidationError('该结清已有不同的重新入职登记')
        return existing
    return PayrollReemployment.objects.create(separation=last, account=account, registered_by=actor, reason=reason.strip())


@transaction.atomic
def register_reemployment_plan(*, reemployment_id, plan_id, revision, actor):
    from .payroll_event_barrier import lock_payroll_events
    from .payroll_models import PayrollConfig, PayrollReemploymentPlan, SalaryPlan
    from .payroll_calculation import plan_snapshot
    from .permissions import is_console_account
    if not getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
        raise ValidationError('重入薪资方案需要工资事件屏障')
    lock_payroll_events()
    _lock()
    if not actor or not actor.is_active or not actor.can_login or 'salary:confirm' not in get_account_permissions(actor):
        raise ValidationError('无权配置重入薪资方案')
    reemployment = PayrollReemployment.objects.select_related('account', 'separation').filter(pk=reemployment_id).first()
    if (not reemployment or not reemployment.account.is_active or not reemployment.account.can_login
            or not is_console_account(reemployment.account)):
        raise ValidationError('重入账号当前不可配置工资')
    latest = PayrollSeparationSettlement.objects.filter(account_id=reemployment.account_id).order_by('-confirmed_at', '-pk').first()
    if not latest or latest.pk != reemployment.separation_id:
        raise ValidationError('员工重入周期已变化，请刷新后重试')
    plan = SalaryPlan.objects.select_for_update().filter(pk=plan_id, is_active=True).first()
    if not plan or plan.revision != revision:
        raise ValidationError('薪资方案不可用或已变化，请刷新后重试')
    last_plan = PayrollReemploymentPlan.objects.filter(reemployment=reemployment).order_by('-effective_at', '-pk').first()
    config = PayrollConfig.objects.select_for_update().filter(pk=1).first()
    night = {'night_start': config.night_start.isoformat(timespec='minutes') if config and config.night_start else None,
             'night_end': config.night_end.isoformat(timespec='minutes') if config and config.night_end else None,
             'revision': config.revision if config else 0}
    if (last_plan and last_plan.plan_id == plan.pk and last_plan.plan_revision == plan.revision
            and last_plan.night_config_snapshot == night):
        return last_plan
    return PayrollReemploymentPlan.objects.create(reemployment=reemployment, plan=plan,
        plan_revision=plan.revision, plan_snapshot=plan_snapshot(plan), night_config_snapshot=night, registered_by=actor)


@transaction.atomic
def record_offline_separation_settlement(*, run_id, account_id, actor, settled_fen, reference, note,
                                         all_wages_settled):
    """Record the explicit offline closeout and forgive remaining old refund commissions.

    The operator attests all prior wages were paid; salary
    confirmation on its own is not treated as an offline payment.
    """
    from .payroll_calculation import refund_accounting_enabled
    if not refund_accounting_enabled() or not getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
        raise ValidationError('离职结清需要启用退款工资核算和工资事件屏障，请先完成门店配置')
    if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
        from .payroll_event_barrier import lock_payroll_events
        lock_payroll_events()
    _lock()
    if (not actor or not actor.is_active or not actor.can_login
            or 'salary:confirm' not in get_account_permissions(actor)):
        raise ValidationError('无权确认员工薪资结清')
    account = ClubAccount.objects.get(pk=account_id)
    membership = getattr(account, 'admin_membership', None)
    if account.is_active and account.can_login and membership and membership.is_active:
        raise ValidationError('员工仍在职，不能办理离职结清')
    run = PayrollRun.objects.get(pk=run_id)
    line = _line(run, account_id)
    if (all_wages_settled is not True or type(settled_fen) is not int or settled_fen < 0
            or line.get('total_fen') != settled_fen or not isinstance(note, str)
            or not isinstance(reference, str)):
        raise ValidationError('请核实实际结清金额并确认此前工资已结清')
    has_refund_accounting = isinstance(line.get('refund_deduction_plan'), dict)
    existing = PayrollSeparationSettlement.objects.filter(account=account, final_run=run).first()
    if existing:
        if (existing.confirmed_by_id != actor.pk or existing.settled_fen != settled_fen
                or existing.reference != reference.strip() or existing.note != note.strip()):
            raise ValidationError('该员工的结清记录已存在，请核对')
        return existing
    previous = PayrollSeparationSettlement.objects.filter(account=account).order_by('-confirmed_at', '-pk').first()
    if previous and (run.month <= previous.final_run.month or not PayrollReemployment.objects.filter(
            separation=previous, account=account).exists()):
        raise ValidationError('该员工尚未登记重新入职或最终工资期未更新')
    if SalaryAssignment.objects.filter(account=account, month__lte=run.month).exclude(
            month__in=PayrollRun.objects.values('month')).exists():
        raise ValidationError('员工仍有未确认工资月份')
    if SalaryAssignment.objects.filter(account=account, month__gt=run.month).exists():
        raise ValidationError('员工在最终工资期之后仍有薪资安排，请先核对并处理')
    for later in PayrollRun.objects.filter(month__gt=run.month).only('snapshot'):
        if any(item.get('account') == account_id for item in later.snapshot.get('lines', [])):
            raise ValidationError('员工在最终工资期之后仍有已确认工资，请使用最后一期结清')
    if not has_refund_accounting:
        # Without the refund ledger, only fixed / recharge / exam / aftersale
        # wages with no order-linked exposure can be closed safely.
        for previous in PayrollRun.objects.filter(month__lte=run.month).only('snapshot'):
            source = next((item for item in previous.snapshot.get('lines', [])
                           if item.get('account') == account_id), None)
            if source is None:
                continue
            plan = source.get('plan_snapshot')
            if (not isinstance(plan, dict) or any(plan.get(key) != 0 for key in
                    ('dispatch_day_bps', 'dispatch_night_bps', 'performance_fen'))):
                raise ValidationError('该员工存在订单提成或绩效，须完成退款调整核算后结清')
        if (PayrollRefundDeduction.objects.filter(account=account).exists() or
                PayrollRefundPerformanceAdjustment.objects.filter(account=account).exists()):
            raise ValidationError('该员工存在退款调整记录，须完成退款调整核算后结清')
    # The refund posting paths lock the order first. Serialize the closeout
    # with all of this employee's order-linked activity before testing for
    # outstanding adjustments, including later add-on refunds.
    order_ids = SalaryActivity.objects.filter(account=account, is_void=False, order__isnull=False).values('order_id')
    list(Order.objects.select_for_update().filter(
        Q(pk__in=order_ids) | Q(payment_status__in=(*PAID_STATES, 'REFUNDED'),
                                  direct_payment__isnull=True, is_gift_snapshot=False)
    ).order_by('pk').values_list('pk', flat=True))
    from .payroll_late_dispatch import activity_payroll_month
    for activity in SalaryActivity.objects.filter(account=account, is_void=False):
        period = activity_payroll_month(activity)
        recognized = PayrollRun.objects.filter(month=period).first()
        if (period > run.month or not recognized or activity.pk not in recognized.snapshot.get('activity_ids', [])
                or not any(item.get('account') == account_id for item in recognized.snapshot.get('lines', []))):
            raise ValidationError('员工仍有未结算业绩')
    from .payroll_refund_performance import capture_closed_period_refund_performance, closed_period_refund_performance
    for prior_separation in PayrollSeparationSettlement.objects.filter(account=account).select_related('final_run'):
        capture_separation_performance_waivers(prior_separation)
    for previous in (PayrollRun.objects.filter(month__lte=run.month).order_by('month')
                     if has_refund_accounting else []):
        source = next((item for item in previous.snapshot.get('lines', []) if item.get('account') == account_id), None)
        if source and 'plan_snapshot' in source:
            deductions = capture_confirmed_refunds(previous.pk, account_id)
            for deduction in deductions:
                refund = deduction.refund or deduction.addon_refund
                from .payroll_refund_performance import refund_event_time
                if deduction.amount_fen and refund_event_time(refund) > run.confirmed_at:
                    raise ValidationError('最终工资确认后出现退款提成调整，请先核算后再结清')
            capture_closed_period_refund_performance(previous.pk)
            for fact in closed_period_refund_performance(previous.pk):
                if fact['account_id'] != account_id:
                    continue
                source_key = 'addon_refund_id' if fact['refund_origin'] == 'ADDON' else 'refund_id'
                adjustment = PayrollRefundPerformanceAdjustment.objects.get(
                    **{source_key: fact['refund_id']}, original_run=previous, account=account)
                if PayrollPerformanceSeparationWaiver.objects.filter(adjustment=adjustment).exists():
                    continue
                revisions = adjustment.revisions.filter(run__month__lte=run.month).aggregate(
                    total=Sum('delta_fen'))['total'] or 0
                if adjustment.amount_fen - revisions != -fact['adjustment_fen']:
                    raise ValidationError('员工仍有未结算的绩效补正')
    for adjustment in PayrollRefundPerformanceAdjustment.objects.filter(account=account,
            original_run__month__lte=run.month, separation_waiver__isnull=True):
        application = adjustment.applications.select_related('run').filter(run__month__lte=run.month).first()
        if not application:
            raise ValidationError('员工仍有未结清绩效调整')
        prior_revision = adjustment.revisions.filter(run__month__lte=application.run.month).aggregate(
            total=Sum('delta_fen'))['total'] or 0
        if application.amount_fen + application.waived_fen != adjustment.amount_fen - prior_revision:
            raise ValidationError('员工绩效调整处理记录待核对')
    settlement = PayrollSeparationSettlement.objects.create(account=account, final_run=run,
        settled_fen=settled_fen, reference=reference.strip(), note=note.strip(), confirmed_by=actor)
    for deduction in PayrollRefundDeduction.objects.filter(account=account, original_run__month__lte=run.month):
        _write_off(deduction, settlement)
    return settlement
