"""Post-reemployment monthly performance, attributed to actual plan intervals."""
from collections import defaultdict
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .payroll_calculation import month_bounds, round_ratio
from .payroll_models import PayrollReemployment, PayrollReemploymentPlan, PayrollRun, PayrollSeparationSettlement
from .payroll_refund_performance import monthly_payment_refund_events


def derive_reemployment_performance(month, cutoff):
    runs = list(PayrollRun.objects.filter(month__lt=month).order_by('month'))
    previous = {}
    for run in runs:
        for line in run.snapshot.get('lines', []):
            for row in line.get('reemployment_performance_sources', []):
                previous[row['plan_id']] = row
    results = defaultdict(list)
    blocked = set()
    for reentry in PayrollReemployment.objects.select_related('separation__final_run').order_by('pk'):
        original = reentry.separation.final_run
        if original.month >= month:
            continue
        _, period_end = month_bounds(original.month)
        if reentry.created_at >= period_end:
            continue
        plans = list(PayrollReemploymentPlan.objects.filter(reemployment=reentry,
            effective_at__lt=period_end, effective_at__lte=cutoff).order_by('effective_at', 'pk'))
        all_events = [e for e in monthly_payment_refund_events(original.month, cutoff=cutoff)
                      if e['occurred_at'] >= reentry.created_at]
        closeout = (PayrollSeparationSettlement.objects.filter(account_id=reentry.account_id,
            confirmed_at__gt=reentry.created_at, confirmed_at__lte=cutoff,
            final_run__month__gte=original.month).order_by('confirmed_at', 'pk').first())
        cycle_end = min(period_end, closeout.confirmed_at) if closeout else period_end
        if closeout and any(event['occurred_at'] == closeout.confirmed_at for event in all_events):
            blocked.add(reentry.account_id)
            continue
        events = [e for e in all_events if e['occurred_at'] < cycle_end]
        if any(plan.effective_at >= cycle_end for plan in plans):
            raise ValidationError('重入方案生效时间晚于本周期结清，请先核对')
        if not plans:
            # Without a plan its scope is unknown: store-scoped events may
            # matter even when no event is attributed to this employee.
            if events:
                blocked.add(reentry.account_id)
            continue
        for e in events:
            if e['occurred_at'] < plans[0].effective_at and (e['account_id'] == reentry.account_id
                    or any(p.plan_snapshot.get('target_scope') == 'STORE' and p.plan_snapshot.get('performance_fen') for p in plans)):
                blocked.add(reentry.account_id)
        for index, plan in enumerate(plans):
            settings = plan.plan_snapshot
            limit = min(plans[index + 1].effective_at, cycle_end) if index + 1 < len(plans) else cycle_end
            target = settings.get('target_fen')
            performance = settings.get('performance_fen')
            scope = settings.get('target_scope')
            if scope not in ('PERSON', 'STORE') or type(target) is not int or type(performance) is not int or (performance and target <= 0):
                raise ValidationError('重入方案绩效目标或范围待核对')
            applicable = [e for e in events if plan.effective_at <= e['occurred_at'] < limit
                          and (scope == 'STORE' or e['account_id'] == reentry.account_id)]
            sources = []
            for event in applicable:
                sources.append({'kind': event['kind'], 'origin': event['origin'], 'id': event['id'],
                    'order_id': event['order_id'], 'payment_id': event.get('payment_id'),
                    'account_id': event['account_id'],
                    'occurred_at': event['occurred_at'].isoformat(), 'amount_fen': event['amount_fen'],
                    'waived_separation_id': None, 'waived_reason': ''})
            # Preserve later refunds only when their original payment is a
            # proved source in this version. They do not extend this interval
            # or add the refund to the old version's performance basis.
            paid = {(e['origin'], e['order_id'] if e['origin'] == 'ORDER' else e['id'])
                    for e in applicable if e['kind'] == 'PAYMENT'}
            waived_refunds = []
            if closeout and closeout.confirmed_at < period_end:
                for event in all_events:
                    key = (event['origin'], event['order_id'] if event['origin'] == 'ORDER'
                           else event.get('payment_id'))
                    if (event['kind'] != 'REFUND' or event['occurred_at'] <= closeout.confirmed_at
                            or key not in paid):
                        continue
                    waived_refunds.append({'origin': event['origin'], 'id': event['id'],
                        'order_id': event['order_id'], 'payment_id': event.get('payment_id'),
                        'occurred_at': event['occurred_at'].isoformat(), 'amount_fen': event['amount_fen'],
                        'separation_id': closeout.pk, 'reason': '原周期工资已结清，原周期不追扣'})
            basis = sum(e['amount_fen'] for e in sources)
            amount = round_ratio(performance, max(0, min(target, basis)), target) if performance else 0
            old = previous.get(plan.pk)
            old_amount = old['performance_fen'] if old else 0
            if old and old.get('reemployment_id') != reentry.pk:
                raise ValidationError('重入绩效历史版本归属待核对')
            if (old and old.get('basis_fen') == basis and old.get('sources') == sources
                    and old.get('period_end') == limit.isoformat()
                    and old.get('post_closeout_refunds', []) == waived_refunds):
                continue
            results[reentry.account_id].append({'plan_id': plan.pk, 'reemployment_id': reentry.pk,
                'original_run_id': original.pk, 'period_start': plan.effective_at.isoformat(),
                'period_end': limit.isoformat(), 'plan_snapshot': settings, 'basis_fen': basis,
                'performance_fen': amount, 'previous_performance_fen': old_amount,
                'delta_fen': amount - old_amount, 'sources': sources,
                'post_closeout_refunds': waived_refunds})
    return results, blocked
