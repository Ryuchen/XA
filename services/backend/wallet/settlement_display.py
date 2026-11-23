"""Conservative presentation facts; these do not move or release funds."""
from collections import defaultdict

from django.conf import settings
from django.db.models import BooleanField, Case, Count, Exists, F, OuterRef, Q, Sum, Value, When, prefetch_related_objects
from rest_framework import serializers
from identifiers.serializers import EntityNumberModelSerializer, EntityNumberListSerializer
from identifiers.services import prime_entity_numbers

from club_accounts.models import LegacyAccountMap
from orders.direct_payment_models import DirectPaymentIntent
from orders.models import OrderProvider, OrderRefund
from .models import Transaction

REVIEW = 'REVIEW'
WAITING = 'WAITING'
SETTLED = 'SETTLED'


def legacy_customer_settlement_reason(order):
    """Explain why a completed historical order cannot mature automatically."""
    if (order and not order.is_gift_snapshot and order.status == 'COMPLETED'
            and order.payment_status in ('PAID', 'PARTIALLY_REFUNDED') and not order.settled_at
            and any(value is None for value in (order.service_clock_version, order.service_aftersale_hours,
                                                order.service_freeze_hours, order.service_deposit_percent))):
        return '订单结算时限快照不完整，请联系门店核对'
    if (order and order.status == 'COMPLETED' and order.payment_status == 'PAID'
            and not order.settled_at and not order.customer_account_id):
        return '历史订单缺少老板业务账号，请核对订单归属和付款记录后处理'
    return ''


