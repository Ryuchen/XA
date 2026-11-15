"""Late verification of a paid dispatch against its confirmed original plan."""
from datetime import time
from types import SimpleNamespace

from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from wallet.models import Transaction
from .payroll_calculation import is_night, month_bounds, raw_to_fen, refund_accounting_enabled, round_ratio
from .payroll_models import PayrollRun, SalaryActivity


def activity_payroll_month(activity):
    if activity.recognition_month:
        return activity.recognition_month
    moment = activity.performed_at
    if activity.kind == 'DISPATCH' and refund_accounting_enabled():
        moment = payment_for_dispatch(activity.order).created_at
    return timezone.localdate(moment).replace(day=1)


def payment_for_dispatch(order):
    payments = list(Transaction.objects.filter(order=order, tx_type='PAY', status='SUCCESS', amount__lte=0)
                    .select_related('wallet'))
    if (len(payments) != 1 or payments[0].amount != -order.amount
            or payments[0].wallet.account_id != order.customer_account_id
            or payments[0].balance_after != payments[0].balance_before + payments[0].amount
            or payments[0].reversal_of_id or payments[0].reversals.exists()):
        raise ValidationError('派单的实际付款流水待核对')
    return payments[0]


def _verified_refund_total(refunds, *, cutoff=None):
    """Use only returned store coins proved by the customer's wallet entry."""
    from .payroll_refund_performance import verify_refund_wallet_event
    total = 0
    for refund in refunds:
        if not refund.amount:
            continue
        tx = verify_refund_wallet_event(refund)
        if cutoff is not None and tx.created_at > cutoff:
            raise ValidationError('派单退款实际入账晚于工资核算截止时间，请先核对')
        total += refund.amount
    return total


def reemployment_night_config(reemployment_plan):
    from .payroll_models import PayrollConfig
    snapshot = reemployment_plan.night_config_snapshot
    current = PayrollConfig.objects.filter(pk=1).first()
    if not isinstance(snapshot, dict) or snapshot.get('revision') != (current.revision if current else 0):
        raise ValidationError('重入后的夜班配置已变化，业绩时点规则待核对')
    return snapshot


def initial_terms(run, line, order, performed, *, reemployment_plan_id=None, night_config=None):
    from .payroll_refund_performance import refund_event_queryset
    from .payroll_models import PayrollSeparationSettlement
    plan, config = line['plan_snapshot'], night_config if night_config is not None else run.snapshot['config']
    night = SimpleNamespace(night_start=time.fromisoformat(config['night_start']) if config['night_start'] else None,
                            night_end=time.fromisoformat(config['night_end']) if config['night_end'] else None)
    shift = is_night(performed, night)
    if shift is None and plan['dispatch_day_bps'] != plan['dispatch_night_bps']:
        raise ValidationError('原工资未设置夜班时段，无法补算派单提成')
    bucket = 'NIGHT' if shift else 'DAY'
    rate = plan['dispatch_night_bps' if shift else 'dispatch_day_bps']
    _, period_end = month_bounds(run.month)
    original_refunds = refund_event_queryset(order.refund_records.all()).filter(
        payroll_refund_time__lt=period_end, payroll_refund_time__lte=run.confirmed_at)
    basis = raw_to_fen(order.amount - _verified_refund_total(original_refunds, cutoff=run.confirmed_at))
    if basis < 0:
        raise ValidationError('原派单退款超过付款金额')
    payment_time = payment_for_dispatch(order).created_at
    after_closeout = PayrollSeparationSettlement.objects.filter(
        account_id=line['account'], final_run_id=run.pk,
        confirmed_at__lte=payment_time).exists()
    prior = []
    for item in SalaryActivity.objects.filter(account_id=line['account'], kind='DISPATCH', is_void=False,
                                               recognition_month__isnull=False).order_by('pk'):
        terms = item.source_snapshot.get('late_compensation', {})
        if terms.get('original_run_id') == run.pk and terms.get('reemployment_plan_id') == reemployment_plan_id:
            prior.append(terms)
    group_before = line['dispatch_night_amount_fen' if shift else 'dispatch_day_amount_fen'] + sum(
        item['source_basis_fen'] for item in prior if item['bucket'] == bucket)
    performance_before = line['performance_basis_fen'] + sum(item['source_basis_fen'] for item in prior)
    return {'source_amount_fen': raw_to_fen(order.amount), 'source_basis_fen': basis,
            'performance_source_basis_fen': 0 if after_closeout and not reemployment_plan_id else basis,
            'original_basis_fen': group_before, 'performance_before_basis_fen': performance_before,
            'rate_bps': rate, 'bucket': bucket, 'original_period_end': period_end.isoformat(),
            'original_confirmed_at': run.confirmed_at.isoformat()}


