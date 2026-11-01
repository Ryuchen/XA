"""Conservative presentation facts; these do not move or release funds."""
from collections import defaultdict

from django.db.models import BooleanField, Case, Count, Exists, F, OuterRef, Q, Sum, Value, When, prefetch_related_objects
from rest_framework import serializers

from club_accounts.models import LegacyAccountMap
from orders.direct_payment_models import DirectPaymentIntent
from orders.models import OrderProvider, OrderRefund
from .models import Transaction

REVIEW = 'REVIEW'
WAITING = 'WAITING'
SETTLED = 'SETTLED'


def legacy_customer_settlement_reason(order):
    """Explain why a completed historical order cannot mature automatically."""
    if (order and order.status == 'COMPLETED' and order.payment_status == 'PAID'
            and not order.settled_at and not order.customer_account_id):
        return '历史订单缺少老板业务账号，请核对订单归属和付款记录后处理'
    return ''


def prepare_order_settlement_display(orders):
    """Batch only the non-sensitive evidence needed for display, in bounded chunks."""
    orders = list(orders)
    for start in range(0, len(orders), 200):
        chunk = orders[start:start + 200]
        ids = {order.pk for order in chunk}
        direct_ids = set(DirectPaymentIntent.objects.filter(order_id__in=ids).values_list('order_id', flat=True))
        eligible_ids = {order.pk for order in chunk if order.pk not in direct_ids and order.payment_status == 'PAID'}
        shares = defaultdict(list)
        entries = defaultdict(list)
        refund_ids = set()
        legacy_accounts = {}
        if eligible_ids:
            refund_ids = set(OrderRefund.objects.filter(order_id__in=eligible_ids).values_list('order_id', flat=True))
            for row in OrderProvider.objects.filter(order_id__in=eligible_ids).only(
                    'id', 'order_id', 'provider_id', 'provider_account_id', 'settlement_base',
                    'provider_income', 'settled_at', 'released_at'):
                shares[row.order_id].append(row)
            reversals = Transaction.objects.filter(reversal_of_id=OuterRef('pk'))
            bad = (Q(reversal_of__isnull=False) | Q(has_reversal=True) | Q(tx_type='REFUND')
                   | Q(tx_type='PAY', amount__gt=0) | Q(tx_type='INCOME', amount__lte=0)
                   | (Q(tx_type__in=['PAY', 'INCOME']) &
                      (~Q(status='SUCCESS') | ~Q(balance_after=F('balance_before') + F('amount')))))
            for row in Transaction.objects.filter(order_id__in=eligible_ids).annotate(
                    has_reversal=Exists(reversals),
                    is_referral=Case(When(Q(remark__startswith='推荐分佣：') | Q(remark__startswith='礼物推荐分佣：'), then=Value(True)),
                                     default=Value(False), output_field=BooleanField())).values('order_id', 'wallet__user_id',
                    'wallet__account_id', 'tx_type', 'is_referral').annotate(total=Sum('amount'), count=Count('id'),
                                                               bad=Count('id', filter=bad)):
                entries[row['order_id']].append(row)
            expected_account_users = {order.customer_id for order in chunk if order.customer_account_id}
            expected_account_users.update(row.provider_id for values in shares.values() for row in values
                                          if row.provider_account_id and row.provider_id)
            expected_account_users.update(order.provider_id for order in chunk
                                          if order.provider_account_id and order.provider_id)
            if expected_account_users:
                legacy_accounts = dict(LegacyAccountMap.objects.filter(legacy_user_id__in=expected_account_users)
                                       .values_list('legacy_user_id', 'account_id'))
        for order in chunk:
            order._display_has_direct = order.pk in direct_ids
            order._display_shares = shares[order.pk]
            order._display_entries = entries[order.pk]
            order._display_has_refund = order.pk in refund_ids
            order._display_legacy_accounts = legacy_accounts


class OrderSettlementListSerializer(serializers.ListSerializer):
    def to_representation(self, data):
        rows = list(data.all() if hasattr(data, 'all') else data)
        prepare_order_settlement_display(rows)
        from orders.team_progress import prepare_team_progress
        prepare_team_progress(rows)
        return super().to_representation(rows)


class ReportSettlementListSerializer(serializers.ListSerializer):
    def to_representation(self, data):
        rows = list(data.all() if hasattr(data, 'all') else data)
        for start in range(0, len(rows), 200):
            chunk = rows[start:start + 200]
            prefetch_related_objects(chunk, 'order__providers', 'order__refund_records', 'order__aftersales')
            from orders.refunds import prepare_provider_refund_evidence
            account = getattr(self.context.get('request'), 'account', None)
            prepare_provider_refund_evidence((report.order for report in chunk if report.order_id), account)
            identity_users = {report.provider_id for report in chunk if report.provider_account_id and report.provider_id}
            identity_map = dict(LegacyAccountMap.objects.filter(legacy_user_id__in=identity_users)
                                .values_list('legacy_user_id', 'account_id')) if identity_users else {}
            for report in chunk:
                report._display_report_identity = (not report.provider_account_id
                    or identity_map.get(report.provider_id) == report.provider_account_id)
            manual = [report for report in chunk if not report.order_id and report.transaction_id]
            prefetch_related_objects(manual, 'transaction__wallet')
            reversed_ids = set(Transaction.objects.filter(
                reversal_of_id__in={report.transaction_id for report in manual}).values_list('reversal_of_id', flat=True)) if manual else set()
            for report in manual:
                report._display_manual_has_reversal = report.transaction_id in reversed_ids
            missing_users = {report.transaction.wallet.user_id for report in manual
                             if not report.transaction.wallet.account_id}
            accounts = dict(LegacyAccountMap.objects.filter(legacy_user_id__in=missing_users)
                            .values_list('legacy_user_id', 'account_id')) if missing_users else {}
            for report in manual:
                wallet = report.transaction.wallet
                report._display_wallet_account = wallet.account_id or accounts.get(wallet.user_id)
            prepare_order_settlement_display(report.order for report in chunk if report.order_id)
        return super().to_representation(rows)


