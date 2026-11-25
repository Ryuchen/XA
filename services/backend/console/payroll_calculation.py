"""Explicit payroll snapshots and matching operating cost/revenue cohorts."""
import calendar
import hashlib
import json
import os
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone as datetime_timezone

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models import DateTimeField, Exists, F, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import serializers

from orders.models import AfterSale, Order, OrderPlatformSubsidyShare, OrderStatusLog
from orders.refunds import PAID_STATES, with_net_amount
from wallet.models import RechargeRecord, Transaction
from .payroll_models import OperatingCost, PayrollConfig, PayrollRun, SalaryActivity, SalaryAssignment

PLAN_FIELDS = ('name', 'base_fen', 'performance_fen', 'target_fen', 'target_scope',
               'dispatch_day_bps', 'dispatch_night_bps', 'recharge_bps', 'aftersale_fee_fen',
               'exam_fee_fen', 'exam_commission_bps', 'remark', 'revision')
TOTAL_FIELDS = ('base_fen', 'performance_fen', 'dispatch_commission_fen', 'recharge_commission_fen',
                'aftersale_commission_fen', 'exam_gross_fen', 'exam_deduction_fen', 'exam_commission_fen',
                'late_adjustments_fen', 'total_fen')


def refund_accounting_enabled():
    """Opt-in only: the payment/refund event barrier is not yet connected.

    Do not enable in production until all payment/refund writers and payroll
    confirmation share a per-store serialization barrier and are race-tested.
    The old override exists only for isolated regression tests.
    """
    configured = getattr(settings, 'PAYROLL_REFUND_ACCOUNTING_ENABLED', None)
    if configured is None:
        configured = os.environ.get('PAYROLL_REFUND_ACCOUNTING_ENABLED')
    if configured is None:
        return bool(getattr(settings, 'PAYROLL_REFUND_ACCOUNTING_INTERNAL_TESTING', False))
    if isinstance(configured, bool):
        return configured
    if isinstance(configured, str) and configured.lower() in ('1', 'true', 'yes', 'on', '0', 'false', 'no', 'off'):
        return configured.lower() in ('1', 'true', 'yes', 'on')
    raise ImproperlyConfigured('PAYROLL_REFUND_ACCOUNTING_ENABLED must be a boolean')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                                     default=str).encode()).hexdigest()


def parse_month(value):
    try:
        if not isinstance(value, str) or len(value) != 7:
            raise ValueError
        result = datetime.strptime(value, '%Y-%m').date().replace(day=1)
        if result.strftime('%Y-%m') != value:
            raise ValueError
        return result
    except (TypeError, ValueError):
        raise serializers.ValidationError({'month': '月份须为YYYY-MM'}) from None


def month_bounds(month):
    last = calendar.monthrange(month.year, month.month)[1]
    end = month + timedelta(days=last)
    zone = timezone.get_current_timezone()
    return (timezone.make_aware(datetime.combine(month, time.min), zone),
            timezone.make_aware(datetime.combine(end, time.min), zone))


