"""Payment and refund events for period-correct payroll accounting."""
from collections import defaultdict
from django.db.models import F
from django.db.models.functions import Coalesce

from rest_framework.exceptions import ValidationError

from orders.models import OrderRefund, OrderAddonPayment, OrderAddonRefund
from wallet.models import Transaction
from stores.db import transaction
from .payroll_calculation import month_bounds, raw_to_fen, round_ratio
from .payroll_models import PayrollLock, PayrollRefundPerformanceAdjustment, PayrollRun, SalaryActivity


def refund_event_queryset(query, *, addon=False):
    """Select candidates by the actual linked customer credit, then verify it."""
    if addon:
        return query.annotate(payroll_refund_time=F('buyer_transaction__created_at'))
    return query.annotate(payroll_refund_time=Coalesce(
        'customer_transaction__created_at', 'points_refund__coin_transaction__created_at',
        'negotiation_execution__customer_transaction__created_at', 'created_at'))


def refund_event_time(refund):
    if not refund.amount:
        return refund.created_at
    return (verify_addon_refund_wallet_event(refund) if isinstance(refund, OrderAddonRefund)
            else verify_refund_wallet_event(refund)).created_at


def addon_payment_time(payment):
    """Verify the debit and return the persisted add-on payment time."""
    from django.utils import timezone
    tx = payment.buyer_transaction
    if (tx.status != 'SUCCESS' or tx.tx_type != 'PAY' or tx.order_id is not None
            or tx.amount != -payment.intent.amount or tx.wallet.account_id != payment.intent.buyer_id
            or tx.balance_after != tx.balance_before + tx.amount or tx.reversal_of_id
            or tx.reversals.exists() or not payment.paid_at or timezone.is_naive(payment.paid_at)
            or not tx.created_at or timezone.is_naive(tx.created_at) or payment.paid_at > tx.created_at):
        raise ValidationError('加购实付流水待核对，不能计算月度业绩')
    month = timezone.localdate(payment.paid_at).replace(day=1)
    run = PayrollRun.objects.filter(month=month).first()
    if run and payment.paid_at <= run.confirmed_at < tx.created_at:
        raise ValidationError('加购付款与工资确认的先后时间待核对')
    return payment.paid_at


def verify_refund_wallet_event(refund):
    """Match the recorded refund to an actual customer-wallet credit."""
    from django.utils import timezone
    from orders.models import NegotiationExecution, OrderPointsRefund

    points = OrderPointsRefund.objects.filter(refund_id=refund.pk).select_related('coin_transaction__wallet').first()
    negotiation = NegotiationExecution.objects.filter(refund_id=refund.pk).select_related('customer_transaction__wallet').first()
    linked = [row for row in (refund.customer_transaction,
                              points.coin_transaction if points else None,
                              negotiation.customer_transaction if negotiation else None) if row is not None]
    if (points and not points.coin_transaction_id) or (negotiation and not negotiation.customer_transaction_id):
        raise ValidationError('主单退款缺少关联的钱包流水，不能核算工资')
    if len({row.pk for row in linked}) > 1:
        raise ValidationError('主单退款与实际钱包流水关联不一致，不能核算工资')
    if linked:
        tx = linked[0]
    else:
        if OrderRefund.objects.filter(order_id=refund.order_id, amount=refund.amount,
                operator_id=refund.operator_id, operator_account_id=refund.operator_account_id).count() != 1:
            raise ValidationError('相同主单退款无法唯一关联钱包流水，不能核算工资')
        matches = list(Transaction.objects.filter(order_id=refund.order_id, wallet__account_id=refund.order.customer_account_id,
            tx_type='REFUND', status='SUCCESS', amount=refund.amount, operator_id=refund.operator_id,
            operator_account_id=refund.operator_account_id).select_related('wallet')[:2])
        if len(matches) != 1:
            raise ValidationError('主单退款缺少唯一可核实的钱包流水，不能核算工资')
        tx = matches[0]
    payments = list(Transaction.objects.filter(order_id=refund.order_id, tx_type='PAY', status='SUCCESS',
                                               amount__lte=0).select_related('wallet')[:2])
    if (tx.order_id != refund.order_id or tx.wallet.account_id != refund.order.customer_account_id
            or tx.wallet.user_id != refund.order.customer_id or tx.tx_type != 'REFUND' or tx.status != 'SUCCESS'
            or tx.amount != refund.amount or tx.operator_id != refund.operator_id
            or tx.operator_account_id != refund.operator_account_id or tx.reversal_of_id or tx.reversals.exists()
            or tx.balance_after != tx.balance_before + tx.amount
            or len(payments) != 1 or payments[0].wallet_id != tx.wallet_id
            or payments[0].amount != -refund.order.amount or payments[0].created_at > tx.created_at
            or payments[0].balance_after != payments[0].balance_before + payments[0].amount
            or payments[0].reversal_of_id or payments[0].reversals.exists()):
        raise ValidationError('主单退款钱包流水或发生月份待核对，不能核算工资')
    if not linked:
        # The fallback candidate is located by the refund record's time. A
        # month or confirmation boundary between the two needs a direct link.
        month = timezone.localdate(refund.created_at).replace(day=1)
        run = PayrollRun.objects.filter(month=month).first()
        if (timezone.localdate(tx.created_at).replace(day=1) != month
                or (run and ((refund.created_at <= run.confirmed_at) != (tx.created_at <= run.confirmed_at)))):
            raise ValidationError('退款缺少直接流水关联，跨月或工资确认时点待核对')
    return tx