def order_settlement_status(order):
    if hasattr(order, '_display_has_direct'):
        return _order_settlement_status(order)
    prepare_order_settlement_display([order])
    try:
        return _order_settlement_status(order)
    finally:
        for name in ('_display_has_direct', '_display_shares', '_display_entries', '_display_has_refund', '_display_legacy_accounts'):
            delattr(order, name)


def _order_settlement_status(order):
    # Test-domain paid records are not evidence of live channel settlement.
    cache = order._state.fields_cache
    has_direct = (order._display_has_direct if hasattr(order, '_display_has_direct')
                  else cache['direct_payment'] is not None if 'direct_payment' in cache
                  else DirectPaymentIntent.objects.filter(order_id=order.pk).exists())
    if has_direct:
        return REVIEW
    if order.payment_status != 'PAID':
        return REVIEW
    if order._display_has_refund or any(entry['bad'] for entry in order._display_entries):
        return REVIEW
    if not order.settled_at:
        if legacy_customer_settlement_reason(order):
            return REVIEW
        return WAITING
    payments = [entry for entry in order._display_entries if entry['tx_type'] == 'PAY']
    if (len(payments) != 1 or payments[0]['count'] != 1 or payments[0]['total'] != -order.amount
            or payments[0]['wallet__user_id'] != order.customer_id
            or (order.customer_account_id and order._display_legacy_accounts.get(order.customer_id) != order.customer_account_id)
            or (order.customer_account_id and order.customer_account_id != (
                payments[0]['wallet__account_id'] or order._display_legacy_accounts.get(order.customer_id)))):
        return REVIEW
    rows = order._display_shares
    active = [row for row in rows if not row.released_at]
    if order.required_provider_count not in (1, 2) or (rows and len(active) != order.required_provider_count):
        return REVIEW
    rows = [row for row in rows if not row.released_at or row.settled_at]
    if rows:
        if sum(row.settlement_base for row in rows) != order.amount:
            return REVIEW
        shares = [(row.provider_id, row.provider_account_id, row.provider_income) for row in rows if row.settled_at]
        if len(shares) != len(rows):
            return REVIEW
    elif order.provider_id and order.required_provider_count == 1:
        shares = [(order.provider_id, order.provider_account_id, order.provider_income)]
    else:
        return REVIEW
    for user_id, account_id, amount in shares:
        if not user_id:
            return REVIEW
        if account_id and order._display_legacy_accounts.get(user_id) != account_id:
            return REVIEW
        credits = [entry for entry in order._display_entries
                   if entry['tx_type'] == 'INCOME' and entry['wallet__user_id'] == user_id and not entry['is_referral']]
        if amount == 0:
            if credits:
                return REVIEW
        elif (len(credits) != 1 or credits[0]['total'] != amount
              or (account_id and account_id != (credits[0]['wallet__account_id']
                                                or order._display_legacy_accounts.get(user_id)))):
            return REVIEW
    return SETTLED


def report_settlement_status(report):
    from .reporting import report_has_valid_order_share
    if not report_has_valid_order_share(report):
        return REVIEW
    if report.order_id:
        status = order_settlement_status(report.order)
        if status != SETTLED:
            return status
        from .reporting import report_order_share
        if report.status != report.Status.APPROVED or not report_has_valid_order_share(report):
            return REVIEW
        share = report_order_share(report)
        if share:
            # A transferred-out participant can retain a settled share. The
            # order-level evidence check already verifies that share and its
            # corresponding credit; release alone does not undo settlement.
            if not share.settled_at or report.payout_amount != share.provider_income:
                return REVIEW
        elif report.payout_amount != report.order.provider_income:
            return REVIEW
        return SETTLED
    if report.status != report.Status.APPROVED:
        return WAITING
    if not report.transaction_id:
        return REVIEW
    tx = report.transaction
    has_reversal = (report._display_manual_has_reversal if hasattr(report, '_display_manual_has_reversal')
                    else tx.reversals.exists())
    wallet_account = (report._display_wallet_account if hasattr(report, '_display_wallet_account')
                      else tx.wallet.account_id or LegacyAccountMap.objects.filter(
                          legacy_user_id=tx.wallet.user_id).values_list('account_id', flat=True).first())
    if (tx.order_id is not None or tx.status != Transaction.Status.SUCCESS or tx.reversal_of_id
            or tx.tx_type != Transaction.TxType.INCOME or tx.wallet.user_id != report.provider_id
            or (report.provider_account_id and report.provider_account_id != wallet_account)
            or tx.amount != report.payout_amount or tx.balance_after != tx.balance_before + tx.amount
            or has_reversal):
        return REVIEW
    return SETTLED
