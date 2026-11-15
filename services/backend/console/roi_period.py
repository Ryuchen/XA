"""Actual customer payment and refund events for operating periods."""
from collections import Counter, defaultdict

from django.db.models import F, Q

from orders.models import Order, OrderAddonPayment, OrderAddonRefund, OrderPointsRedemption, OrderPointsRefund, OrderRefund
from wallet.models import Transaction


def points_refund_event_time(proof):
    """Use the returned coin credit, or the points ledger for a points-only refund."""
    if proof.coin_units:
        if not proof.coin_transaction_id:
            raise ValueError('积分退款缺少店币入账')
        return proof.coin_transaction.created_at
    if proof.points_hundredths:
        if not proof.points_ledger_id:
            raise ValueError('积分退款缺少积分流水')
        return proof.points_ledger.created_at
    raise ValueError('积分退款没有真实返还资产')


def ordinary_refund_event_time(refund):
    """Time of the verified customer credit; zero agreements have no credit."""
    if not refund.amount:
        return refund.created_at
    from .payroll_refund_performance import verify_refund_wallet_event
    return verify_refund_wallet_event(refund).created_at


def gift_refund_event_time(refund):
    if not refund.amount:
        return refund.created_at
    tx = refund.customer_transaction
    if (not tx or tx.order_id != refund.order_id or tx.wallet.account_id != refund.order.customer_account_id
            or tx.wallet.user_id != refund.order.customer_id or tx.tx_type != 'REFUND'
            or tx.status != 'SUCCESS' or tx.amount != refund.amount or tx.reversal_of_id or tx.reversals.exists()
            or tx.operator_id != refund.operator_id or tx.operator_account_id != refund.operator_account_id
            or tx.balance_after != tx.balance_before + tx.amount):
        raise ValueError('礼物退款钱包入账凭证待核对')
    return tx.created_at


def _valid_assignments(order, assignments):
    from orders.settlement import compute_provider_income
    return (bool(assignments) and sum(row.settlement_base for row in assignments) == order.amount
            and sum(row.provider_income for row in assignments) == order.provider_income
            and all(row.provider_account_id and row.commission_type in ('PERCENT', 'FIXED')
                    and row.provider_income == compute_provider_income(row.settlement_base, row.commission_type,
                                                                        row.commission_rate, row.commission_fixed)
                    for row in assignments))


def _verified_addon_costs(addons, start, end):
    from rest_framework.exceptions import ValidationError
    from engagement.funding_plan import FundingPlanError, RefundablePart, plan_refund
    from orders.addon_payment import verify_receipt

    result = {'provider': 0, 'gift_compensation': 0}
    try:
        for addon in addons:
            verify_receipt(addon)
            allocations = list(addon.allocations.order_by('pk'))
            returned = {row.pk: 0 for row in allocations}
            paid_at = addon.buyer_transaction.created_at
            if addon.paid_at > paid_at:
                return None
            if start <= paid_at < end:
                result['provider'] += addon.provider_income
                result['gift_compensation'] += addon.intent.gift + addon.intent.compensation
            for refund in addon.refund_records.select_related('proposal', 'buyer_transaction').order_by('created_at', 'pk'):
                if refund.created_at < paid_at:
                    return None
                tx = refund.buyer_transaction
                if (refund.proposal.payment_id != addon.pk or refund.proposal.amount != refund.amount
                        or tx.order_id is not None or tx.wallet_id != addon.buyer_transaction.wallet_id
                        or tx.tx_type != 'REFUND' or tx.status != 'SUCCESS' or tx.amount != refund.amount
                        or tx.operator_id != refund.legacy_operator_id or tx.operator_account_id != refund.operator_id
                        or tx.created_at < paid_at or tx.reversal_of_id or tx.reversals.exists()
                        or tx.balance_after != tx.balance_before + tx.amount):
                    return None
                refund_at = tx.created_at
                expected = plan_refund([RefundablePart(row.pk, row.kind, row.amount, returned[row.pk])
                                        for row in allocations], refund.amount)
                expected_plan = [{'allocation_id': row.pk, 'kind': row.kind, 'amount_raw': expected[row.pk]}
                                 for row in allocations if expected[row.pk]]
                if refund.proposal.source_plan != expected_plan:
                    return None
                for key, amount in expected.items():
                    returned[key] += amount
                if start <= refund_at < end:
                    result['provider'] -= (refund.addon_pending_deduction + refund.main_pending_deduction
                                           + refund.wallet_deduction + refund.deposit_deduction)
                    result['gift_compensation'] -= sum(item['amount_raw'] for item in expected_plan
                                                       if item['kind'] in ('GIFT', 'COMPENSATION'))
            if any(row.refunded_amount != returned[row.pk] for row in allocations):
                return None
    except (ValidationError, FundingPlanError):
        return None
    return result


