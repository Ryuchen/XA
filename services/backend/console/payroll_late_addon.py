"""Derive unpaid add-on sales that were verified after their payroll period."""
from collections import defaultdict
from datetime import time, timedelta
from types import SimpleNamespace

from django.utils import timezone
from rest_framework.exceptions import ValidationError

from orders.models import OrderAddonPayment
from .payroll_calculation import is_night, month_bounds, raw_to_fen, round_ratio
from .payroll_models import PayrollRun, PayrollSeparationSettlement, SalaryActivity


def _next_month(month):
    return (month.replace(day=28) + timedelta(days=4)).replace(day=1)


def _performance(basis, plan):
    if not plan['performance_fen']:
        return 0
    return round_ratio(plan['performance_fen'], max(0, min(plan['target_fen'], basis)), plan['target_fen'])


def derive_late_addons(month, cutoff):
    """Return each unrecognized add-on once, at its first open payroll period."""
    _, end = month_bounds(month)
    payments = list(OrderAddonPayment.objects.filter(paid_at__lt=end, paid_at__lte=cutoff,
        intent__order__direct_payment__isnull=True, intent__order__is_gift_snapshot=False)
        .select_related('intent', 'buyer_transaction__wallet').order_by('paid_at', 'pk'))
    from .payroll_refund_performance import addon_payment_time, verify_addon_refund_wallet_event, refund_event_queryset
    payment_times = {item.pk: addon_payment_time(item) for item in payments}
    if any(item.buyer_transaction.created_at > cutoff for item in payments):
        raise ValidationError('加购实付流水晚于工资核算截止时间，请先核对')
    owners = {item.order_id: item for item in SalaryActivity.objects.filter(
        kind='DISPATCH', is_void=False, created_at__lte=cutoff,
        order_id__in=[item.intent.order_id for item in payments])}
    runs = {run.month: run for run in PayrollRun.objects.filter(month__lte=month).order_by('month')}
    recognized = {}
    for run in runs.values():
        for line in run.snapshot.get('lines', []):
            for item in line.get('late_addon_sources', []):
                recognized[(item['payment_id'], item['account_id'])] = item
    groups, blocked = defaultdict(list), set()
    for payment in payments:
        activity = owners.get(payment.intent.order_id)
        source_month = timezone.localdate(payment_times[payment.pk]).replace(day=1)
        original = runs.get(source_month)
        if not original or source_month >= month:
            continue
        source_lines = original.snapshot.get('lines', [])
        owner_line = next((row for row in source_lines if activity and row['account'] == activity.account_id), None)
        if activity and not owner_line:
            blocked.add(activity.account_id)
        for line in source_lines:
            account_id = line['account']
            owner = bool(activity and account_id == activity.account_id)
            after_closeout = PayrollSeparationSettlement.objects.filter(
                account_id=account_id, final_run_id=original.pk,
                confirmed_at__lte=payment_times[payment.pk]).exists()
            store_performance = (line['plan_snapshot']['target_scope'] == 'STORE'
                                 and line['plan_snapshot']['performance_fen']
                                 and payment_times[payment.pk] > original.confirmed_at
                                 and not after_closeout)
            if (not owner and not store_performance) or (payment.pk, account_id) in recognized:
                continue
            if owner and any(row['payment_id'] == payment.pk for row in line.get('addon_sources', [])):
                continue
            from .payroll_models import PayrollReemployment, PayrollReemploymentPlan
            reemployment = PayrollReemployment.objects.filter(account_id=account_id,
                    separation__final_run__month=source_month,
                    created_at__lte=payment_times[payment.pk]).order_by('-created_at', '-pk').first()
            reemployment_plan = None
            if reemployment:
                reemployment_plan = PayrollReemploymentPlan.objects.filter(reemployment=reemployment,
                    effective_at__lte=payment_times[payment.pk]).order_by('-effective_at', '-pk').first()
                if not reemployment_plan:
                    blocked.add(account_id)
                    continue
                if not owner:
                    continue  # Store performance is handled per actual version interval.
            # A dispatch may be verified after an earlier payroll period that
            # is still open. Do not put the add-on into that period before
            # its owner was actually known; use the first open period from
            # the later of verification and the original month's successor.
            known_at = payment.buyer_transaction.created_at
            if owner:
                known_at = max(known_at, activity.created_at)
            target = max(_next_month(source_month), timezone.localdate(known_at).replace(day=1))
            while target in runs:
                target = _next_month(target)
            if target < month:
                blocked.add(account_id)
            elif target == month:
                groups[(original.pk, account_id, reemployment_plan.pk if reemployment_plan else None)].append(
                    (payment, activity if owner else None, line, original, reemployment_plan, after_closeout))

    result = defaultdict(list)
    for (_, account_id, reemployment_plan_id), candidates in groups.items():
        _, _, line, original, reemployment_plan, _ = candidates[0]
        plan = reemployment_plan.plan_snapshot if reemployment_plan else line['plan_snapshot']
        if reemployment_plan:
            from .payroll_late_dispatch import reemployment_night_config
            try:
                config = reemployment_night_config(reemployment_plan)
            except ValidationError:
                blocked.add(account_id)
                continue
        else:
            config = original.snapshot['config']
        night_rules = SimpleNamespace(night_start=time.fromisoformat(config['night_start']) if config['night_start'] else None,
                                      night_end=time.fromisoformat(config['night_end']) if config['night_end'] else None)
        _, original_end = month_bounds(original.month)
        # Only the confirmed snapshot and previously recognized positive
        # sources form the original-period performance baseline.
        perf_basis = 0 if reemployment_plan else line['performance_basis_fen']
        baseline = ({'DAY': 0, 'NIGHT': 0} if reemployment_plan else
                    {'DAY': line['dispatch_day_amount_fen'], 'NIGHT': line['dispatch_night_amount_fen']})
        # Late dispatches are accounted before add-ons against the same old
        # payroll scheme, including when both are paid in this period.
        from .payroll_late_dispatch import current_terms
        for activity in SalaryActivity.objects.filter(account_id=account_id, kind='DISPATCH', is_void=False,
                recognition_month__isnull=False, recognition_month__lte=month).select_related('order').order_by('pk'):
            terms = activity.source_snapshot.get('late_compensation', {})
            if (terms.get('original_run_id') != original.pk
                    or terms.get('reemployment_plan_id') != reemployment_plan_id):
                continue
            known = runs.get(activity.recognition_month)
            saved = None
            if known:
                saved_line = next((row for row in known.snapshot.get('lines', []) if row['account'] == account_id), {})
                saved = next((row for row in saved_line.get('late_adjustments', []) if row['activity_id'] == activity.pk), None)
            current = saved or current_terms(activity, cutoff=cutoff)
            baseline[current['bucket']] += current['commission_basis_fen']
            if plan['target_scope'] == 'PERSON':
                perf_basis += terms.get('performance_source_basis_fen', terms['source_basis_fen'])
        # Previously paid late additions remain part of the rounding and
        # original-month performance baseline; their later refunds are separate.
        for source in recognized.values():
            if (source['original_run_id'] == original.pk and source['account_id'] == account_id
                    and source.get('reemployment_plan_id') == reemployment_plan_id):
                if source['bucket'] in baseline:
                    baseline[source['bucket']] += source['commission_basis_fen']
                if source.get('counts_performance',
                              plan['target_scope'] == 'PERSON' or source['paid_after_confirmation']):
                    perf_basis += source['original_period_net_fen']
        if plan['target_scope'] == 'STORE':
            for paid_run in runs.values():
                for paid_line in paid_run.snapshot.get('lines', []):
                    if paid_line.get('account') == account_id:
                        perf_basis += sum(source['original_period_net_fen']
                            for source in paid_line.get('late_store_order_sources', [])
                            if source['original_run_id'] == original.pk)
        for payment, activity, _, _, _, after_closeout in candidates:
            shift = is_night(activity.performed_at, night_rules) if activity else None
            if activity and shift is None and plan['dispatch_day_bps'] != plan['dispatch_night_bps']:
                blocked.add(account_id)
                continue
            bucket = ('NIGHT' if shift else 'DAY') if activity else 'NONE'
            rate = plan['dispatch_night_bps' if shift else 'dispatch_day_bps'] if activity else 0
            refunds = list(refund_event_queryset(payment.refund_records.all(), addon=True).filter(
                payroll_refund_time__lt=end, payroll_refund_time__lte=cutoff))
            for refund in refunds:
                tx = verify_addon_refund_wallet_event(refund)
                if tx.created_at > cutoff:
                    raise ValidationError('加购退款实际入账晚于工资核算截止时间，请先核对')
            returned = sum(refund.amount for refund in refunds)
            if returned > payment.intent.amount:
                raise ValidationError('加购退款超过原付款金额，请先核对')
            commission_basis = raw_to_fen(payment.intent.amount - returned) if activity else 0
            original_net = raw_to_fen(payment.intent.amount - sum(refund.amount for refund in refunds
                if refund.payroll_refund_time < original_end and refund.payroll_refund_time <= original.confirmed_at))
            original_basis = baseline[bucket] if activity else 0
            commission = round_ratio(original_basis + commission_basis, rate, 10000) - round_ratio(original_basis, rate, 10000)
            counts_performance = ((not after_closeout or reemployment_plan is not None) and
                                  (plan['target_scope'] == 'PERSON' or
                                   payment_times[payment.pk] > original.confirmed_at))
            performance = (_performance(perf_basis + original_net, plan) - _performance(perf_basis, plan)
                           if counts_performance and not reemployment_plan else 0)
            result[account_id].append({'payment_id': payment.pk, 'activity_id': activity.pk if activity else None,
                'account_id': account_id, 'original_run_id': original.pk, 'bucket': bucket, 'rate_bps': rate,
                'reemployment_plan_id': reemployment_plan_id,
                'original_basis_fen': original_basis, 'commission_basis_fen': commission_basis,
                'original_period_net_fen': original_net, 'paid_after_confirmation': payment_times[payment.pk] > original.confirmed_at,
                'counts_performance': counts_performance,
                'commission_fen': commission, 'performance_fen': performance,
                'amount_fen': commission + performance})
            if activity:
                baseline[bucket] += commission_basis
            if counts_performance:
                perf_basis += original_net
    return result, blocked