def current_terms(activity, *, cutoff):
    from django.utils.dateparse import parse_datetime
    from .payroll_refund_performance import refund_event_queryset
    terms = activity.source_snapshot['late_compensation']
    period_end = parse_datetime(terms['original_period_end'])
    original_confirmed = parse_datetime(terms['original_confirmed_at'])
    _, recognition_end = month_bounds(activity.recognition_month)
    subsequent = refund_event_queryset(activity.order.refund_records.all()).filter(
        payroll_refund_time__lte=cutoff, payroll_refund_time__lt=recognition_end).filter(
        Q(payroll_refund_time__gte=period_end) | Q(payroll_refund_time__gt=original_confirmed))
    all_refunds = raw_to_fen(_verified_refund_total(subsequent, cutoff=cutoff))
    if all_refunds > terms['source_basis_fen']:
        raise ValidationError('迟登记派单退款超过原工资补发基数，请先核对')
    basis = terms['source_basis_fen'] - all_refunds
    plan = terms['plan_snapshot']
    perf_basis = terms['performance_before_basis_fen']
    original = PayrollRun.objects.get(pk=terms['original_run_id'])
    line = next(item for item in original.snapshot['lines'] if item['account'] == activity.account_id)
    original_basis = (0 if terms.get('reemployment_id') else
        line['dispatch_night_amount_fen' if terms['bucket'] == 'NIGHT' else 'dispatch_day_amount_fen'])
    prior = list(SalaryActivity.objects.filter(account_id=activity.account_id, kind='DISPATCH',
        is_void=False, recognition_month__isnull=False, pk__lt=activity.pk).select_related('order').order_by('pk'))
    for item in prior:
        prior_terms = item.source_snapshot.get('late_compensation', {})
        if (prior_terms.get('original_run_id') != original.pk or prior_terms.get('bucket') != terms['bucket']
                or prior_terms.get('reemployment_plan_id') != terms.get('reemployment_plan_id')):
            continue
        prior_run = PayrollRun.objects.filter(month=item.recognition_month).first()
        saved = None
        if prior_run:
            prior_line = next((value for value in prior_run.snapshot['lines'] if value['account'] == item.account_id), {})
            saved = next((value for value in prior_line.get('late_adjustments', []) if value['activity_id'] == item.pk), None)
        if saved:
            original_basis += saved['commission_basis_fen']
        else:
            _, prior_end = month_bounds(item.recognition_month)
            refunded = _verified_refund_total(refund_event_queryset(item.order.refund_records.all()).filter(
                payroll_refund_time__lte=cutoff, payroll_refund_time__lt=prior_end).filter(
                Q(payroll_refund_time__gte=period_end) | Q(payroll_refund_time__gt=original_confirmed)), cutoff=cutoff)
            refunded_fen = raw_to_fen(refunded)
            if refunded_fen > prior_terms['source_basis_fen']:
                raise ValidationError('前期迟登记派单退款超过补发基数，请先核对')
            original_basis += prior_terms['source_basis_fen'] - refunded_fen
    commission = (round_ratio(original_basis + basis, terms['rate_bps'], 10000)
                  - round_ratio(original_basis, terms['rate_bps'], 10000))
    if not terms.get('reemployment_id') and plan['target_scope'] == 'PERSON' and plan['performance_fen']:
        # Positive late recognition uses the immutable confirmed baseline.
        # Later refunds belong to their own month, not the old payroll target.
        perf_basis = 0 if terms.get('reemployment_id') else line['performance_basis_fen']
        for item in prior:
            prior_terms = item.source_snapshot.get('late_compensation', {})
            if prior_terms.get('original_run_id') == original.pk and prior_terms.get('reemployment_plan_id') == terms.get('reemployment_plan_id'):
                perf_basis += prior_terms.get('performance_source_basis_fen', prior_terms['source_basis_fen'])
        # Earlier confirmed payroll periods can already have paid the
        # original month's late add-ons. Their frozen positive basis consumes
        # the same personal performance cap as this late dispatch.
        for paid_run in (PayrollRun.objects.none() if terms.get('reemployment_id') else
                         PayrollRun.objects.filter(month__lt=activity.recognition_month).order_by('month')):
            paid_line = next((row for row in paid_run.snapshot.get('lines', [])
                              if row.get('account') == activity.account_id), None)
            if paid_line:
                perf_basis += sum(source['original_period_net_fen']
                    for source in paid_line.get('late_addon_sources', [])
                    if source['original_run_id'] == original.pk and source.get('counts_performance', True))
        def performance(value):
            return round_ratio(plan['performance_fen'], max(0, min(plan['target_fen'], value)), plan['target_fen'])
        performance_amount = performance(perf_basis + terms.get('performance_source_basis_fen', terms['source_basis_fen'])) - performance(perf_basis)
    else:
        performance_amount = 0
    return {**terms, 'registration_original_basis_fen': terms.get('registration_original_basis_fen', terms['original_basis_fen']),
            'original_basis_fen': original_basis, 'effective_performance_before_basis_fen': perf_basis,
            'commission_basis_fen': basis, 'commission_fen': commission,
            'performance_fen': performance_amount, 'gross_fen': commission + performance_amount,
            'amount_fen': commission + performance_amount}