def prepare_order_settlement_display(orders, *, points_prepared=False, order_list_only=False):
    """Batch only the non-sensitive evidence needed for display, in bounded chunks."""
    orders = list(orders)
    for start in range(0, len(orders), 200):
        chunk = orders[start:start + 200]
        if points_prepared:
            if any(not hasattr(order, '_display_points_evidence') for order in chunk):
                raise ValueError('积分展示凭证未完整准备')
        else:
            from orders.points_service_payment import prepare_points_display
            prepare_points_display(chunk)
        subsidies = defaultdict(dict)
        if getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False):
            from orders.models import OrderPlatformSubsidyShare
            for item in OrderPlatformSubsidyShare.objects.filter(subsidy__order_id__in=[order.pk for order in chunk]).select_related('subsidy', 'expense_transaction__wallet__user', 'income_transaction'):
                subsidies[item.subsidy.order_id][item.assignment_id] = item
        direct_ids = {order.pk for order in chunk if order._state.fields_cache.get('direct_payment') is not None}
        uncached_ids = {order.pk for order in chunk if 'direct_payment' not in order._state.fields_cache}
        if uncached_ids:
            direct_ids.update(DirectPaymentIntent.objects.filter(order_id__in=uncached_ids).values_list('order_id', flat=True))
        eligible_ids = {order.pk for order in chunk if order.pk not in direct_ids and order.payment_status == 'PAID'}
        # An order list's unsettled PENDING status returns before consulting
        # shares or identities. Report displays require the full data even for
        # partially settled orders, and therefore never use this shortcut.
        detailed_ids = {order.pk for order in chunk if order.pk in eligible_ids and
                        (not order_list_only or order.status != 'PENDING' or order.settled_at)}
        shares = defaultdict(list)
        entries = defaultdict(list)
        refund_ids = set()
        legacy_accounts = {}
        if eligible_ids:
            # OrderListSerializer already loads the complete refund collection.
            known_refunds = {order.pk: bool(order.refund_records.all()) for order in chunk
                             if order_list_only and order.pk in eligible_ids
                             and 'refund_records' in getattr(order, '_prefetched_objects_cache', {})}
            refund_ids = {pk for pk, has_refund in known_refunds.items() if has_refund}
            refund_ids.update(OrderRefund.objects.filter(order_id__in=eligible_ids - known_refunds.keys())
                              .values_list('order_id', flat=True))
            for row in OrderProvider.objects.filter(order_id__in=detailed_ids).only(
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
            detailed_orders = [order for order in chunk if order.pk in detailed_ids]
            expected_account_users = {order.customer_id for order in detailed_orders if order.customer_account_id}
            expected_account_users.update(row.provider_id for values in shares.values() for row in values
                                          if row.provider_account_id and row.provider_id)
            expected_account_users.update(order.provider_id for order in detailed_orders
                                          if order.provider_account_id and order.provider_id)
            expected_account_users.update(order.inviter_id for order in detailed_orders
                                          if order.inviter_account_id and order.inviter_id)
            if expected_account_users:
                legacy_accounts = dict(LegacyAccountMap.objects.filter(legacy_user_id__in=expected_account_users)
                                       .values_list('legacy_user_id', 'account_id'))
        for order in chunk:
            order._display_has_direct = order.pk in direct_ids
            order._display_shares = shares[order.pk]
            order._display_entries = entries[order.pk]
            order._display_has_refund = order.pk in refund_ids
            order._display_legacy_accounts = legacy_accounts
            order._display_subsidies = subsidies[order.pk]
            order._display_order_only = bool(order_list_only and order.pk in eligible_ids and order.pk not in detailed_ids)


class OrderSettlementListSerializer(EntityNumberListSerializer):
    def to_representation(self, data):
        rows = list(data.all() if hasattr(data, 'all') else data)
        prepare_order_settlement_display(rows)
        from orders.team_progress import prepare_team_progress
        prepare_team_progress(rows)
        return super().to_representation(rows)


class ReportSettlementListSerializer(EntityNumberListSerializer):
    def to_representation(self, data):
        rows = list(data.all() if hasattr(data, 'all') else data)
        for start in range(0, len(rows), 200):
            chunk = rows[start:start + 200]
            prefetch_related_objects(chunk, 'order__providers', 'order__refund_records', 'order__aftersales')
            orders = [report.order for report in chunk if report.order_id]
            prime_entity_numbers(orders)
            from orders.refunds import prepare_provider_refund_evidence
            account = getattr(self.context.get('request'), 'account', None)
            prepare_provider_refund_evidence(orders, account)
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
            prepare_order_settlement_display(orders)
        return super().to_representation(rows)


def order_settlement_status(order):
    if hasattr(order, '_display_has_direct'):
        if getattr(order, '_display_order_only', False) and (order.status != 'PENDING' or order.settled_at):
            prepare_order_settlement_display([order])
        return _order_settlement_status(order)
    prepare_order_settlement_display([order])
    try:
        return _order_settlement_status(order)
    finally:
        for name in ('_display_has_direct', '_display_shares', '_display_entries', '_display_has_refund', '_display_legacy_accounts', '_display_subsidies', '_display_points_evidence', '_display_order_only'):
            delattr(order, name)


def _order_settlement_status(order):
    from orders.points_service_payment import display_has_points
    from orders.points_checkout_test import ordinary_settlement_order
    if display_has_points(order) or ordinary_settlement_order(order):
        from orders.points_settlement_test import verified_order_settlement_display
        return verified_order_settlement_display(order)
    # Test-domain paid records are not evidence of live channel settlement.
    cache = order._state.fields_cache
    has_direct = (order._display_has_direct if hasattr(order, '_display_has_direct')
                  else cache['direct_payment'] is not None if 'direct_payment' in cache
                  else DirectPaymentIntent.objects.filter(order_id=order.pk).exists())
    if has_direct:
        return REVIEW
    if order._display_subsidies:
        from .reporting import subsidy_agreement_evidence_valid
        if not subsidy_agreement_evidence_valid(order):
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
            or (order.customer_id and payments[0]['wallet__user_id'] != order.customer_id)
            or (order.customer_id and order.customer_account_id and order._display_legacy_accounts.get(order.customer_id) != order.customer_account_id)
            or (order.customer_account_id and order.customer_account_id != (
                payments[0]['wallet__account_id'] or order._display_legacy_accounts.get(order.customer_id)))):
        return REVIEW
    referral = [entry for entry in order._display_entries if entry['tx_type'] == 'INCOME' and entry['is_referral']]
    if order.inviter_commission:
        if len(referral) != 1 or referral[0]['count'] != 1 or referral[0]['total'] != order.inviter_commission:
            return REVIEW
        entry = referral[0]
        if order.inviter_account_id:
            if order.inviter_account_id != (entry['wallet__account_id'] or order._display_legacy_accounts.get(entry['wallet__user_id'])):
                return REVIEW
        elif not order.inviter_id or entry['wallet__user_id'] != order.inviter_id:
            return REVIEW
    elif referral:
        return REVIEW
    rows = order._display_shares
    active = [row for row in rows if not row.released_at]
    if order.required_provider_count not in (1, 2) or (rows and len(active) != order.required_provider_count):
        return REVIEW
    rows = [row for row in rows if not row.released_at or row.settled_at]
    if rows:
        if sum(row.settlement_base for row in rows) != order.amount:
            return REVIEW
        shares = []
        for row in rows:
            if not row.settled_at:
                continue
            funded = order._display_subsidies.get(row.pk)
            extra = 0
            if funded:
                if not funded.paid_at:
                    return REVIEW
                extra = funded.amount
                if extra:
                    expense, income = funded.expense_transaction, funded.income_transaction
                    if (not expense or not income or expense.order_id != order.pk or income.order_id != order.pk
                            or expense.tx_type != 'SUBSIDY_EXPENSE' or expense.amount != -extra
                            or income.tx_type != 'INCOME' or income.amount != extra
                            or expense.status != 'SUCCESS' or income.status != 'SUCCESS'
                            or not expense.wallet.user or expense.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
                            or expense.balance_after != expense.balance_before - extra
                            or income.balance_after != income.balance_before + extra):
                        return REVIEW
            shares.append((row.provider_id, row.provider_account_id, row.provider_income + extra))
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
    if report.order_id:
        from django.conf import settings
        from orders.models import OrderOrdinaryRefundAddonSettlementEvidence
        if (getattr(settings, 'ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING', False)
                and OrderOrdinaryRefundAddonSettlementEvidence.objects.filter(settlement__order_id=report.order_id).exists()):
            from orders.negotiation_settlement import report_settlement_snapshot
            status = report_settlement_snapshot(report)
            return status[0] if status is not None else REVIEW
        from orders.points_service_payment import display_has_points
        from orders.points_checkout_test import ordinary_settlement_order
        if display_has_points(report.order) or ordinary_settlement_order(report.order):
            from orders.points_settlement_test import verified_person_settlement_display
            return verified_person_settlement_display(report)[0]
    from .reporting import report_has_valid_order_share
    if not report_has_valid_order_share(report):
        return REVIEW
    if report.order_id:
        from orders.negotiation_settlement import report_settlement_snapshot
        negotiated = report_settlement_snapshot(report)
        if negotiated is not None:
            return negotiated[0]
        from .reporting import report_order_share
        order = report.order
        if getattr(order, '_display_order_only', False):
            prepare_order_settlement_display([order])
        from orders.segment_financial_capture import transfer_income_pending
        if transfer_income_pending(order, report.provider_account_id):
            return REVIEW
        share = report_order_share(report)
        # A double order can remain open while this participant's own share
        # has already been credited. Verify that share and its actual ledger
        # before presenting it as settled; do not infer from an order status.
        if order.service_clock_version is not None and not order.settled_at and share and share.settled_at:
            if not hasattr(order, '_display_has_direct'):
                prepare_order_settlement_display([order])
            if (report.status != report.Status.APPROVED or report.payout_amount != share.provider_income
                    or order._display_has_direct or order._display_has_refund
                    or (share.provider_account_id and order._display_legacy_accounts.get(share.provider_id)
                        != share.provider_account_id)):
                return REVIEW
            credits = [entry for entry in order._display_entries if entry['tx_type'] == 'INCOME'
                       and entry['wallet__user_id'] == share.provider_id and not entry['is_referral']]
            from .reporting import report_subsidy_snapshot
            has_subsidy, subsidy_amount, subsidy_paid_at = report_subsidy_snapshot(report)
            if has_subsidy and (subsidy_amount is None or subsidy_paid_at is None):
                return REVIEW
            income_amount = subsidy_amount if has_subsidy else share.provider_income
            if income_amount == 0:
                return SETTLED if not credits else REVIEW
            if (len(credits) == 1 and credits[0]['count'] == 1 and credits[0]['bad'] == 0
                    and credits[0]['total'] == income_amount
                    and (not share.provider_account_id or credits[0]['wallet__account_id'] == share.provider_account_id)):
                return SETTLED
            return REVIEW
        status = order_settlement_status(report.order)
        if status != SETTLED:
            return status
        if report.status != report.Status.APPROVED or not report_has_valid_order_share(report):
            return REVIEW
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
