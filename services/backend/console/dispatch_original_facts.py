"""Read-only evidence for staff-facing original-price dispatch metrics.

This module deliberately does not choose a date or resolve staff attribution:
the source events are returned separately for the caller's reporting policy.
All amounts are internal wallet units; None means the fact needs review.
"""
from dataclasses import dataclass
from datetime import datetime

from django.db import DatabaseError
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError

from wallet.models import Transaction
from .payroll_models import SalaryActivity


@dataclass(frozen=True)
class OriginalNet:
    original_units: int
    refunded_equivalent_units: int
    net_units: int
    paid_at: datetime


def attribution_sources(order, *, as_of):
    """Return explicit payroll assignment and genuine staff logs independently.

    No logged creator is assumed to be the dispatch owner: verified payroll
    attribution and actual CREATE/ASSIGN operators may differ.
    """
    if not isinstance(as_of, datetime) or as_of.utcoffset() is None or not order.pk:
        return None
    rows = SalaryActivity.objects.filter(order=order, kind='DISPATCH', created_at__lte=as_of).select_related('account').order_by('pk')
    activities = []
    for row in rows:
        source_key = row.source_key
        if row.is_void:
            snapshot = row.source_snapshot if isinstance(row.source_snapshot, dict) else {}
            try:
                voided = parse_datetime(snapshot.get('voided_at', '')) if isinstance(snapshot.get('voided_at'), str) else None
            except ValueError:
                return None
            source_key = snapshot.get('voided_source_key')
            if not voided or voided.utcoffset() is None or voided < row.created_at or source_key != f'ORDER:{order.pk}':
                return None
            if voided <= as_of:
                continue
        activities.append((row, source_key))
    if len(activities) > 1:
        return None
    assignment, source_key = activities[0] if activities else (None, None)
    if assignment and (assignment.account.account_type != 'STAFF' or source_key != f'ORDER:{order.pk}'):
        return None
    logs = list(order.status_logs.filter(action__in=('CREATE', 'ASSIGN'),
        operator_account__account_type='STAFF', created_at__lte=as_of)
        .order_by('created_at', 'pk').values('id', 'action', 'operator_account_id', 'created_at', 'reason'))
    return {'payroll_attribution': ({'activity_id': assignment.pk, 'account_id': assignment.account_id,
                                     'recorded_at': assignment.created_at,
                                     'performed_at': assignment.performed_at} if assignment else None),
            'staff_events': logs}


def main_order_original_net(order, *, as_of):
    """Prove original price less equivalent refunds; full refund clears price."""
    from orders.grab_refunded_facts import verified_coin_refund
    from orders.grab_points_refunded_facts import points_refund_fact
    from orders.models import OrderPointsRedemption

    if (not isinstance(as_of, datetime) or as_of.utcoffset() is None or not order.pk
            or order.is_gift_snapshot or order.payment_status not in ('PAID', 'PARTIALLY_REFUNDED', 'REFUNDED')
            or type(order.original_amount) is not int or order.original_amount <= 0
            or order.original_amount < order.amount):
        return None
    try:
        payments = list(Transaction.objects.filter(order_id=order.pk, tx_type='PAY', status='SUCCESS',
                                                    amount__lte=0).select_related('wallet')[:2])
        if len(payments) != 1:
            return None
        tx = payments[0]
        if (tx.amount != -order.amount or tx.wallet.account_id != order.customer_account_id
                or tx.wallet.user_id != order.customer_id or tx.created_at > as_of or tx.reversal_of_id
                or tx.reversals.exists() or tx.balance_after != tx.balance_before + tx.amount):
            return None
        receipt = OrderPointsRedemption.objects.filter(order=order).select_related('payment', 'ledger').first()
        if receipt:
            ledger = receipt.ledger
            if (receipt.payment_id != tx.pk or receipt.paid_units != order.amount
                    or receipt.deal_units != receipt.paid_units + receipt.discount_units
                    or not ledger or ledger.order_id != order.pk or ledger.account_id != order.customer_account_id
                    or ledger.kind != 'ORDER_POINTS_REDEEM' or ledger.amount != -receipt.points_hundredths
                    or ledger.event_key != f'order:{order.pk}:points-redemption' or ledger.created_at > as_of):
                return None
        refunds_exist = order.refund_records.exists()
        if order.payment_status == 'PAID':
            if refunds_exist:
                return None
            return OriginalNet(order.original_amount, 0, order.original_amount, tx.created_at)
        if not refunds_exist:
            return None
        fact = points_refund_fact(order, as_of=as_of) if receipt else verified_coin_refund(order, as_of=as_of)
        if fact is None or fact.refunded_units > order.original_amount:
            return None
        net = 0 if fact.is_full else order.original_amount - fact.refunded_units
        return OriginalNet(order.original_amount, fact.refunded_units, net, tx.created_at)
    except (DatabaseError, TypeError, ValueError):
        return None


