"""Customer ranking based on paid order and add-on original-price facts."""
from django.db.models import Q, Sum

from club_accounts.models import ClubAccount, LegacyAccountMap
from .models import Order, OrderAddonPayment, OrderAddonRefund, OrderRefund


def customer_totals(*, start, now):
    """Retain the existing completion-period basis, including independent add-ons.

    A full main-order refund clears its original price, but not an add-on.
    Point refunds use their original-order equivalent, not returned coins alone.
    """
    orders = list(Order.objects.filter(
        Q(status=Order.Status.COMPLETED)
        | Q(status=Order.Status.CANCELLED, payment_status=Order.PaymentStatus.REFUNDED),
        payment_status__in=('PAID', 'PARTIALLY_REFUNDED', 'REFUNDED'),
        completed_at__gte=start, completed_at__lte=now,
    ).values('id', 'customer_account_id', 'customer_id', 'original_amount', 'amount', 'payment_status'))
    order_ids = [row['id'] for row in orders]
    refunds = {}
    for row in OrderRefund.objects.filter(order_id__in=order_ids).values(
            'order_id', 'amount', 'points_refund__equivalent_units'):
        value = row['points_refund__equivalent_units']
        refunds[row['order_id']] = refunds.get(row['order_id'], 0) + (row['amount'] if value is None else value)
    addon_refunds = dict(OrderAddonRefund.objects.filter(order_id__in=order_ids)
                         .values('payment_id').annotate(total=Sum('amount')).values_list('payment_id', 'total'))
    addons = {}
    for row in OrderAddonPayment.objects.filter(intent__order_id__in=order_ids).values('id', 'intent__order_id', 'intent__amount'):
        amount, returned = row['intent__amount'], addon_refunds.get(row['id'], 0)
        order_id = row['intent__order_id']
        subtotal, valid = addons.get(order_id, (0, True))
        addons[order_id] = (subtotal + max(amount - returned, 0), valid and returned <= amount)
    legacy_ids = {row['customer_id'] for row in orders if not row['customer_account_id'] and row['customer_id']}
    mappings = dict(LegacyAccountMap.objects.filter(legacy_user_id__in=legacy_ids)
                    .values_list('legacy_user_id', 'account_id'))
    totals = {}
    for row in orders:
        account_id = row['customer_account_id'] or mappings.get(row['customer_id'])
        key = ('account', account_id) if account_id else ('user', row['customer_id'])
        if key[1] is None:
            continue
        entry = totals.setdefault(key, {'account_id': account_id, 'user_id': row['customer_id'],
                                        'total': 0, 'order_count': 0, 'complete': True})
        entry['user_id'] = entry['user_id'] or row['customer_id']
        entry['order_count'] += 1
        original, paid = row['original_amount'], row['amount']
        returned = refunds.get(row['id'], 0)
        addon_total, addons_valid = addons.get(row['id'], (0, True))
        # A fully discounted order can be cancelled with a real zero-amount
        # refund. Its original price is still fully reversed in the ranking.
        zero_payment_refund = (row['payment_status'] == 'REFUNDED' and paid == 0
                               and row['id'] in refunds and returned == 0)
        # A missing or inconsistent original amount must not become a fake zero.
        if (original < paid or (paid and not original) or not addons_valid or returned > original
                or (row['payment_status'] in ('PARTIALLY_REFUNDED', 'REFUNDED')
                    and not returned and not zero_payment_refund)):
            entry['complete'] = False
            continue
        main_net = 0 if row['payment_status'] == 'REFUNDED' else max(original - returned, 0)
        entry['total'] += main_net + addon_total
    account_ids = [key[1] for key in totals if key[0] == 'account']
    accounts = {row.pk: row for row in ClubAccount.objects.filter(pk__in=account_ids)}
    legacy_by_account = dict(LegacyAccountMap.objects.filter(account_id__in=account_ids)
                             .values_list('account_id', 'legacy_user_id'))
    return list(totals.values()), accounts, legacy_by_account