def period_revenue(start, end):
    """Sum verified store-coin movements at their own event times.

    Original order amounts, points returned, and provider debits are not
    customer coin income. Add-ons have independent payment/refund receipts.
    """
    payment_kind = Q(tx_type='PAY', order__is_gift_snapshot=False) | Q(tx_type='GIFT', order__is_gift_snapshot=True)
    payments = list(Transaction.objects.filter(payment_kind, order__isnull=False, order__direct_payment__isnull=True,
        status='SUCCESS', amount__lt=0, created_at__gte=start, created_at__lt=end)
        .select_related('order', 'wallet'))
    from .payroll_refund_performance import refund_event_queryset
    refunds = list(refund_event_queryset(OrderRefund.objects.filter(order__direct_payment__isnull=True))
        .filter(payroll_refund_time__gte=start, payroll_refund_time__lt=end).select_related('order'))
    refund_transactions = list(Transaction.objects.filter(order__isnull=False, order__direct_payment__isnull=True,
        wallet__account_id=F('order__customer_account_id'), tx_type='REFUND', status='SUCCESS', amount__gt=0,
        created_at__gte=start, created_at__lt=end).values_list('order_id', flat=True))
    addons = list(OrderAddonPayment.objects.filter(intent__order__direct_payment__isnull=True,
        buyer_transaction__created_at__gte=start, buyer_transaction__created_at__lt=end).select_related('intent__order', 'buyer_transaction__wallet'))
    addon_refunds = list(OrderAddonRefund.objects.filter(order__direct_payment__isnull=True,
        buyer_transaction__created_at__gte=start, buyer_transaction__created_at__lt=end).select_related('payment__intent', 'payment__buyer_transaction__wallet', 'buyer_transaction__wallet'))
    addon_refund_receipts = list(OrderAddonRefund.objects.filter(order__direct_payment__isnull=True,
        buyer_transaction__created_at__gte=start, buyer_transaction__created_at__lt=end)
        .select_related('payment__intent', 'payment__buyer_transaction__wallet', 'buyer_transaction__wallet'))
    points_refunds = list(OrderPointsRefund.objects.filter(refund__order__direct_payment__isnull=True,
        points_ledger__created_at__gte=start, points_ledger__created_at__lt=end)
        .select_related('redemption__order', 'refund', 'points_ledger'))
    ids = ({row.order_id for row in payments} | {row.order_id for row in refunds}
           | set(refund_transactions) | {row.intent.order_id for row in addons}
           | {row.order_id for row in addon_refunds} | {row.order_id for row in addon_refund_receipts}
           | {row.refund.order_id for row in points_refunds})
    original = defaultdict(list)
    for row in Transaction.objects.filter(payment_kind, order_id__in=ids, status='SUCCESS', amount__lt=0).select_related('order', 'wallet'):
        original[row.order_id].append(row)
    expected, expected_period = defaultdict(Counter), defaultdict(Counter)
    for row in refund_event_queryset(OrderRefund.objects.filter(order_id__in=ids, amount__gt=0)).filter(payroll_refund_time__lt=end):
        expected[row.order_id][(row.amount, row.operator_id, row.operator_account_id)] += 1
        if row.payroll_refund_time >= start:
            expected_period[row.order_id][(row.amount, row.operator_id, row.operator_account_id)] += 1
    actual, actual_period = defaultdict(Counter), defaultdict(Counter)
    for row in Transaction.objects.filter(order_id__in=ids, tx_type='REFUND', status='SUCCESS', amount__gt=0,
                                          created_at__lt=end).select_related('wallet', 'order'):
        if (row.wallet.account_id == row.order.customer_account_id and row.wallet.user_id == row.order.customer_id
                and not row.reversal_of_id
                and row.balance_after == row.balance_before + row.amount and not row.reversals.exists()):
            actual[row.order_id][(row.amount, row.operator_id, row.operator_account_id)] += 1
            if row.created_at >= start:
                actual_period[row.order_id][(row.amount, row.operator_id, row.operator_account_id)] += 1
    invalid = set()
    orders = Order.objects.filter(pk__in=ids).in_bulk()
    for order_id in ids:
        rows = original[order_id]
        order = orders[order_id]
        payment_invalid = (len(rows) != (1 if order.amount else 0))
        if rows and not payment_invalid:
            tx = rows[0]
            payment_invalid = (tx.wallet.account_id != order.customer_account_id
                or tx.wallet.user_id != order.customer_id or tx.amount != -order.amount
                or tx.balance_after != tx.balance_before + tx.amount
                or tx.reversal_of_id or tx.reversals.exists())
        if (payment_invalid or expected[order_id] != actual[order_id]
                or expected_period[order_id] != actual_period[order_id]):
            invalid.add(order_id)
    # A refund period may not include the original add-on payment. Verify
    # that original receipt as well before recognizing its refund as revenue.
    related_addons = {row.pk: row for row in addons}
    related_addons.update({row.payment_id: row.payment for row in addon_refunds})
    for row in related_addons.values():
        tx = row.buyer_transaction
        if (row.paid_at > tx.created_at or tx.order_id is not None or tx.wallet.account_id != row.intent.buyer_id or tx.tx_type != 'PAY'
                or tx.status != 'SUCCESS' or tx.amount != -row.intent.amount
                or tx.balance_after != tx.balance_before + tx.amount or tx.reversal_of_id or tx.reversals.exists()):
            invalid.add(row.intent.order_id)
    for row in addon_refunds:
        tx = row.buyer_transaction
        if (row.payment.intent.order_id != row.order_id or row.payment.buyer_transaction.created_at > row.created_at or tx.order_id is not None
                or tx.wallet_id != row.payment.buyer_transaction.wallet_id or tx.tx_type != 'REFUND'
                or tx.status != 'SUCCESS' or tx.amount != row.amount
                or not (start <= tx.created_at < end)
                or tx.balance_after != tx.balance_before + tx.amount or tx.reversal_of_id or tx.reversals.exists()):
            invalid.add(row.order_id)
    points_orders = set(OrderPointsRedemption.objects.filter(order_id__in=ids).values_list('order_id', flat=True))
    refund_points = list(OrderPointsRefund.objects.filter(refund_id__in=[row.pk for row in refunds]))
    recorded_points_refunds = {row.refund_id for row in refund_points}
    invalid_points = {row.order_id for row in refunds
                      if row.order_id in points_orders and row.pk not in recorded_points_refunds}
    invalid_points.update(row.refund.order_id for row in refund_points if row.points_hundredths and not row.points_ledger_id)
    for row in points_refunds:
        order, ledger = row.redemption.order, row.points_ledger
        if (row.refund.order_id != order.pk or row.refund.amount != row.coin_units
                or row.equivalent_units < row.coin_units
                or (row.points_hundredths and (not ledger or ledger.order_id != order.pk
                    or ledger.account_id != order.customer_account_id or ledger.kind != 'ORDER_POINTS_RETURN'
                    or not (start <= ledger.created_at < end)
                    or ledger.event_key != f'order:{order.pk}:points-return:{row.refund_id}'
                    or ledger.amount != row.points_hundredths or ledger.operator_id != row.refund.operator_account_id))):
            invalid_points.add(order.pk)
    paid = sum(-row.amount for row in payments) + sum(row.intent.amount for row in addons)
    refunded = sum(row.amount for row in refunds) + sum(row.amount for row in addon_refunds)
    return {'paid_units': None if invalid else paid, 'refunded_units': None if invalid else refunded,
            'net_units': None if invalid else paid - refunded, 'order_ids': ids,
            'payment_order_ids': {row.order_id for row in payments},
            'refund_order_ids': {row.order_id for row in refunds} | {row.refund.order_id for row in points_refunds},
            'addon_order_ids': {row.intent.order_id for row in addons} | {row.order_id for row in addon_refunds},
            'invalid_order_ids': sorted(invalid),
            'refunded_points_hundredths': None if invalid_points else sum(row.points_hundredths for row in points_refunds),
            'refunded_points_value_units': None if invalid_points else sum(row.equivalent_units - row.coin_units for row in points_refunds),
            'invalid_points_order_ids': sorted(invalid_points)}