def addon_original_net(payment, *, as_of):
    """Prove an add-on's paid original price and actual returned coins."""
    from orders.addon_payment import verify_receipt

    if not isinstance(as_of, datetime) or as_of.utcoffset() is None or not payment.pk:
        return None
    try:
        verify_receipt(payment)
        intent, tx = payment.intent, payment.buyer_transaction
        if (type(intent.amount) is not int or intent.amount <= 0 or tx.created_at > as_of
                or payment.paid_at > as_of or payment.paid_at > tx.created_at):
            return None
        refunds = list(payment.refund_records.select_related('buyer_transaction').order_by('created_at', 'pk'))
        if any(row.created_at > as_of or row.buyer_transaction.created_at > as_of
               or row.created_at < tx.created_at or row.buyer_transaction.reversal_of_id
               or row.buyer_transaction.reversals.exists() for row in refunds):
            return None
        returned = sum(row.amount for row in refunds)
        if returned > intent.amount:
            return None
        return OriginalNet(intent.amount, returned, intent.amount - returned, payment.paid_at)
    except (DatabaseError, ValidationError, TypeError, ValueError, KeyError):
        return None


def today_dispatch_totals(*, as_of):
    """Staff dispatch day for main orders; actual payment day for add-ons.

    A source with conflicting attribution or unverifiable money makes each
    affected account's metric unknown rather than silently reporting zero.
    """
    from collections import defaultdict
    from django.utils import timezone
    from orders.models import Order, OrderAddonPayment, OrderStatusLog

    if not isinstance(as_of, datetime) or as_of.utcoffset() is None:
        raise ValueError('as_of must be timezone-aware')
    start = timezone.localtime(as_of).replace(hour=0, minute=0, second=0, microsecond=0)
    log_order_ids = set(OrderStatusLog.objects.filter(action__in=('CREATE', 'ASSIGN'),
        operator_account__account_type='STAFF', created_at__gte=start, created_at__lte=as_of)
        .values_list('order_id', flat=True))
    addons = list(OrderAddonPayment.objects.filter(paid_at__gte=start,
        paid_at__lte=as_of).select_related('intent__order', 'buyer_transaction__wallet'))
    order_ids = log_order_ids | {row.intent.order_id for row in addons}
    orders = {row.pk: row for row in Order.objects.filter(pk__in=order_ids, is_gift_snapshot=False,
                                                           direct_payment__isnull=True)}
    addons_by_order = defaultdict(list)
    for addon in addons:
        if addon.intent.order_id in orders:
            addons_by_order[addon.intent.order_id].append(addon)
    totals = defaultdict(int)
    reviews = defaultdict(set)
    for order in orders.values():
        facts = attribution_sources(order, as_of=as_of)
        candidates = set(SalaryActivity.objects.filter(order=order, kind='DISPATCH', created_at__lte=as_of)
                         .values_list('account_id', flat=True))
        if facts is None:
            candidates.update(order.status_logs.filter(action__in=('CREATE', 'ASSIGN'),
                operator_account__account_type='STAFF', created_at__lte=as_of)
                .values_list('operator_account_id', flat=True))
            for account_id in candidates:
                reviews[account_id].add(order.pk)
            continue
        events = facts['staff_events']
        proven = [row for row in events if (row['action'] == 'CREATE' and row['reason'] == '客服代派单')
                  or (row['action'] == 'ASSIGN' and row['reason'] in ('客服指派陪玩', '客服指派双陪'))]
        explicit = facts['payroll_attribution']
        candidates.update(row['operator_account_id'] for row in events)
        if explicit:
            candidates.add(explicit['account_id'])
            matching = [row for row in proven if row['operator_account_id'] == explicit['account_id']
                        and row['created_at'] == explicit['performed_at']]
            primary = matching[0] if len(matching) == 1 else None
        else:
            primary = proven[0] if proven and len({row['operator_account_id'] for row in proven}) == 1 else None
        # Only actual staff work and an unambiguous verified attribution can
        # establish a date and owner; automatic or unrelated logs cannot.
        if not primary:
            for account_id in candidates:
                reviews[account_id].add(order.pk)
            continue
        owner_id = primary['operator_account_id']
        if primary['created_at'] >= start:
            if order.payment_status == 'UNPAID':
                if Transaction.objects.filter(order=order, tx_type='PAY', status='SUCCESS', amount__lt=0).exists():
                    reviews[owner_id].add(order.pk)
            else:
                fact = main_order_original_net(order, as_of=as_of)
                if fact is None:
                    reviews[owner_id].add(order.pk)
                else:
                    totals[owner_id] += fact.net_units
        for addon in addons_by_order[order.pk]:
            fact = addon_original_net(addon, as_of=as_of)
            if fact is None:
                reviews[owner_id].add(order.pk)
            else:
                totals[owner_id] += fact.net_units
    return {account_id: {'amount': None if reviews[account_id] else totals[account_id],
                         'review_count': len(reviews[account_id])}
            for account_id in set(totals) | set(reviews)}