def round_ratio(amount, numerator, denominator):
    return (int(amount) * int(numerator) + int(denominator) // 2) // int(denominator)


def raw_to_fen(amount):
    # Business currency: 10 raw units / wave, 10 waves / CNY. Keep this named
    # conversion separate from RMB payroll inputs even though the ratio is 1.
    return int(amount) * 100 // (10 * 10)


def recharge_snapshot_matches(activity):
    """Whether the attribution still matches its immutable recharge evidence."""
    recharge = activity.recharge
    if not recharge or not recharge.recharge_tx_id:
        return False
    snapshot = activity.source_snapshot
    return (
        snapshot.get('recharge_tx_id') == recharge.recharge_tx_id
        and snapshot.get('source_amount_raw') == recharge.amount
        and snapshot.get('buyer_account_id') == recharge.account_id
        and snapshot.get('trade_no') == recharge.trade_no
        and activity.performed_at == recharge.recharge_tx.created_at
    )


def recharge_source_amount_fen(activity):
    """Return None unless both the source record and attribution are proved."""
    if activity.kind != 'RECHARGE' or not recharge_snapshot_matches(activity):
        return None
    if not eligible_recharges().filter(pk=activity.recharge_id).exists():
        return None
    return raw_to_fen(activity.recharge.amount)


def sum_known(values):
    """Sum financial values without turning an unknown component into zero."""
    values = list(values)
    return None if any(value is None for value in values) else sum(values)


def add_known(current, value):
    return None if current is None or value is None else current + value


def plan_snapshot(plan):
    return {**{key: getattr(plan, key) for key in PLAN_FIELDS}, 'id': plan.pk,
            'position': plan.position_id, 'position_code': plan.position.code,
            'position_name': plan.position.name}


def name_of(account):
    return account.nickname or account.real_name or account.username


def is_night(moment, config):
    if config is None or config.night_start is None or config.night_end is None:
        return None
    current = timezone.localtime(moment).time().replace(tzinfo=None)
    start, end = config.night_start, config.night_end
    return start <= current < end if start < end else current >= start or current < end


def aftersale_cycles(row):
    def canonical(value):
        if value is None:
            return None
        parsed = serializers.DateTimeField().run_validation(value)
        return parsed.astimezone(datetime_timezone.utc).isoformat()

    result, seen = [], set()
    for item in [*row.history, {'status': row.status, 'resolved_at': row.resolved_at.isoformat() if row.resolved_at else None,
                               'created_at': row.created_at.isoformat(), 'operator_id': row.operator_id,
                               'client_request_id': row.client_request_id}]:
        if item.get('status') not in ('RESOLVED', 'REFUNDED', 'PARTIALLY_REFUNDED') or not item.get('resolved_at'):
            continue
        created_at, resolved_at = canonical(item.get('created_at')), canonical(item['resolved_at'])
        key = fingerprint([row.pk, created_at, resolved_at, item.get('client_request_id')])[:40]
        if key not in seen:
            seen.add(key)
            result.append({'cycle_key': key, 'performed_at': resolved_at, 'status': item['status'],
                           'operator_id': item.get('operator_id')})
    return result


def eligible_recharges():
    """Only proved boss pre-deposits, never provider funding or adjustments."""
    duplicates = RechargeRecord.objects.filter(recharge_tx_id=OuterRef('recharge_tx_id')).exclude(pk=OuterRef('pk'))
    return RechargeRecord.objects.annotate(duplicate_recharge_tx=Exists(duplicates)).filter(
        duplicate_recharge_tx=False, amount__gt=0, recharge_tx__status='SUCCESS', recharge_tx__tx_type='TOPUP',
        recharge_tx__amount=F('amount'), recharge_tx__order__isnull=True, recharge_tx__reversal_of__isnull=True,
        recharge_tx__reversals__isnull=True,
        recharge_tx__balance_after=F('recharge_tx__balance_before') + F('recharge_tx__amount')).filter(
        Q(account__account_type='BOSS') | Q(account__isnull=True, user__role='CUSTOMER')).filter(
        Q(account__isnull=False, recharge_tx__wallet__account_id=F('account_id'))
        | Q(recharge_tx__wallet__account__isnull=True, user__isnull=False,
            recharge_tx__wallet__user_id=F('user_id')))


def unassigned_sources(assignments, activities, start, end):
    """A missing sales owner is unknown commission, not a zero commission."""
    plans = [row.plan_snapshot for row in assignments]
    source_keys = {row.source_key for row in activities}
    result = {'dispatch': [], 'recharge': [], 'aftersale': []}
    if any(plan['dispatch_day_bps'] or plan['dispatch_night_bps']
           or (plan['target_scope'] == 'PERSON' and plan['performance_fen']) for plan in plans):
        logs = OrderStatusLog.objects.filter(order_id=OuterRef('pk'), operator_account__account_type='STAFF')
        first_create = logs.filter(action='CREATE').order_by('created_at', 'pk').values('created_at')[:1]
        first_assign = logs.filter(action='ASSIGN').order_by('created_at', 'pk').values('created_at')[:1]
        if refund_accounting_enabled():
            # Dispatch compensation belongs to the payment month even when
            # the staff created or assigned the order in an earlier month.
            from .payroll_refund_performance import monthly_payment_refund_events
            events = monthly_payment_refund_events(timezone.localdate(start).replace(day=1))
            paid_ids = {event['order_id'] for event in events if event['kind'] == 'PAYMENT'}
            net_by_order = defaultdict(int)
            for event in events:
                if event['order_id'] in paid_ids:
                    net_by_order[event['order_id']] += event['amount_fen']
            # An add-on can be paid in a later month; its dispatch attribution
            # is still the verified activity on the original service order.
            source_keys.update(f'ORDER:{order_id}' for order_id in SalaryActivity.objects.filter(
                kind='DISPATCH', is_void=False, order_id__in=paid_ids).values_list('order_id', flat=True))
            orders = Order.objects.filter(pk__in=[pk for pk, net in net_by_order.items() if net > 0]).annotate(
                payroll_event_at=Coalesce(Subquery(first_create, output_field=DateTimeField()),
                                         Subquery(first_assign, output_field=DateTimeField()))).filter(
                payroll_event_at__isnull=False)
        else:
            orders = with_net_amount(Order.objects.filter(payment_status__in=PAID_STATES,
                direct_payment__isnull=True, is_gift_snapshot=False)).annotate(
                payroll_event_at=Coalesce(Subquery(first_create, output_field=DateTimeField()),
                                         Subquery(first_assign, output_field=DateTimeField()))).filter(
                payroll_event_at__gte=start, payroll_event_at__lt=end, _net_paid_amount__gt=0)
        for order in orders:
            if f'ORDER:{order.pk}' not in source_keys:
                result['dispatch'].append({'id': order.pk, 'order_no': order.order_no})
    if any(plan['recharge_bps'] for plan in plans):
        for row in eligible_recharges().filter(recharge_tx__created_at__gte=start, recharge_tx__created_at__lt=end):
            if f'RECHARGE:{row.pk}' not in source_keys:
                result['recharge'].append({'id': row.pk})
    if any(plan['aftersale_fee_fen'] for plan in plans):
        for row in AfterSale.objects.filter(order__direct_payment__isnull=True):
            for cycle in aftersale_cycles(row):
                moment = serializers.DateTimeField().run_validation(cycle['performed_at'])
                key = f'AFTERSALE:{row.pk}:{cycle["cycle_key"]}'
                if start <= moment < end and key not in source_keys:
                    result['aftersale'].append({'id': row.pk, 'cycle_key': cycle['cycle_key']})
    return result


def calculate_payroll(month):
    month_text = month.strftime('%Y-%m')
    confirmed = PayrollRun.objects.filter(month=month).first()
    if confirmed:
        return {**confirmed.snapshot, 'status': 'CONFIRMED', 'can_confirm': False,
                'run_id': confirmed.pk, 'confirmed_at': confirmed.confirmed_at.isoformat()}
    start, end = month_bounds(month)
    config = PayrollConfig.objects.filter(pk=1).first()
    activities = list(SalaryActivity.objects.filter(is_void=False).filter(
        Q(recognition_month=month) | Q(recognition_month__isnull=True, performed_at__gte=start, performed_at__lt=end))
                      .select_related('account', 'order', 'recharge', 'aftersale').order_by('id'))
    assignments = list(SalaryAssignment.objects.filter(month=month).select_related('account', 'plan'))
    store_by_day = defaultdict(int)
    refund_accounting = bool(refund_accounting_enabled())
    personal_events_by_day = defaultdict(lambda: defaultdict(int))
    addon_sources_by_account = defaultdict(list)
    if refund_accounting:
        from .payroll_refund_performance import monthly_performance_bases
        monthly = monthly_performance_bases(month)
        paid_orders = {event['order_id'] for event in monthly['events']
                       if event['kind'] == 'PAYMENT' and event['origin'] == 'ORDER'}
        activities = [row for row in activities if row.kind != 'DISPATCH' or row.recognition_month] + list(
            SalaryActivity.objects.filter(is_void=False, kind='DISPATCH', order_id__in=paid_orders)
            .filter(recognition_month__isnull=True).select_related('account', 'order', 'recharge', 'aftersale').order_by('id'))
        amounts = defaultdict(int)
        addon_paid = {event['id']: dict(event) for event in monthly['events']
                      if event['kind'] == 'PAYMENT' and event['origin'] == 'ADDON'}
        for event in monthly['events']:
            day = timezone.localdate(event['occurred_at']).isoformat()
            store_by_day[day] += event['amount_fen']
            if event['account_id'] is not None:
                personal_events_by_day[event['account_id']][day] += event['amount_fen']
            if event['origin'] == 'ORDER' and event['order_id'] in paid_orders:
                amounts[event['order_id']] += event['amount_fen']
            if (event['origin'] == 'ADDON' and event['kind'] == 'REFUND'
                    and event['payment_id'] in addon_paid):
                addon_paid[event['payment_id']]['amount_fen'] += event['amount_fen']
        addon_activities = {row.order_id: row for row in SalaryActivity.objects.filter(
            is_void=False, kind='DISPATCH', order_id__in=[event['order_id'] for event in addon_paid.values()])}
        for event in addon_paid.values():
            activity = addon_activities.get(event['order_id'])
            if activity:
                addon_sources_by_account[activity.account_id].append((activity, event))
    else:
        order_ids = [row.order_id for row in activities if row.kind == 'DISPATCH']
        amounts = {row.pk: raw_to_fen(row._net_paid_amount) for row in with_net_amount(
            Order.objects.filter(pk__in=order_ids, payment_status__in=PAID_STATES, direct_payment__isnull=True))}
        store_orders = list(with_net_amount(Order.objects.filter(
            created_at__gte=start, created_at__lt=end, payment_status__in=PAID_STATES,
            direct_payment__isnull=True, is_gift_snapshot=False)))
        for order in store_orders:
            store_by_day[timezone.localdate(order.created_at).isoformat()] += raw_to_fen(order._net_paid_amount)
    store_amount = sum(store_by_day.values())
    by_person = defaultdict(list)
    for row in activities:
        by_person[row.account_id].append(row)
    lines, warnings, daily_costs = [], [], defaultdict(int)
    if not refund_accounting or not getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
        warnings.append('退款工资核算或工资事件屏障尚未启用，该预览不能用于工资确认')
    from .payroll_reemployment_activity import verify_late_reemployment_activity
    for activity in activities:
        try:
            verify_late_reemployment_activity(activity)
        except serializers.ValidationError as exc:
            warnings.append(f'重入业绩记录 {activity.pk} 的来源待核对：{exc.detail}')
    if any(row.plan_snapshot['recharge_bps'] for row in assignments):
        invalid_recharges = RechargeRecord.objects.filter(
            Q(recharge_tx__created_at__gte=start, recharge_tx__created_at__lt=end)
            | Q(recharge_tx__isnull=True, created_at__gte=start, created_at__lt=end)).filter(
            Q(account__account_type='BOSS') | Q(account__isnull=True, user__role='CUSTOMER')
        ).exclude(pk__in=eligible_recharges().values('pk'))
        invalid_ids = list(invalid_recharges.order_by('pk').values_list('pk', flat=True)[:30])
        if invalid_ids:
            warnings.append(f'充值记录 {", ".join(map(str, invalid_ids))} 的原始凭证待核对，不能将未知充值业绩当作0')
    recharge_ids = {row.recharge_id for row in activities if row.kind == 'RECHARGE'}
    eligible_ids = set(eligible_recharges().filter(pk__in=recharge_ids).values_list('pk', flat=True))
    invalid_recharge_activity_ids = set()
    invalid_recharge_recognition_ids = set()
    for activity in activities:
        if activity.kind != 'RECHARGE':
            continue
        if activity.recharge_id not in eligible_ids or not recharge_snapshot_matches(activity):
            invalid_recharge_activity_ids.add(activity.pk)
            if activity.recognition_month:
                invalid_recharge_recognition_ids.add(activity.pk)
                unknown_day = max(month, timezone.localdate(activity.created_at))
            else:
                unknown_day = timezone.localdate(activity.performed_at)
            daily_costs[unknown_day.isoformat()] = None
            warnings.append(f'充值业绩记录 {activity.pk} 的原始凭证不一致，请核对后再确认工资')
    assigned = {row.account_id for row in assignments}
    late_addons, blocked_addons, late_store_orders, reemployment_performance = {}, set(), {}, {}
    if refund_accounting:
        from .payroll_late_addon import derive_late_addons
        late_addons, blocked_addons = derive_late_addons(month, timezone.now())
        from .payroll_late_store import derive_late_store_orders
        late_store_orders, blocked_store = derive_late_store_orders(month, timezone.now(), late_addons)
        blocked_addons |= blocked_store
        from .payroll_reemployment_performance import derive_reemployment_performance
        reemployment_performance, blocked_reemployment = derive_reemployment_performance(month, timezone.now())
        blocked_addons |= blocked_reemployment
        for account_id in blocked_addons:
            warnings.append(f'员工 {account_id} 有待核对的跨期工资，请先核实原工资、入职周期及前期结算')
    unattributed = unassigned_sources(assignments, activities, start, end)
    for key, label in (('dispatch', '派单'), ('recharge', '充值'), ('aftersale', '已完成售后周期')):
        if unattributed[key]:
            warnings.append(f'该月有{len(unattributed[key])}笔{label}尚未核实薪资归属，未将应计提成当作0')
    for account_id in sorted((set(by_person) | set(addon_sources_by_account) | set(late_addons)
                              | set(late_store_orders) | set(reemployment_performance)) - assigned):
        from club_accounts.models import ClubAccount
        account = ClubAccount.objects.get(pk=account_id)
        warnings.append(f'{name_of(account)}有业绩记录，但该月未安排薪资方案')
    for assignment in assignments:
        plan = assignment.plan_snapshot
        line = {field: 0 for field in TOTAL_FIELDS}
        line.update(account=assignment.account_id, account_name=name_of(assignment.account),
                    assignment_id=assignment.pk, plan_name=plan['name'], position_name=plan['position_name'],
                    dispatch_day_amount_fen=0, dispatch_night_amount_fen=0, dispatch_unclassified_amount_fen=0,
                    recharge_amount_fen=0, aftersale_count=0, exam_count=0, warnings=[],
                    evidence_complete=True, unknown_salary_activity_ids=[],
                    plan_snapshot=plan, late_adjustments=[], addon_sources=[], late_addon_sources=[],
                    late_store_order_sources=[], reemployment_performance_sources=[])
        personal_by_day = defaultdict(int)
        if refund_accounting:
            personal_by_day.update(personal_events_by_day[assignment.account_id])
        last_earned = 0
        for activity in sorted(by_person[assignment.account_id], key=lambda row: (row.performed_at, row.pk)):
            invalid_recharge = activity.pk in invalid_recharge_activity_ids
            if invalid_recharge:
                line['evidence_complete'] = False
                line['unknown_salary_activity_ids'].append(activity.pk)
                line['recharge_amount_fen'] = None
                line['warnings'].append(f'充值业绩记录 {activity.pk} 的原始凭证待核对，相关金额未知')
            if activity.recognition_month:
                if invalid_recharge:
                    line['late_adjustments_fen'] = None
                    recognized_day = max(month, timezone.localdate(activity.created_at)).isoformat()
                    daily_costs[recognized_day] = None
                    continue
                terms = activity.source_snapshot['late_compensation']
                if refund_accounting and activity.kind == 'DISPATCH':
                    from .payroll_late_dispatch import current_terms
                    terms = current_terms(activity, cutoff=timezone.now())
                amount = terms['amount_fen']
                line['late_adjustments_fen'] = add_known(line['late_adjustments_fen'], amount)
                line['late_adjustments'].append({'activity_id': activity.pk, 'kind': activity.kind,
                    'source_key': activity.source_key, **terms})
                recognized_day = max(month, timezone.localdate(activity.created_at)).isoformat()
                daily_costs[recognized_day] = add_known(daily_costs[recognized_day], amount)
                continue
            day = timezone.localdate(activity.performed_at).isoformat()
            if activity.kind == 'DISPATCH':
                amount = amounts.get(activity.order_id, 0)
                if not refund_accounting:
                    personal_by_day[day] += amount
                night = is_night(activity.performed_at, config)
                if night is None and plan['dispatch_day_bps'] != plan['dispatch_night_bps'] and amount:
                    line['dispatch_unclassified_amount_fen'] += amount
                elif night:
                    line['dispatch_night_amount_fen'] += amount
                else:
                    line['dispatch_day_amount_fen'] += amount
            elif activity.kind == 'RECHARGE' and not invalid_recharge:
                line['recharge_amount_fen'] = add_known(
                    line['recharge_amount_fen'], raw_to_fen(activity.recharge.amount))
            elif activity.kind == 'AFTERSALE':
                line['aftersale_count'] += 1
            elif activity.kind == 'EXAM':
                line['exam_count'] += 1
            recharge_commission = (None if line['recharge_amount_fen'] is None else
                                   round_ratio(line['recharge_amount_fen'], plan['recharge_bps'], 10000))
            earned = sum_known((
                round_ratio(line['dispatch_day_amount_fen'], plan['dispatch_day_bps'], 10000),
                round_ratio(line['dispatch_night_amount_fen'], plan['dispatch_night_bps'], 10000),
                recharge_commission,
                line['aftersale_count'] * plan['aftersale_fee_fen'],
                line['exam_count'] * (plan['exam_fee_fen'] - round_ratio(
                    plan['exam_fee_fen'], plan.get('exam_commission_bps', 0), 10000)),
            ))
            if refund_accounting and earned is not None:
                # Dispatch is attributed below to actual payment/refund dates.
                earned -= (round_ratio(line['dispatch_day_amount_fen'], plan['dispatch_day_bps'], 10000)
                           + round_ratio(line['dispatch_night_amount_fen'], plan['dispatch_night_bps'], 10000))
            if earned is None or last_earned is None:
                daily_costs[day] = None
            else:
                daily_costs[day] = add_known(daily_costs[day], earned - last_earned)
            last_earned = earned
        for activity, event in sorted(addon_sources_by_account[assignment.account_id],
                                      key=lambda item: (item[1]['occurred_at'], item[1]['id'])):
            amount = event['amount_fen']
            night = is_night(activity.performed_at, config)
            before = (round_ratio(line['dispatch_day_amount_fen'], plan['dispatch_day_bps'], 10000)
                      + round_ratio(line['dispatch_night_amount_fen'], plan['dispatch_night_bps'], 10000))
            if night is None and plan['dispatch_day_bps'] != plan['dispatch_night_bps'] and amount:
                bucket = 'UNCLASSIFIED'
                line['dispatch_unclassified_amount_fen'] += amount
            else:
                bucket = 'NIGHT' if night else 'DAY'
                line['dispatch_night_amount_fen' if night else 'dispatch_day_amount_fen'] += amount
            after = (round_ratio(line['dispatch_day_amount_fen'], plan['dispatch_day_bps'], 10000)
                     + round_ratio(line['dispatch_night_amount_fen'], plan['dispatch_night_bps'], 10000))
            if not refund_accounting:
                day = timezone.localdate(event['occurred_at']).isoformat()
                daily_costs[day] = add_known(daily_costs[day], after - before)
            line['addon_sources'].append({'payment_id': event['id'], 'activity_id': activity.pk,
                                          'order_id': activity.order_id, 'net_fen': amount, 'bucket': bucket})
        if refund_accounting:
            # Compute the same monthly rounded commission incrementally. This
            # keeps cross-month dispatch attribution and later same-month
            # refunds in the actual transaction day rather than moving them
            # to the dispatch or add-on payment date.
            main_activities = {row.order_id: row for row in by_person[assignment.account_id]
                               if row.kind == 'DISPATCH' and not row.recognition_month}
            addon_activity_by_payment = {event['id']: activity for activity, event
                                         in addon_sources_by_account[assignment.account_id]}
            buckets = {'DAY': 0, 'NIGHT': 0}
            source_totals = defaultdict(int)
            for event in monthly['events']:
                if event['origin'] == 'ORDER':
                    activity = main_activities.get(event['order_id'])
                    source = ('ORDER', event['order_id'])
                else:
                    payment_id = event['id'] if event['kind'] == 'PAYMENT' else event['payment_id']
                    activity = addon_activity_by_payment.get(payment_id)
                    source = ('ADDON', payment_id)
                if not activity:
                    continue
                shift = is_night(activity.performed_at, config)
                if shift is None and plan['dispatch_day_bps'] != plan['dispatch_night_bps']:
                    continue
                bucket = 'NIGHT' if shift else 'DAY'
                rate = plan['dispatch_night_bps' if shift else 'dispatch_day_bps']
                source_totals[source] += event['amount_fen']
                if source_totals[source] < 0:
                    raise serializers.ValidationError('派单退款超过当月对应付款，请核对工资凭证')
                before = round_ratio(buckets[bucket], rate, 10000)
                buckets[bucket] += event['amount_fen']
                day = timezone.localdate(event['occurred_at']).isoformat()
                daily_costs[day] = add_known(daily_costs[day], round_ratio(buckets[bucket], rate, 10000) - before)
        for source in late_addons.get(assignment.account_id, []):
            line['late_adjustments_fen'] = add_known(line['late_adjustments_fen'], source['amount_fen'])
            line['late_addon_sources'].append(source)
            effective_day = min(max(timezone.localdate(), month), month.replace(day=calendar.monthrange(month.year, month.month)[1]))
            daily_costs[effective_day.isoformat()] = add_known(daily_costs[effective_day.isoformat()], source['amount_fen'])
        for source in late_store_orders.get(assignment.account_id, []):
            line['late_adjustments_fen'] = add_known(line['late_adjustments_fen'], source['amount_fen'])
            line['late_store_order_sources'].append(source)
            effective_day = min(max(timezone.localdate(), month), month.replace(day=calendar.monthrange(month.year, month.month)[1]))
            daily_costs[effective_day.isoformat()] = add_known(daily_costs[effective_day.isoformat()], source['amount_fen'])
        line['base_fen'] = plan['base_fen']
        personal = (sum(personal_by_day.values()) if refund_accounting else
                    line['dispatch_day_amount_fen'] + line['dispatch_night_amount_fen']
                    + line['dispatch_unclassified_amount_fen'])
        line['performance_basis_fen'] = store_amount if plan['target_scope'] == 'STORE' else personal
        if plan['performance_fen']:
            if plan['target_fen'] <= 0:
                line['warnings'].append('绩效金额大于0时必须配置正数业绩目标')
            else:
                line['performance_fen'] = round_ratio(plan['performance_fen'],
                    max(0, min(plan['target_fen'], line['performance_basis_fen'])), plan['target_fen'])
                basis_by_day = store_by_day if plan['target_scope'] == 'STORE' else personal_by_day
                cumulative, previous = 0, 0
                for day, amount in sorted(basis_by_day.items()):
                    cumulative += amount
                    earned = round_ratio(plan['performance_fen'], max(0, min(plan['target_fen'], cumulative)), plan['target_fen'])
                    daily_costs[day] = add_known(daily_costs[day], earned - previous)
                    previous = earned
        line['dispatch_commission_fen'] = (round_ratio(line['dispatch_day_amount_fen'], plan['dispatch_day_bps'], 10000)
                                          + round_ratio(line['dispatch_night_amount_fen'], plan['dispatch_night_bps'], 10000))
        if line['dispatch_unclassified_amount_fen']:
            line['warnings'].append('夜班起止时间未配置，派单提成尚不能核算或确认')
        line['recharge_commission_fen'] = (None if line['recharge_amount_fen'] is None else
                                           round_ratio(line['recharge_amount_fen'], plan['recharge_bps'], 10000))
        line['aftersale_commission_fen'] = line['aftersale_count'] * plan['aftersale_fee_fen']
        line['exam_gross_fen'] = line['exam_count'] * plan['exam_fee_fen']
        # The exam fee is quoted per examination. Round each unit deduction,
        # then sum units, so the same verified exams always cost the same.
        line['exam_deduction_fen'] = line['exam_count'] * round_ratio(plan['exam_fee_fen'], plan.get('exam_commission_bps', 0), 10000)
        line['exam_commission_fen'] = line['exam_gross_fen'] - line['exam_deduction_fen']
        line['total_fen'] = sum_known(line[key] for key in (
            'base_fen', 'performance_fen', 'dispatch_commission_fen',
            'recharge_commission_fen', 'aftersale_commission_fen',
            'exam_commission_fen', 'late_adjustments_fen'))
        version_rows = reemployment_performance.get(assignment.account_id, [])
        credit = sum(max(0, row['delta_fen']) for row in version_rows)
        available = None if line['total_fen'] is None else line['total_fen'] + credit
        deducted = 0
        for row in version_rows:
            requested = max(0, -row['delta_fen'])
            if available is None:
                applied = None
                waived = None
            else:
                applied = min(available, requested)
                available -= applied
                deducted += applied
                waived = requested - applied
            line['reemployment_performance_sources'].append({**row,
                'applied_fen': (row['delta_fen'] if row['delta_fen'] >= 0 else -applied)
                              if applied is not None else None,
                'waived_fen': waived})
        if available is None:
            line['late_adjustments_fen'] = None
            line['total_fen'] = None
        else:
            line['late_adjustments_fen'] = add_known(line['late_adjustments_fen'], credit - deducted)
            line['total_fen'] = available
        if version_rows:
            effective_day = min(max(timezone.localdate(), month), month.replace(day=calendar.monthrange(month.year, month.month)[1]))
            daily_costs[effective_day.isoformat()] = add_known(daily_costs[effective_day.isoformat()], credit - deducted)
        if refund_accounting:
            today = timezone.localdate()
            effective_day = min(max(today, month), month.replace(day=calendar.monthrange(month.year, month.month)[1]))
            if line['total_fen'] is None:
                line['refund_deduction_plan'] = None
                line['late_adjustments_fen'] = None
                daily_costs[effective_day.isoformat()] = None
            else:
                from .payroll_refund_ledger import _plan
                line['refund_deduction_plan'] = _plan(assignment.account_id, month, line['total_fen'])
                line['late_adjustments_fen'] = add_known(
                    line['late_adjustments_fen'], line['refund_deduction_plan']['revision_credit_fen'])
                line['total_fen'] = line['refund_deduction_plan']['net_fen']
                daily_costs[effective_day.isoformat()] = add_known(
                    daily_costs[effective_day.isoformat()],
                    line['refund_deduction_plan']['revision_credit_fen'] - line['refund_deduction_plan']['deducted_fen'])
        warnings.extend(f"{line['account_name']}：{message}" for message in line['warnings'])
        for number in range(1, calendar.monthrange(month.year, month.month)[1] + 1):
            day = month.replace(day=number).isoformat()
            base_increment = (plan['base_fen'] * number // calendar.monthrange(month.year, month.month)[1]
                              - plan['base_fen'] * (number - 1) // calendar.monthrange(month.year, month.month)[1])
            daily_costs[day] = add_known(daily_costs[day], base_increment)
        lines.append(line)
    totals = {key: sum_known(line[key] for line in lines) for key in TOTAL_FIELDS}
    if invalid_recharge_activity_ids:
        totals['recharge_commission_fen'] = None
        totals['total_fen'] = None
    if invalid_recharge_recognition_ids:
        totals['late_adjustments_fen'] = None
    totals['employee_count'] = len(lines)
    if not assignments:
        warnings.append('该月尚未安排任何人员薪资')
    data = {'month': month_text, 'status': 'PREVIEW', 'can_confirm': bool(assignments) and not warnings,
            'warnings': warnings, 'totals': totals, 'lines': lines,
            'basis': ('人民币分；个人业绩按显式归属订单的付款和退款发生月计入；门店业绩按非礼物订单的付款和退款发生月计入。绩效封顶，考官按实得计薪。' if refund_accounting else
                      '人民币分；个人业绩为显式归属派单的退款后净消费；门店业绩为该月创建的非礼物订单净消费。绩效封顶，考官按实得计薪。'),
            'config': {'night_start': config.night_start.isoformat(timespec='minutes') if config and config.night_start else None,
                       'night_end': config.night_end.isoformat(timespec='minutes') if config and config.night_end else None,
                       'revision': config.revision if config else 0},
            'activity_ids': [row.pk for row in activities]}
    data['unattributed'] = {key: {'count': len(items), 'examples': items[:30]} for key, items in unattributed.items()}
    data['daily_costs'] = [{'day': day, 'amount_fen': amount} for day, amount in sorted(daily_costs.items())]
    data['fingerprint'] = fingerprint(data)
    return data


def _allocate_month_salary(total, month, first, last):
    """Cumulative integer allocation preserves the exact full-month amount."""
    days = calendar.monthrange(month.year, month.month)[1]
    lo, hi = max(first, month), min(last, month.replace(day=days))
    if lo > hi:
        return 0
    return total * hi.day // days - total * (lo.day - 1) // days


def snapshot_salary(snapshot, month, first, last):
    if 'daily_costs' in snapshot:
        return sum_known(row['amount_fen'] for row in snapshot['daily_costs']
                         if first.isoformat() <= row['day'] <= last.isoformat())
    total = snapshot['totals']['total_fen']
    return None if total is None else _allocate_month_salary(total, month, first, last)


def refunded_referral_cost(order):
    """Prove referral earnings separately, including a provider who also refers."""
    if not order.inviter_commission:
        return 0
    if not (order.inviter_account_id or order.inviter_id):
        return None
    txs = list(order.transactions.filter(status='SUCCESS', tx_type__in=['INCOME', 'GIFT']).select_related('wallet'))
    from orders.referral_evidence import ReferralEvidenceError, paid_referral_credits
    try:
        originals = paid_referral_credits(order, [row for row in txs if not row.reversal_of_id and row.amount > 0])
    except ReferralEvidenceError:
        return None
    refunds = list(order.refund_records.all())
    negotiated = [row for refund in refunds for row in refund.negotiated_allocation if row.get('role') == 'INVITER']
    expected = order.inviter_commission
    retained = None
    if negotiated:
        if len(refunds) != 1 or len(negotiated) != 1:
            return None
        item = negotiated[0]
        expected = item['entitlement'] if item['settled'] else item['retained']
        retained = item['retained']
    if sum(row.amount for row in originals) != expected:
        return None
    original_ids = {row.pk for row in originals}
    reversals = [row for row in txs if row.reversal_of_id in original_ids]
    # Paid referral commissions are never recovered on refund. A reversal is
    # not a supported first-version outcome and must be reconciled.
    if reversals:
        return None
    net = sum(row.amount for row in originals + reversals)
    if not 0 <= net <= expected or (retained is not None and net != retained):
        return None
    return net


def subsidy_liability(share):
    assignment = share.assignment
    if assignment.order_id != share.subsidy.order_id:
        return None
    if not share.paid_at:
        if share.expense_transaction_id or share.income_transaction_id or assignment.settled_at:
            return None
        return share.amount
    if share.amount == 0 and not share.expense_transaction_id and not share.income_transaction_id:
        return 0 if assignment.settled_at else None
    expense, income = share.expense_transaction, share.income_transaction
    if (not expense or not income or not assignment.settled_at
            or (expense.status, income.status) != ('SUCCESS', 'SUCCESS')):
        return None
    if (expense.tx_type != 'SUBSIDY_EXPENSE' or expense.amount != -share.amount
            or income.amount != share.amount
            or income.tx_type != 'INCOME' or expense.order_id != share.subsidy.order_id
            or income.order_id != share.subsidy.order_id
            or not expense.wallet.user or expense.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
            or income.wallet.account_id != assignment.provider_account_id
            or income.wallet.user_id != assignment.provider_id
            or expense.balance_after != expense.balance_before + expense.amount
            or income.balance_after != income.balance_before + income.amount
            or expense.reversal_of_id or income.reversal_of_id or income.reversals.exists()):
        return None
    reversals = list(Transaction.objects.filter(reversal_of=expense, status='SUCCESS'))
    if any(row.wallet_id != expense.wallet_id or row.amount < 0 for row in reversals):
        return None
    amount = -(expense.amount + sum(row.amount for row in reversals))
    return amount if amount >= 0 else None


def operating_roi(first, last):
    zone = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(first, time.min), zone)
    end = timezone.make_aware(datetime.combine(last + timedelta(days=1), time.min), zone)
    from .roi_period import ordinary_negotiation_cost, period_revenue, points_negotiation_cost, service_payment_cost
    revenue = period_revenue(start, end)
    net_consumption = raw_to_fen(revenue['net_units']) if revenue['net_units'] is not None else None
    paid_consumption = raw_to_fen(revenue['paid_units']) if revenue['paid_units'] is not None else None
    refunded_consumption = raw_to_fen(revenue['refunded_units']) if revenue['refunded_units'] is not None else None
    refunded_points_value = (raw_to_fen(revenue['refunded_points_value_units'])
                             if revenue['refunded_points_value_units'] is not None else None)
    roas_consumption = net_consumption
    # Include newly paid orders in the liability review even before completion.
    # Their captured provider terms can already establish the expected cost.
    settled_or_refunded = Order.objects.filter(payment_status__in=[*PAID_STATES, 'REFUNDED'], direct_payment__isnull=True)
    cohort = with_net_amount(settled_or_refunded.annotate(recognition_at=Coalesce('completed_at', 'settled_at')).filter(
        recognition_at__gte=start, recognition_at__lt=end).filter(Q(status='COMPLETED') | Q(settled_at__isnull=False)))
    rows = list(cohort.prefetch_related('providers'))
    completed_ids = {row.pk for row in rows}
    rows.extend(Order.objects.filter(pk__in=revenue['payment_order_ids'] - completed_ids)
                .prefetch_related('providers'))
    candidates = {row.pk: row for row in rows}
    related_ids = revenue['refund_order_ids'] | revenue['addon_order_ids']
    for order in Order.objects.filter(pk__in=related_ids - set(candidates)).prefetch_related('providers'):
        candidates[order.pk] = order
    event_costs = {}
    invalid_events = set(revenue['invalid_order_ids']) | set(revenue['invalid_points_order_ids'])
    from orders.models import OrderAddonPayment, OrderPointsRedemption, OrderRefund
    refunded_candidates = set(OrderRefund.objects.filter(order_id__in=candidates).values_list('order_id', flat=True))
    points_candidates = set(OrderPointsRedemption.objects.filter(order_id__in=candidates)
                            .values_list('order_id', flat=True))
    unverified_service_costs = set()
    for order in candidates.values():
        if order.pk not in invalid_events and order.amount > 0:
            if order.is_gift_snapshot:
                from .gift_roi import costs as gift_costs
                result = gift_costs(order.pk, start, end)
            elif order.pk in refunded_candidates and order.pk in points_candidates:
                result = points_negotiation_cost(order, start, end)
                if result is None:
                    from .points_single_addon_roi import costs as points_single_addon_costs
                    result = points_single_addon_costs(order.pk, start, end)
                if result is None:
                    from .points_followup_roi import costs as points_followup_costs
                    result = points_followup_costs(order.pk, start, end)
                if result is None:
                    from .points_transfer_refund_roi import costs as points_transfer_refund_costs
                    result = points_transfer_refund_costs(order.pk, start, end)
                if result is None:
                    from .points_transfer_followup_roi import costs as points_transfer_followup_costs
                    result = points_transfer_followup_costs(order.pk, start, end)
            elif order.pk in refunded_candidates:
                from .referral_funded_roi import costs as referral_funded_costs
                result = referral_funded_costs(order.pk, start, end)
                if result is None:
                    result = ordinary_negotiation_cost(order, start, end)
                if result is None:
                    from .ordinary_followup_roi import costs as ordinary_followup_costs
                    result = ordinary_followup_costs(order.pk, start, end)
                if result is None:
                    from .ordinary_transfer_refund_roi import costs as transfer_refund_costs
                    result = transfer_refund_costs(order.pk, start, end)
            else:
                result = service_payment_cost(order, start, end)
                if result is None and order.pk in points_candidates:
                    from .points_team_addon_roi import costs as points_team_addon_costs
                    result = points_team_addon_costs(order.pk, start, end)
                if result is None:
                    from .segmented_roi import costs as segmented_costs
                    result = segmented_costs(order.pk, start, end)
                if result is None:
                    from .ordinary_transfer_refund_roi import costs as transfer_refund_costs
                    result = transfer_refund_costs(order.pk, start, end)
            if result is not None:
                event_costs[order.pk] = result
            else:
                unverified_service_costs.add(order.pk)
    rows = [row for row in rows if row.pk not in event_costs]
    from orders.analytics import partial_share_rows
    partial = defaultdict(list)
    # Existing analytics deliberately refuses estimates when a partial refund
    # cannot be matched to original successful earnings and their reversals.
    for order in rows:
        if order.payment_status in ('PARTIALLY_REFUNDED', 'REFUNDED'):
            partial[order.pk] = partial_share_rows(Order.objects.filter(pk=order.pk), include_refunded=True)
    provider_cost = sum(row['provider'] for row in event_costs.values())
    referral_cost = sum(row['referral'] for row in event_costs.values())
    subsidy_cost = 0
    unknown, unknown_referral, unknown_subsidy = [], [], []
    for order in rows:
        referral = refunded_referral_cost(order) if order.payment_status in ('PARTIALLY_REFUNDED', 'REFUNDED') else order.inviter_commission
        if referral is None:
            unknown_referral.append(order.pk)
        else:
            referral_cost += referral
        if order.payment_status in ('PARTIALLY_REFUNDED', 'REFUNDED'):
            shares = partial[order.pk]
            if not shares or any(row['income'] is None for row in shares):
                unknown.append(order.pk)
            else:
                provider_cost += sum(row['income'] for row in shares)
        else:
            members = [row for row in order.providers.all()
                       if row.released_at is None or row.settlement_base > 0 or row.provider_income > 0]
            if members:
                provider_cost += sum(row.provider_income for row in members)
            elif order.provider_account_id or order.provider_id or order.amount == 0:
                provider_cost += order.provider_income
            else:
                unknown.append(order.pk)
    provider_cost = raw_to_fen(provider_cost)
    referral_cost = raw_to_fen(referral_cost)
    # Existing cost snapshots describe a fulfilled-order cohort, not arbitrary
    # payment/refund events. Use them only when the two periods coincide;
    # otherwise show the real revenue and leave ROI pending reconciliation.
    from engagement.models import OrderFundingAllocation
    from orders.models import OrderAddonPayment, OrderRefund
    from orders.models import OrderOriginalShareTransfer, OrderPrestartHandoff
    cohort_ids = {row.pk for row in rows if row.amount > 0}
    cost_review = (set(revenue['invalid_order_ids']) | set(revenue['invalid_points_order_ids'])
                   | unverified_service_costs | (cohort_ids ^ revenue['payment_order_ids']))
    cost_review.update(revenue['refund_order_ids'] - revenue['payment_order_ids'])
    cost_review.update(revenue['addon_order_ids'])
    cost_review.update(points_candidates)
    cost_review.update(OrderAddonPayment.objects.filter(intent__order_id__in=cohort_ids)
                       .values_list('intent__order_id', flat=True))
    cost_review.update(OrderPrestartHandoff.objects.filter(order_id__in=cohort_ids)
                       .values_list('order_id', flat=True))
    cost_review.update(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order_id__in=cohort_ids)
                       .values_list('previous_segment__attribution__order_id', flat=True))
    cost_review.update(OrderRefund.objects.filter(order_id__in=cohort_ids)
                       .exclude(created_at__gte=start, created_at__lt=end).values_list('order_id', flat=True))
    cost_review.difference_update(event_costs)
    gift_compensation_cost = sum(row['gift_compensation'] for row in event_costs.values())
    for order in rows:
        if order.amount == 0 or order.is_gift_snapshot:
            continue
        allocations = list(OrderFundingAllocation.objects.filter(order=order))
        if (not allocations or sum(item.amount for item in allocations) != order.amount
                or any(item.refunded_amount > item.amount for item in allocations)):
            cost_review.add(order.pk)
            continue
        gift_compensation_cost += sum(item.amount - item.refunded_amount for item in allocations
                                      if item.kind in ('GIFT', 'COMPENSATION'))
    gift_compensation_cost = raw_to_fen(gift_compensation_cost)
    from orders.models import SubsidyAgreementExecution
    from .subsidy_agreement_roi import agreement_period_cost
    agreement_order_ids = set(SubsidyAgreementExecution.objects.filter(
        Q(processed_at__gte=start, processed_at__lt=end)
        | Q(created_at__gte=start, created_at__lt=end)).values_list('order_id', flat=True))
    relevant_subsidy_orders = {row.pk for row in rows} | agreement_order_ids
    seen_subsidy_orders, agreement_costs = set(), {}
    for share in OrderPlatformSubsidyShare.objects.filter(subsidy__order_id__in=relevant_subsidy_orders).select_related(
            'subsidy__order', 'expense_transaction', 'income_transaction'):
        order = share.subsidy.order
        seen_subsidy_orders.add(order.pk)
        execution = getattr(order, 'subsidy_agreement_execution', None)
        if execution:
            if order.pk in agreement_costs:
                continue
            value = agreement_period_cost(order, execution, start, end)
            agreement_costs[order.pk] = value
        else:
            value = subsidy_liability(share)
        if value is None:
            unknown_subsidy.append(share.subsidy.order_id)
        else:
            subsidy_cost += raw_to_fen(value)
    unknown_subsidy.extend(agreement_order_ids - seen_subsidy_orders)
    costs = list(OperatingCost.objects.filter(is_void=False, day__gte=first, day__lte=last).select_related('category', 'channel'))
    daily = defaultdict(lambda: {'ad_spend_fen': 0, 'other_cost_fen': 0})
    ad_spend = other_cost = 0
    for row in costs:
        key = 'ad_spend_fen' if row.category.code == 'AD_SPEND' else 'other_cost_fen'
        daily[row.day.isoformat()][key] += row.amount_fen
        if key == 'ad_spend_fen':
            ad_spend += row.amount_fen
        else:
            other_cost += row.amount_fen
    salary_cost, estimated_salary_cost, unconfirmed, invalid_salary = 0, 0, [], []
    month = first.replace(day=1)
    while month <= last:
        run = PayrollRun.objects.filter(month=month).first()
        if run:
            value = snapshot_salary(run.snapshot, month, first, last)
            salary_cost = add_known(salary_cost, value)
            estimated_salary_cost = add_known(estimated_salary_cost, value)
            if value is None:
                invalid_salary.append({
                    'month': month.strftime('%Y-%m'),
                    'warnings': ['已确认工资快照中的薪资金额未知，请核对后再查看经营ROI'],
                })
        else:
            unconfirmed.append(month.strftime('%Y-%m'))
            preview = calculate_payroll(month)
            if preview['can_confirm']:
                estimated_salary_cost = add_known(
                    estimated_salary_cost, snapshot_salary(preview, month, first, last))
            else:
                invalid_salary.append({'month': month.strftime('%Y-%m'), 'warnings': preview['warnings']})
        month = month + timedelta(days=calendar.monthrange(month.year, month.month)[1])
    shop_income = raw_to_fen(Transaction.objects.filter(
        wallet__user__username=settings.PLATFORM_SYSTEM_USERNAME, tx_type='SHOP_INCOME', status='SUCCESS',
        order__direct_payment__isnull=True, created_at__gte=start, created_at__lt=end).aggregate(v=Sum('amount'))['v'] or 0)
    ad_days = {row.day for row in costs if row.category.code == 'AD_SPEND'}
    missing_ad_dates = []
    cursor = first
    while cursor <= last:
        if cursor not in ad_days:
            missing_ad_dates.append(cursor.isoformat())
        cursor += timedelta(days=1)
    from .continuation_roi import continuation_reward_cost
    continuation_units, invalid_continuation_rewards = continuation_reward_cost(start, end)
    continuation_cost = raw_to_fen(continuation_units)
    complete = (net_consumption is not None and not cost_review and not unknown and not unknown_referral
                and not unknown_subsidy and not invalid_continuation_rewards and not unconfirmed
                and not invalid_salary and not missing_ad_dates)
    total_cost = provider_cost + referral_cost + subsidy_cost + gift_compensation_cost + continuation_cost + salary_cost + ad_spend + other_cost if complete else None
    profit = net_consumption - total_cost if total_cost is not None else None
    warnings = []
    if revenue['invalid_order_ids']:
        warnings.append('部分订单的付款或退款流水待核对，期间净消费暂不可用')
    if revenue['invalid_points_order_ids']:
        warnings.append('部分订单的退还积分流水待核对，退还积分统计暂不可用')
    if cost_review:
        warnings.append('部分订单的付款、退款或加购与成本发生期间不同，经营成本及 ROI 待核对')
    if unknown:
        warnings.append('部分订单的退款后陪玩成本无法由现有分账和冲正凭证证明，经营ROI待核对')
    if unknown_referral:
        warnings.append('部分订单的退款后推荐分佣无法由原入账与冲正凭证证明，经营ROI待核对')
    if unknown_subsidy:
        warnings.append('部分订单的平台补贴支出无法由现有凭证证明，经营ROI待核对')
    if invalid_continuation_rewards:
        warnings.append('部分续单奖励凭证与钱包流水不一致，经营ROI待核对')
    if unconfirmed:
        warnings.append('查询范围内存在未确认薪资月份；提供有明确人员安排的暂估结果，不将未知薪资当作零成本')
    if invalid_salary:
        warnings.append('查询范围内存在薪资金额未知的月份；员工薪资、经营成本及 ROI 待核对')
    if missing_ad_dates:
        warnings.append('查询范围内部分日期尚未录入投流成本；未投放请明确录入0元，缺失不视为已核对0成本')
    if ad_spend == 0:
        warnings.append('投流成本为0，净消费/投流指标不计算比值')
    provisional_complete = (net_consumption is not None and not cost_review and not unknown and not unknown_referral
                            and not unknown_subsidy and not invalid_continuation_rewards and not invalid_salary and not missing_ad_dates)
    provisional_total = provider_cost + referral_cost + subsidy_cost + gift_compensation_cost + continuation_cost + estimated_salary_cost + ad_spend + other_cost if provisional_complete else None
    provisional_profit = net_consumption - provisional_total if provisional_total is not None else None
    partial_settlements = []
    verified_settlements = []
    for item in event_costs.values():
        verified = item.get('verified_settlement')
        if verified is not None:
            verified_settlements.append({
                'order_id': verified['order_id'], 'observed_at': verified['observed_at'],
                'status': verified['status'], 'settled_segment_ids': verified['settled_segment_ids'],
                'income_posted_in_period_fen': raw_to_fen(verified['income_posted_in_period_units']),
                'pending_segment_id': verified['pending_segment_id'],
                'pending_segment_ids': verified.get('pending_segment_ids', []),
                'pending_main_fen': raw_to_fen(verified['pending_main_units']),
                'pending_addon_fen': raw_to_fen(verified['pending_addon_units']),
            })
        evidence = item.get('partial_settlement')
        if evidence is None:
            continue
        partial_settlements.append({
            'order_id': evidence['order_id'], 'observed_at': evidence['observed_at'],
            'settled_segment_id': evidence['settled_segment_id'], 'settled_at': evidence['settled_at'],
            'income_posted_in_period_fen': raw_to_fen(evidence['income_posted_in_period_units']),
            'pending_segment_id': evidence['pending_segment_id'],
            'pending_main_fen': raw_to_fen(evidence['pending_main_units']),
            'pending_addon_fen': raw_to_fen(evidence['pending_addon_units']),
        })
    return {'start_date': first.isoformat(), 'end_date': last.isoformat(), 'currency': 'CNY',
            'roas': {'net_consumption_fen': roas_consumption, 'paid_consumption_fen': paid_consumption,
                     'refunded_consumption_fen': refunded_consumption,
                     'refunded_points_hundredths': revenue['refunded_points_hundredths'],
                     'refunded_points_value_fen': refunded_points_value, 'ad_spend_fen': ad_spend,
                     'value': roas_consumption / ad_spend if roas_consumption is not None and ad_spend and not missing_ad_dates else None,
                     'missing_ad_dates': missing_ad_dates,
                     'basis': '付款按实际付款时间归属，退款按发生时间冲减；店币净消费不等于现金实收'},
            'operating': {'net_consumption_fen': net_consumption, 'paid_consumption_fen': paid_consumption,
                          'refunded_consumption_fen': refunded_consumption,
                          'refunded_points_hundredths': revenue['refunded_points_hundredths'],
                          'refunded_points_value_fen': refunded_points_value,
                          'provider_cost_fen': None if unknown or cost_review else provider_cost,
                          'referral_cost_fen': None if unknown_referral or cost_review else referral_cost,
                          'continuation_reward_cost_fen': None if invalid_continuation_rewards else continuation_cost,
                          'invalid_continuation_reward_ids': invalid_continuation_rewards,
                          'gift_compensation_cost_fen': None if cost_review else gift_compensation_cost,
                          'subsidy_cost_fen': None if unknown_subsidy or cost_review else subsidy_cost,
                          'known_provider_cost_fen': None if cost_review else provider_cost,
                          'partly_settled_provider_evidence': partial_settlements,
                          'partly_settled_provider_evidence_scope': '仅列本次查询已核实的部分结算订单；期间入账按钱包流水时间，待结算按查询时现状，不代表全店负债或历史期末余额',
                          'verified_provider_settlement_evidence': verified_settlements,
                          'verified_provider_settlement_evidence_scope': '仅列本次查询适配且已核实的积分服务订单；期间入账按钱包流水时间，待结算和结算状态按查询时现状，不代表全店汇总或历史期末余额',
                          'salary_cost_fen': None if unconfirmed or invalid_salary else salary_cost,
                          'ad_spend_fen': ad_spend, 'other_cost_fen': other_cost, 'total_cost_fen': total_cost,
                          'profit_fen': profit, 'roi': profit / total_cost if total_cost else None,
                          'realized_shop_income_fen': shop_income, 'order_count': len(revenue['order_ids']),
                          'unknown_cost_order_ids': sorted(set(unknown + unknown_referral + unknown_subsidy) | cost_review),
                          'revenue_review_order_ids': revenue['invalid_order_ids'],
                          'points_refund_review_order_ids': revenue['invalid_points_order_ids'],
                          'unknown_referral_order_ids': unknown_referral, 'salary_unconfirmed_months': unconfirmed,
                          'complete': complete},
            'provisional': {'net_consumption_fen': net_consumption, 'provider_cost_fen': None if unknown or cost_review else provider_cost,
                            'referral_cost_fen': None if unknown_referral or cost_review else referral_cost,
                            'continuation_reward_cost_fen': None if invalid_continuation_rewards else continuation_cost,
                            'gift_compensation_cost_fen': None if cost_review else gift_compensation_cost,
                            'subsidy_cost_fen': None if unknown_subsidy or cost_review else subsidy_cost,
                            'salary_cost_fen': None if invalid_salary else estimated_salary_cost,
                            'ad_spend_fen': ad_spend, 'other_cost_fen': other_cost,
                            'total_cost_fen': provisional_total, 'profit_fen': provisional_profit,
                            'roi': provisional_profit / provisional_total if provisional_total else None,
                            'complete': provisional_complete, 'estimated': bool(unconfirmed),
                            'salary_unconfirmed_months': unconfirmed, 'invalid_salary_months': invalid_salary,
                            'missing_ad_dates': missing_ad_dates},
            'daily': [{'day': day, **values} for day, values in sorted(daily.items())], 'warnings': warnings,
            'basis': {'operating': '(期间净消费－陪玩成本－推荐分佣－续单奖励－平台补贴－赠送及赔付成本－员工薪资－投流费－其他已录成本)÷总成本',
                      'orders': '付款和退款分别按各自发生时间归属；模拟直付订单不计入',
                      'provider_cost': '按付款时可证明的收益快照确认应付成本；已接通的协商退款在退款日冲减实际承担，其他无法核对的成本标记待核对',
                      'salary': '已确认用快照，未确认提供明确标注暂估；仅底薪按当月日历天数累计整数分摊，绩效和提成按真实业绩日，历史快照不随方案改变',
                      'realized_shop_income': '仅对照：查询期内成功平台留存流水含退款冲正；与订单批次可能跨期，不替代经营ROI收入',
                      'exclusions': '充值本金、赠币充值、押金和提现本金不作为营业收入；净消费不等于现金实收'}}