def ordinary_negotiation_cost(order, start, end):
    """Return period costs for a provable single ordinary negotiated refund.

    Payment establishes the original liabilities; the refund changes them at
    its own time. Already paid referral income is never clawed back.
    Other refund and handoff paths remain with their existing review handling.
    """
    from rest_framework.exceptions import ValidationError
    from django.core.exceptions import ValidationError as DjangoValidationError
    from stores.db import transaction
    from engagement.models import OrderFundingAllocation
    from orders.models import (NegotiationExecution, OrderAddonHandover, OrderOriginalShareTransfer,
                               OrderPlatformSubsidy, OrderPrestartHandoff)
    from orders.negotiation_execution import verify_execution
    from orders.referral_evidence import ReferralEvidenceError, paid_referral_credits
    from orders.refund_math import RefundCalculationError, wallet_source_refund

    addons = list(OrderAddonPayment.objects.filter(intent__order=order)
                  .select_related('intent', 'buyer_transaction__wallet'))
    if (order.is_gift_snapshot or not order.amount or OrderPointsRedemption.objects.filter(order=order).exists()
            or OrderPlatformSubsidy.objects.filter(order=order).exists()
            or NegotiationExecution.objects.filter(zero_order=order).exists()
            or OrderPrestartHandoff.objects.filter(order=order).exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
            or OrderAddonHandover.objects.filter(payment__in=addons, status='ACCEPTED').exists()):
        return None
    refunds = list(order.refund_records.order_by('created_at', 'pk'))
    if len(refunds) != 1 or not refunds[0].amount:
        return None
    refund = refunds[0]
    execution = (NegotiationExecution.objects.filter(refund=refund)
                 .select_related('proposal__case__order', 'refund', 'customer_transaction').first())
    payments = list(order.transactions.filter(tx_type='PAY', status='SUCCESS', amount__lt=0))
    assignments = list(order.providers.all())
    sources = list(OrderFundingAllocation.objects.filter(order=order).select_related('lot'))
    if (not execution or len(payments) != 1 or not _valid_assignments(order, assignments) or not sources
            or payments[0].created_at > refund.created_at
            or any(addon.buyer_transaction.created_at < payments[0].created_at for addon in addons)
            or sum(row.amount for row in sources) != order.amount):
        return None
    payment = payments[0]
    if (payment.amount != -order.amount or payment.wallet.account_id != order.customer_account_id
            or payment.wallet.user_id != order.customer_id or payment.reversal_of_id
            or payment.reversals.exists() or payment.balance_after != payment.balance_before + payment.amount):
        return None
    rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    if order.inviter_commission != order.amount * rate // 100:
        return None
    try:
        with transaction.atomic():
            from django.conf import settings
            if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                from console.payroll_event_barrier import lock_payment_refund_events
                lock_payment_refund_events()
            order = Order.objects.select_for_update().get(pk=order.pk)
            from orders.financial_lock import lock_order_money
            lock_order_money()
            refunds = list(order.refund_records.order_by('created_at', 'pk'))
            if len(refunds) != 1 or refunds[0].pk != refund.pk:
                return None
            refund = refunds[0]
            execution = (NegotiationExecution.objects.filter(refund=refund)
                         .select_related('proposal__case__order', 'refund', 'customer_transaction').first())
            if not execution:
                return None
            sources = list(OrderFundingAllocation.objects.filter(order=order).select_related('lot'))
            rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
            if not sources or sum(row.amount for row in sources) != order.amount or order.inviter_commission != order.amount * rate // 100:
                return None
            shares = verify_execution(execution)
            addon_costs = _verified_addon_costs(addons, start, end)
            if addon_costs is None:
                return None
            amounts = {kind: sum(row.amount for row in sources if row.kind == kind)
                       for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
            returned = wallet_source_refund(coin_units=refund.amount,
                paid_prepaid_units=amounts['PREPAID'], paid_gift_units=amounts['GIFT'],
                paid_compensation_units=amounts['COMPENSATION'])
            expected_returned = {'PREPAID': returned.prepaid_units, 'GIFT': returned.gift_units,
                                 'COMPENSATION': returned.compensation_units}
            if any(sum(row.refunded_amount for row in sources if row.kind == kind) != value
                   for kind, value in expected_returned.items()):
                return None
            credits = paid_referral_credits(order, list(order.transactions.filter(
                status='SUCCESS', tx_type__in=['INCOME', 'GIFT'], amount__gt=0,
                reversal_of__isnull=True).select_related('wallet')))
            if any(tx.reversals.exists() or tx.balance_after != tx.balance_before + tx.amount for tx in credits):
                return None
            refund_at = ordinary_refund_event_time(refund)
            before = [tx for tx in credits if tx.created_at <= refund_at]
            after = [tx for tx in credits if tx.created_at > refund_at]
            net_referral = (order.amount - refund.amount) * rate // 100
            if ((before and (after or sum(tx.amount for tx in before) != order.inviter_commission))
                    or (after and sum(tx.amount for tx in after) != net_referral)
                    or (order.settled_at and not credits and net_referral > 0)):
                return None
    except (ValidationError, DjangoValidationError, RefundCalculationError, ReferralEvidenceError):
        return None
    paid_in_period = start <= payments[0].created_at < end
    refund_in_period = start <= ordinary_refund_event_time(refund) < end
    return {
        'provider': (order.provider_income if paid_in_period else 0)
                    - (sum(row.burden for row in shares) if refund_in_period else 0)
                    + addon_costs['provider'],
        'referral': (order.inviter_commission if paid_in_period else 0)
                    - ((order.inviter_commission - net_referral) if refund_in_period and not before else 0),
        'gift_compensation': ((amounts['GIFT'] + amounts['COMPENSATION']) if paid_in_period else 0)
                    - ((returned.gift_units + returned.compensation_units) if refund_in_period else 0)
                    + addon_costs['gift_compensation'],
    }


def points_negotiation_cost(order, start, end):
    """Recognize a verified points-service negotiation at payment and refund."""
    from rest_framework.exceptions import ValidationError
    from django.core.exceptions import ValidationError as DjangoValidationError
    from stores.db import transaction
    from orders.models import (NegotiationExecution, OrderAddonHandover, OrderOriginalShareTransfer, OrderPrestartHandoff,
                               PointsNegotiationPosting)
    from orders.points_negotiation_execution import verify_posting_locked
    from orders.referral_evidence import ReferralEvidenceError, paid_referral_credits

    addons = list(OrderAddonPayment.objects.filter(intent__order=order)
                  .select_related('intent', 'buyer_transaction__wallet'))
    if (order.is_gift_snapshot or not order.amount
            or NegotiationExecution.objects.filter(zero_order=order).exists()
            or OrderPrestartHandoff.objects.filter(order=order).exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
            or OrderAddonHandover.objects.filter(payment__in=addons, status='ACCEPTED').exists()):
        return None
    refunds = list(order.refund_records.order_by('created_at', 'pk'))
    if len(refunds) != 1:
        return None
    refund = refunds[0]
    posting = (PointsNegotiationPosting.objects.filter(points_refund__refund=refund)
               .select_related('points_refund__redemption__payment', 'proposal').first())
    if not posting:
        return None
    proof = posting.points_refund
    payment = proof.redemption.payment
    if payment.created_at > refund.created_at or any(addon.buyer_transaction.created_at < payment.created_at for addon in addons):
        return None
    rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    original_referral = order.amount * rate // 100
    if order.inviter_commission != original_referral:
        return None
    try:
        with transaction.atomic():
            from contextlib import ExitStack
            from django.conf import settings
            if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                from console.payroll_event_barrier import lock_payment_refund_events
                lock_payment_refund_events()
            order = Order.objects.select_for_update().get(pk=order.pk)
            from orders.financial_lock import lock_order_money
            lock_order_money()
            from orders.points_business_access import case_refund_scope
            if order.refund_records.count() != 1:
                return None
            rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
            original_referral = order.amount * rate // 100
            if order.inviter_commission != original_referral:
                return None
            with ExitStack() as stack:
                stack.enter_context(case_refund_scope(posting.proposal.case_id))
                verified = verify_posting_locked(posting)
            if not isinstance(verified.participants, list):
                return None
            addon_costs = _verified_addon_costs(addons, start, end)
            if addon_costs is None:
                return None
            original_income = sum(row['income_at_entry'] for row in verified.participants)
            burden = sum(verified.shares.values_list('burden', flat=True))
            credits = paid_referral_credits(order, list(order.transactions.filter(
                status='SUCCESS', tx_type__in=['INCOME', 'GIFT'], amount__gt=0,
                reversal_of__isnull=True).select_related('wallet')))
            if any(tx.reversals.exists() or tx.balance_after != tx.balance_before + tx.amount for tx in credits):
                return None
            refund_at = points_refund_event_time(proof)
            before = [tx for tx in credits if tx.created_at <= refund_at]
            after = [tx for tx in credits if tx.created_at > refund_at]
            net_referral = (order.amount - refund.amount) * rate // 100
            if ((before and (after or sum(tx.amount for tx in before) != original_referral))
                    or (after and sum(tx.amount for tx in after) != net_referral)
                    or (order.settled_at and not credits and net_referral > 0)):
                return None
    except (ValidationError, DjangoValidationError, ReferralEvidenceError):
        return None
    paid_in_period = start <= payment.created_at < end
    refund_in_period = start <= points_refund_event_time(proof) < end
    original_gift = sum(order.funding_allocations.filter(kind__in=('GIFT', 'COMPENSATION'))
                        .values_list('amount', flat=True))
    return {
        'provider': ((original_income if paid_in_period else 0) - (burden if refund_in_period else 0)
                     + addon_costs['provider']),
        'referral': (original_referral if paid_in_period else 0)
                    - ((original_referral - net_referral) if refund_in_period and not before else 0),
        'gift_compensation': ((original_gift if paid_in_period else 0)
                    - ((proof.gift_units + proof.compensation_units) if refund_in_period else 0)
                    + addon_costs['gift_compensation']),
    }


def service_payment_cost(order, start, end):
    """Cost a main payment and any add-on events with intact snapshots.

    Add-on refunds may consume earnings from this main order or other add-ons;
    their full verified provider burden is recognized once at refund time.
    """
    from rest_framework.exceptions import ValidationError
    from django.core.exceptions import ValidationError as DjangoValidationError
    from django.core.exceptions import ObjectDoesNotExist
    from stores.db import transaction
    from engagement.funding_wallet import validate_order_funding_sources
    from engagement.models import OrderFundingAllocation
    from orders.models import (NegotiationExecution, OrderAddonHandover, OrderOriginalShareTransfer,
                               OrderPlatformSubsidy, OrderPrestartHandoff)
    from orders.points_funding_plan import PointsFundingPlanError, plan_verified_order
    from orders.referral_evidence import ReferralEvidenceError, paid_referral_credits
    from orders.settlement import SettlementError

    addons = list(OrderAddonPayment.objects.filter(intent__order=order)
                  .select_related('intent', 'buyer_transaction__wallet'))
    if (order.is_gift_snapshot or not order.amount or order.refund_records.exists()
            or OrderPlatformSubsidy.objects.filter(order=order).exists()
            or NegotiationExecution.objects.filter(zero_order=order).exists()
            or OrderPrestartHandoff.objects.filter(order=order).exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
            or OrderAddonHandover.objects.filter(payment__in=addons, status='ACCEPTED').exists()):
        return None
    payments = list(order.transactions.filter(tx_type='PAY', status='SUCCESS', amount__lt=0).select_related('wallet'))
    assignments = list(order.providers.all())
    sources = list(OrderFundingAllocation.objects.filter(order=order).select_related('lot'))
    if (len(payments) != 1 or not _valid_assignments(order, assignments) or not sources
            or sum(row.amount for row in sources) != order.amount or any(row.refunded_amount for row in sources)):
        return None
    payment = payments[0]
    rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    if (payment.amount != -order.amount or payment.wallet.account_id != order.customer_account_id
            or payment.wallet.user_id != order.customer_id or payment.reversal_of_id or payment.reversals.exists()
            or payment.balance_after != payment.balance_before + payment.amount
            or order.inviter_commission != order.amount * rate // 100
            or any(addon.buyer_transaction.created_at < payment.created_at for addon in addons)):
        return None
    partial_evidence = None
    try:
        with transaction.atomic():
            from django.conf import settings
            if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                from console.payroll_event_barrier import lock_payment_refund_events
                lock_payment_refund_events()
            order = Order.objects.select_for_update().get(pk=order.pk)
            from orders.financial_lock import lock_order_money
            lock_order_money()
            sources = list(OrderFundingAllocation.objects.filter(order=order).select_related('lot'))
            if (order.refund_records.exists() or not sources or sum(row.amount for row in sources) != order.amount
                    or any(row.refunded_amount for row in sources)):
                return None
            addons = list(OrderAddonPayment.objects.filter(intent__order=order)
                          .select_related('intent', 'buyer_transaction__wallet'))
            validate_order_funding_sources(order, sources)
            points = OrderPointsRedemption.objects.filter(order=order).exists()
            plan = plan_verified_order(order.pk, allow_finalized=True) if points else None
            main_provider = plan.provider_income_units if plan else order.provider_income
            main_referral = plan.inviter_income_units if plan else order.inviter_commission
            credits = paid_referral_credits(order, list(order.transactions.filter(
                status='SUCCESS', tx_type__in=['INCOME', 'GIFT'], amount__gt=0,
                reversal_of__isnull=True).select_related('wallet')))
            if ((order.settled_at and main_referral > 0 and not credits)
                    or (credits and (sum(row.amount for row in credits) != main_referral
                        or any(row.reversals.exists() or row.balance_after != row.balance_before + row.amount
                               for row in credits)))):
                return None
            addon_costs = _verified_addon_costs(addons, start, end)
            if addon_costs is None:
                return None
            if not points and not order.settled_at and len(addons) >= 2:
                from orders.models import OrderPointsSettlement
                has_cross = any(refund.earning_deductions.exclude(payment_id=addon.pk).exists()
                                for addon in addons for refund in addon.refund_records.all())
                if has_cross and OrderPointsSettlement.objects.filter(order=order, persons__isnull=False).exists():
                    from .ordinary_addon_partial_roi import evidence
                    partial_evidence = evidence(order, addons, sources, start, end)
    except (ValidationError, DjangoValidationError, PointsFundingPlanError, ReferralEvidenceError,
            SettlementError, ObjectDoesNotExist, ValueError, KeyError, TypeError):
        return None
    paid_in_period = start <= payment.created_at < end
    result = {'provider': main_provider if paid_in_period else 0,
              'referral': main_referral if paid_in_period else 0,
              'gift_compensation': sum(row.amount for row in sources if row.kind in ('GIFT', 'COMPENSATION'))
                                   if paid_in_period else 0}
    result['provider'] += addon_costs['provider']
    result['gift_compensation'] += addon_costs['gift_compensation']
    if partial_evidence is not None:
        result['partial_settlement'] = partial_evidence
    return result
