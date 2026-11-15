"""Recognize store performance for sales made after that month's payroll was confirmed."""
from collections import defaultdict
from datetime import timedelta

from django.utils import timezone

from .payroll_calculation import round_ratio
from .payroll_models import PayrollRun, PayrollSeparationSettlement
from .payroll_refund_performance import monthly_payment_refund_events


def _next_month(month):
    return (month.replace(day=28) + timedelta(days=4)).replace(day=1)


def _performance(basis, plan):
    if not plan['performance_fen']:
        return 0
    return round_ratio(plan['performance_fen'], max(0, min(plan['target_fen'], basis)), plan['target_fen'])


def derive_late_store_orders(month, cutoff, late_addons):
    """Return unpaid store-performance changes in the first unconfirmed period."""
    runs = {run.month: run for run in PayrollRun.objects.filter(month__lte=month).order_by('month')}
    recognized_orders, prior_addons = {}, []
    for run in runs.values():
        for line in run.snapshot.get('lines', []):
            for source in line.get('late_store_order_sources', []):
                recognized_orders[(source['payment_id'], source['account_id'])] = source
            prior_addons.extend(line.get('late_addon_sources', []))
    for rows in late_addons.values():
        prior_addons.extend(rows)
    results, blocked = defaultdict(list), set()
    for original_month, original in runs.items():
        if original_month >= month:
            continue
        target = _next_month(original_month)
        while target in runs:
            target = _next_month(target)
        events = monthly_payment_refund_events(original_month, cutoff=cutoff)
        new_payments = [event for event in events if event['origin'] == 'ORDER' and event['kind'] == 'PAYMENT'
                        and event['occurred_at'] > original.confirmed_at]
        if not new_payments:
            continue
        for line in original.snapshot.get('lines', []):
            plan, account_id = line['plan_snapshot'], line['account']
            if plan['target_scope'] != 'STORE' or not plan['performance_fen']:
                continue
            pending = [event for event in new_payments if (event['id'], account_id) not in recognized_orders]
            if not pending:
                continue
            # The confirmed run is also the employee's final run when there
            # is a separation settlement. Sales after that settlement do not
            # belong to the old employment period, even if reentry has not
            # yet been registered. Reentry sales are calculated separately.
            closeout = PayrollSeparationSettlement.objects.filter(
                account_id=account_id, final_run_id=original.pk,
                confirmed_at__lte=cutoff).order_by('confirmed_at', 'pk').first()
            if closeout:
                pending = [event for event in pending if event['occurred_at'] < closeout.confirmed_at]
            if not pending:
                continue
            from .payroll_models import PayrollReemployment
            reentries = PayrollReemployment.objects.filter(account_id=account_id,
                separation__final_run__month=original_month).values_list('created_at', flat=True)
            reentry_times = list(reentries)
            pending = [event for event in pending
                       if not any(event['occurred_at'] >= started for started in reentry_times)]
            if not pending:
                continue
            if target < month:
                blocked.add(account_id)
                continue
            if target != month:
                continue
            basis = line['performance_basis_fen']
            for source in prior_addons:
                if (source['original_run_id'] == original.pk and source['account_id'] == account_id
                        and source.get('counts_performance', source['paid_after_confirmation'])):
                    basis += source['original_period_net_fen']
            for source in recognized_orders.values():
                if source['original_run_id'] == original.pk and source['account_id'] == account_id:
                    basis += source['original_period_net_fen']
            for event in pending:
                net = event['amount_fen']
                amount = _performance(basis + net, plan) - _performance(basis, plan)
                results[account_id].append({'payment_id': event['id'], 'order_id': event['order_id'],
                    'account_id': account_id, 'original_run_id': original.pk,
                    'original_period_net_fen': net, 'performance_fen': amount, 'amount_fen': amount})
                basis += net
    return results, blocked