def verify_addon_refund_wallet_event(refund):
    """Prove the add-on customer credit and its event period."""
    from django.utils import timezone
    paid_at, tx = addon_payment_time(refund.payment), refund.buyer_transaction
    if (paid_at > refund.created_at or refund.payment.buyer_transaction.created_at > refund.created_at
            or refund.payment.intent.order_id != refund.order_id
            or refund.proposal.payment_id != refund.payment_id or refund.proposal.amount != refund.amount
            or tx.status != 'SUCCESS' or tx.tx_type != 'REFUND' or tx.amount != refund.amount
            or tx.wallet_id != refund.payment.buyer_transaction.wallet_id or tx.order_id is not None
            or tx.operator_account_id != refund.operator_id or tx.operator_id != refund.legacy_operator_id
            or paid_at > tx.created_at
            or tx.reversal_of_id or tx.reversals.exists() or tx.balance_after != tx.balance_before + tx.amount):
        raise ValidationError('加购退款钱包流水或发生月份待核对，不能核算工资')
    return tx


def monthly_payment_refund_events(month, *, cutoff=None, period_end=None):
    """Return actual service payments and returned store coins in their event month."""
    start, end = month_bounds(month)
    payments = Transaction.objects.filter(tx_type='PAY', status='SUCCESS', amount__lt=0,
        order__isnull=False, order__direct_payment__isnull=True, order__is_gift_snapshot=False,
        created_at__gte=start, created_at__lt=end).select_related('order', 'wallet')
    # A fully discounted service can produce a zero-value cancellation receipt.
    # It has no returned store coins and is not a negative payroll event.
    refunds = refund_event_queryset(OrderRefund.objects.filter(amount__gt=0,
        order__direct_payment__isnull=True, order__is_gift_snapshot=False)).filter(
            payroll_refund_time__gte=start, payroll_refund_time__lt=end).select_related('order')
    addons = OrderAddonPayment.objects.filter(paid_at__gte=start, paid_at__lt=end,
        intent__order__direct_payment__isnull=True, intent__order__is_gift_snapshot=False)
    addons = addons.select_related('intent__order', 'buyer_transaction__wallet')
    addon_refunds = refund_event_queryset(OrderAddonRefund.objects.filter(
        order__direct_payment__isnull=True, order__is_gift_snapshot=False), addon=True).filter(
            payroll_refund_time__gte=start, payroll_refund_time__lt=end).select_related('payment__intent', 'payment__buyer_transaction__wallet', 'buyer_transaction')
    if cutoff is not None:
        payments, refunds = payments.filter(created_at__lte=cutoff), refunds.filter(payroll_refund_time__lte=cutoff)
        addons = addons.filter(paid_at__lte=cutoff)
        addon_refunds = addon_refunds.filter(payroll_refund_time__lte=cutoff)
    if period_end is not None:
        payments, refunds = payments.filter(created_at__lt=period_end), refunds.filter(payroll_refund_time__lt=period_end)
        addons = addons.filter(paid_at__lt=period_end)
        addon_refunds = addon_refunds.filter(payroll_refund_time__lt=period_end)
    payments, refunds, addons, addon_refunds = list(payments), list(refunds), list(addons), list(addon_refunds)
    order_ids = ({row.order_id for row in payments} | {row.order_id for row in refunds}
                 | {row.intent.order_id for row in addons} | {row.order_id for row in addon_refunds})
    original_payments = defaultdict(list)
    for payment in Transaction.objects.filter(order_id__in=order_ids, tx_type='PAY', status='SUCCESS',
                                               amount__lte=0).select_related('order', 'wallet'):
        original_payments[payment.order_id].append(payment)
    owner_rows = SalaryActivity.objects.filter(order_id__in=order_ids, kind='DISPATCH', is_void=False)
    if cutoff is not None:
        # The payment may precede confirmation while its employee ownership
        # is verified later. A historical cutoff must not see that future
        # attribution; later payroll can then recognise the actual source.
        owner_rows = owner_rows.filter(created_at__lte=cutoff)
    owners = {row.order_id: row.account_id for row in owner_rows}
    for order_id in order_ids:
        originals = original_payments[order_id]
        if (len(originals) != 1 or originals[0].wallet.account_id != originals[0].order.customer_account_id
                or originals[0].amount != -originals[0].order.amount or originals[0].reversal_of_id
                or originals[0].reversals.exists()
                or originals[0].balance_after != originals[0].balance_before + originals[0].amount):
            raise ValidationError('订单实付流水待核对，不能计算月度业绩')
    events = [
        {'kind': 'PAYMENT', 'origin': 'ORDER', 'id': item.pk, 'order_id': item.order_id, 'account_id': owners.get(item.order_id),
         'occurred_at': item.created_at, 'amount_fen': raw_to_fen(-item.amount)}
        for item in payments
    ]
    for item in refunds:
        tx = verify_refund_wallet_event(item)
        if ((cutoff is not None and tx.created_at > cutoff)
                or (period_end is not None and tx.created_at >= period_end)):
            raise ValidationError('主单退款实际入账跨越工资核算截止时间，请先核对')
        events.append({'kind': 'REFUND', 'origin': 'ORDER', 'id': item.pk, 'order_id': item.order_id,
                       'account_id': owners.get(item.order_id), 'occurred_at': tx.created_at,
                       'amount_fen': -raw_to_fen(item.amount)})
    for item in addons:
        paid_at = addon_payment_time(item)
        if cutoff is not None and item.buyer_transaction.created_at > cutoff:
            raise ValidationError('加购实付流水晚于工资核算截止时间，请先核对')
        events.append({'kind': 'PAYMENT', 'origin': 'ADDON', 'id': item.pk, 'order_id': item.intent.order_id,
                       'account_id': owners.get(item.intent.order_id), 'occurred_at': paid_at,
                       'amount_fen': raw_to_fen(item.intent.amount)})
    for item in addon_refunds:
        tx = verify_addon_refund_wallet_event(item)
        if ((cutoff is not None and tx.created_at > cutoff)
                or (period_end is not None and tx.created_at >= period_end)):
            raise ValidationError('加购退款实际入账跨越工资核算截止时间，请先核对')
        events.append({'kind': 'REFUND', 'origin': 'ADDON', 'id': item.pk, 'payment_id': item.payment_id,
                       'order_id': item.order_id, 'account_id': owners.get(item.order_id),
                       'occurred_at': tx.created_at, 'amount_fen': -raw_to_fen(item.amount)})
    return sorted(events, key=lambda row: (row['occurred_at'], row['origin'], row['kind'], row['id']))


def _performance(basis, plan):
    if not plan['performance_fen']:
        return 0
    return round_ratio(plan['performance_fen'], max(0, min(plan['target_fen'], basis)), plan['target_fen'])


def monthly_performance_bases(month):
    """Aggregate one month's actual payments and refunds without carrying deficits forward."""
    events = monthly_payment_refund_events(month)
    personal = defaultdict(int)
    store = 0
    for event in events:
        store += event['amount_fen']
        if event['account_id'] is not None:
            personal[event['account_id']] += event['amount_fen']
    return {'store_fen': store, 'personal_fen': dict(personal), 'events': events}


def monthly_performance(month, account_id, plan):
    bases = monthly_performance_bases(month)
    basis = bases['store_fen'] if plan['target_scope'] == 'STORE' else bases['personal_fen'].get(account_id, 0)
    return {'basis_fen': basis, 'performance_fen': _performance(basis, plan)}


def closed_period_refund_performance(run_id, *, cutoff=None, period_end=None):
    """Calculate later refund effects without changing a confirmed payroll snapshot."""
    run = PayrollRun.objects.get(pk=run_id)
    monthly_events = monthly_payment_refund_events(run.month, cutoff=cutoff, period_end=period_end)
    events = [row for row in monthly_events if row['kind'] == 'REFUND']
    adjustments = []
    for line in run.snapshot.get('lines', []):
        plan = line['plan_snapshot']
        basis = line['performance_basis_fen']
        from .payroll_models import PayrollSeparationSettlement
        closeout = PayrollSeparationSettlement.objects.filter(
            account_id=line['account'], final_run_id=run.pk).order_by('confirmed_at', 'pk').first()
        # Include positive original-month sales absent from the confirmed
        # snapshot before calculating this month's separate refund effect.
        # Refunds already reflected in a late source at confirmation are
        # excluded from its positive amount here as well.
        recorded_orders = set(SalaryActivity.objects.filter(
            pk__in=run.snapshot['activity_ids'], account_id=line['account'],
            kind='DISPATCH', is_void=False).values_list('order_id', flat=True))
        recorded_addons = {source['payment_id'] for source in line.get('addon_sources', [])}
        late_payments = []
        for paid in monthly_events:
            if paid['kind'] != 'PAYMENT':
                continue
            if plan['target_scope'] == 'STORE':
                late = paid['occurred_at'] > run.confirmed_at
            else:
                if paid['account_id'] != line['account']:
                    continue
                late = (paid['order_id'] not in recorded_orders if paid['origin'] == 'ORDER'
                        else paid['id'] not in recorded_addons)
            if not late:
                continue
            # The old employee's scheme ends at separation; later sales
            # belong to a separately registered reemployment period.
            if closeout and paid['occurred_at'] >= closeout.confirmed_at:
                continue
            if paid['occurred_at'] > run.confirmed_at:
                late_payments.append(paid)
                continue
            amount = paid['amount_fen']
            for refund in events:
                if refund['occurred_at'] > run.confirmed_at or refund['origin'] != paid['origin']:
                    continue
                if ((paid['origin'] == 'ORDER' and refund['order_id'] == paid['order_id'])
                        or (paid['origin'] == 'ADDON' and refund['payment_id'] == paid['id'])):
                    amount += refund['amount_fen']
            basis += amount
        # A sale after a refund must not affect the performance immediately
        # before that refund. Replay payments and refunds in event order;
        # later sales are paid through their own late-recognition path.
        later = sorted([*late_payments, *(event for event in events if event['occurred_at'] > run.confirmed_at)],
                       key=lambda row: (row['occurred_at'], row['origin'], row['kind'], row['id']))
        for event in later:
            if event['kind'] == 'PAYMENT':
                basis += event['amount_fen']
                continue
            if plan['target_scope'] == 'PERSON' and event['account_id'] != line['account']:
                continue
            before = _performance(basis, plan)
            basis += event['amount_fen']
            after = _performance(basis, plan)
            adjustments.append({'account_id': line['account'], 'refund_id': event['id'], 'refund_origin': event['origin'],
                'original_run_id': run.pk, 'performance_before_fen': before,
                'performance_after_fen': after, 'adjustment_fen': after - before})
    return adjustments


@transaction.atomic
def capture_closed_period_refund_performance(run_id, *, cutoff=None, period_end=None, account_id=None):
    """Persist one traceable adjustment per employee and refund, without changing the run."""
    PayrollLock.objects.get_or_create(pk=1)
    PayrollLock.objects.select_for_update().get(pk=1)
    rows = []
    for item in closed_period_refund_performance(run_id, cutoff=cutoff, period_end=period_end):
        if account_id is not None and item['account_id'] != account_id:
            continue
        if cutoff is not None or period_end is not None:
            model = OrderAddonRefund if item['refund_origin'] == 'ADDON' else OrderRefund
            occurred = refund_event_time(model.objects.get(pk=item['refund_id']))
            if (cutoff is not None and occurred > cutoff) or (period_end is not None and occurred >= period_end):
                continue
        expected = {'before_fen': item['performance_before_fen'], 'after_fen': item['performance_after_fen'],
                    'amount_fen': -item['adjustment_fen']}
        source_field = 'addon_refund_id' if item['refund_origin'] == 'ADDON' else 'refund_id'
        row, created = PayrollRefundPerformanceAdjustment.objects.get_or_create(
            **{source_field: item['refund_id']}, original_run_id=run_id, account_id=item['account_id'], defaults=expected)
        # Previously recorded effects are immutable. Later changes are
        # reflected by append-only revisions in the next payroll run.
        rows.append(row)
    return rows
