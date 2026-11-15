"""Evidence for when a provider last finished all order-related business.

The seven-day refund clock cannot start from a preview request: doing so
would ignore business already finished before the request.  Conversely,
missing historic terminal timestamps must not be guessed.
"""
from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime


@dataclass(frozen=True)
class ClearanceEvidence:
    last_closed_at: object = None
    needs_review: bool = False
    has_business: bool = False


def verified_segment_credit(order, account_id):
    # Subsidy verification takes an order row lock; refund eligibility already
    # holds the provider and wallet locks. Keep its existing subsidy evidence
    # path rather than acquiring those locks in the opposite order.
    from orders.models import OrderSegmentSettlementManifest
    if OrderSegmentSettlementManifest.objects.filter(
            order=order, order__platform_subsidy__isnull=False).exists():
        return False, None
    from orders.segment_settlement import verified_person_credit
    return verified_person_credit(order, account_id)


def _verified_refund_execution(refund):
    """Read the accepted agreement and posted customer refund without row locks."""
    from copy import copy
    from rest_framework.exceptions import ValidationError
    from orders.models import NegotiationExecution
    from orders.negotiation_execution import _allocation_hash
    from orders.negotiation_proposals import _decisions, _verified
    from orders.refunds import request_fingerprint

    execution = NegotiationExecution.objects.filter(refund=refund).select_related(
        'proposal__case__order', 'customer_transaction__wallet').first()
    if not execution or not execution.customer_transaction_id:
        return None
    proposal, tx = execution.proposal, execution.customer_transaction
    case = proposal.case
    episode = copy(case)
    episode.created_at, episode.client_request_id = proposal.case_opened_at, proposal.case_request_id
    try:
        _verified(episode, proposal)
        _decisions(episode, proposal)
    except (ValidationError, ValueError, TypeError):
        return None
    if (proposal.status != 'ACCEPTED' or execution.terms_hash != proposal.terms_hash
            or execution.accepted_at != proposal.accepted_at or case.order_id != refund.order_id
            or refund.operator_account_id != execution.operator_id or refund.amount != proposal.refund_units
            or refund.reason != proposal.explanation or not refund.client_request_id
            or refund.request_fingerprint != request_fingerprint(refund.amount, refund.reason,
                {'proposal_id':proposal.pk, 'terms_hash':proposal.terms_hash})
            or not isinstance(refund.benefit_effects, dict)
            or refund.benefit_effects.get('negotiation_execution_version') != 1
            or not isinstance(refund.negotiated_allocation, list)
            or execution.allocation_hash != _allocation_hash(refund.negotiated_allocation)
            or tx.order_id != refund.order_id or tx.tx_type != 'REFUND' or tx.status != 'SUCCESS'
            or tx.amount != refund.amount or tx.wallet.account_id != case.account_id
            or tx.wallet.user_id != case.order.customer_id or tx.operator_account_id != execution.operator_id
            or tx.operator_id != refund.operator_id or tx.reversal_of_id or tx.reversals.exists()
            or tx.balance_after != tx.balance_before + tx.amount):
        return None
    return execution


def _verified_unaffected_refund(refund, account_id):
    """Exclude a teammate only when the accepted agreement proves its scope."""
    execution = _verified_refund_execution(refund)
    if execution:
        return account_id not in execution.proposal.affected_accounts
    from .deposit_points_clearance import verified_unaffected_points_refund
    return verified_unaffected_points_refund(refund, account_id)


def _verified_ordinary_negotiated_person(order, assignment):
    """Read a directly agreed, settled refund share without acquiring locks."""
    from rest_framework.exceptions import ValidationError
    from orders.models import NegotiationExecutionShare, OrderOriginalShareTransfer
    from orders.addon_main_deduction import deductions_at
    from orders.negotiation_settlement import negotiated_aftersale_start
    from orders.services import missing_report_evidence
    from wallet.models import ProviderReport

    refunds = [row for row in order.refund_records.all()
               if isinstance(row.benefit_effects, dict)
               and row.benefit_effects.get('negotiation_execution_version') == 1]
    if not refunds:
        return False, None
    if all(_verified_unaffected_refund(refund, assignment.provider_account_id)
           for refund in order.refund_records.all()):
        return False, None
    if (len(refunds) != 1 or order.refund_records.count() != 1
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        return True, None
    refund = refunds[0]
    execution = _verified_refund_execution(refund)
    if not execution or not execution.termination_processed_at:
        return True, None
    if assignment.provider_account_id not in execution.proposal.affected_accounts:
        return False, None
    if order.addon_refunds.filter(main_pending_deduction__gt=0).exists():
        return True, None
    share = NegotiationExecutionShare.objects.filter(execution=execution, assignment=assignment,
        account_id=assignment.provider_account_id).select_related(
            'settled_report', 'income_transaction__wallet').first()
    terms = {item['account_id']: item for item in execution.proposal.provider_terms}
    term = terms.get(assignment.provider_account_id)
    if not share or not term:
        return True, None
    try:
        original = assignment.provider_income - deductions_at(order, before=execution.created_at).get(
            assignment.provider_account_id, 0)
        allocation = next((item for item in refund.negotiated_allocation
                           if item.get('share_id') == assignment.pk), None)
        report = share.settled_report
        if (original != share.original_income or (share.original_income, share.burden, share.retained_income) != (
                    term['original_income_units'], term['burden_units'], term['retained_units'])
                or share.original_income != share.pending_deduction + share.retained_income
                or share.burden != share.pending_deduction + share.wallet_deduction + share.deposit_deduction
                or not allocation or allocation.get('entitlement') != share.original_income
                or allocation.get('amount') != share.burden or allocation.get('retained') != share.retained_income
                or not assignment.settled_at or share.settled_at != assignment.settled_at
                or not report or report.order_id != order.pk or report.status != ProviderReport.Status.APPROVED
                or report.provider_account_id != assignment.provider_account_id
                or report.provider_id != assignment.provider_id or report.payout_amount != assignment.provider_income
                or not report.submitted_at or not report.audited_at
                or not report.submitted_at <= report.audited_at <= assignment.settled_at
                or share.report_submitted_at != report.submitted_at
                or not order.completed_at or order.service_aftersale_hours is None or order.service_freeze_hours is None
                or assignment.settled_at < order.completed_at
                or assignment.settled_at < negotiated_aftersale_start(
                    order, execution, assignment, share) + timedelta(hours=order.service_aftersale_hours)
                or assignment.settled_at < report.submitted_at + timedelta(hours=order.service_freeze_hours)
                or missing_report_evidence(report)):
            return True, None
        debit = share.wallet_transaction
        if bool(debit) != bool(share.wallet_deduction):
            return True, None
        if debit and (debit.order_id != order.pk or debit.wallet.account_id != assignment.provider_account_id
                or debit.wallet.user_id != assignment.provider_id or debit.tx_type != 'REFUND'
                or debit.status != 'SUCCESS' or debit.amount != -share.wallet_deduction
                or debit.balance_after != debit.balance_before + debit.amount
                or debit.operator_account_id != execution.operator_id or debit.operator_id != refund.operator_id
                or debit.reversal_of_id or debit.reversals.exists()):
            return True, None
        deficit = share.deposit_deficit
        if bool(deficit) != bool(share.deposit_deduction):
            return True, None
        if deficit:
            from django.conf import settings
            source = deficit.source_store_transaction
            if (deficit.refund_id != refund.pk or deficit.account_id != assignment.provider_account_id
                    or deficit.profile.user_id != assignment.provider_id or deficit.amount != share.deposit_deduction
                    or deficit.source_kind != 'DIRECT' or not source or source.order_id != order.pk
                    or source.tx_type != 'DEPOSIT_INCOME' or source.status != 'SUCCESS'
                    or source.amount != -deficit.amount or not source.wallet.user
                    or source.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
                    or source.balance_after != source.balance_before + source.amount
                    or source.operator_account_id != execution.operator_id or source.operator_id != refund.operator_id
                    or source.reversal_of_id or source.reversals.exists()):
                return True, None
        income = share.income_transaction
        credits = list(order.transactions.filter(tx_type='INCOME', status='SUCCESS', reversal_of__isnull=True,
            wallet__account_id=assignment.provider_account_id, wallet__user_id=assignment.provider_id,
            remark=f'订单收入：{order.order_no}'))
        if (len(credits) != (1 if share.retained_income else 0)
                or bool(income) != bool(share.retained_income)):
            return True, None
        if income and (credits[0].pk != income.pk or income.order_id != order.pk
                or income.wallet.account_id != assignment.provider_account_id
                or income.wallet.user_id != assignment.provider_id or income.amount != share.retained_income
                or income.balance_after != income.balance_before + income.amount
                or income.reversal_of_id or income.reversals.exists()):
            return True, None
        return True, share
    except (ValidationError, ValueError, TypeError, KeyError, AttributeError):
        return True, None


def clearance_evidence(profile, wallet, *, now=None, addon_evidence=None, reservation_evidence=None):
    from orders.aftersale_scope import applies_to
    from orders.models import (AfterSale, Order, OrderPlatformSubsidyShare, OrderProvider,
                               OrderRefund, OrderSegmentPersonSettlement)
    from wallet.models import IncomeHold, ProviderReport, Transaction

    now = now or timezone.now()
    account_id, user_id = profile.account_id, profile.user_id
    if not account_id:
        return ClearanceEvidence(needs_review=True)
    timestamps = []
    invalid = False
    has_business = False
    if reservation_evidence is None:
        from .deposit_reservation_clearance import reservation_clearance
        reservation_evidence = reservation_clearance(profile, now=now)
    invalid = reservation_evidence['needs_review'] or bool(reservation_evidence['active'])
    has_business = reservation_evidence['has_business']
    timestamps.extend(reservation_evidence['timestamps'])

    def record(value, *, no_earlier_than=None):
        nonlocal invalid
        if (not value or timezone.is_naive(value) or value > now
                or (no_earlier_than and (timezone.is_naive(no_earlier_than) or value < no_earlier_than))):
            invalid = True
        else:
            timestamps.append(value)

    def history_time(value):
        if not isinstance(value, str):
            return None
        try:
            return parse_datetime(value)
        except (ValueError, TypeError, OverflowError):
            return None

    # Refund-deposit obligations can be cleared after the order and its
    # earnings have settled. Their actual repayment and permit events are
    # part of the seven-day clock for both deposit modes.
    from wallet.models import RefundDepositDeficit, RefundDepositRecoveryPermit
    for deficit in RefundDepositDeficit.objects.filter(profile=profile, account_id=account_id).iterator():
        has_business = True
        income_rows = list(deficit.repayments.select_related(
            'application__source_transaction', 'provider_transaction__wallet', 'store_transaction'))
        cash_rows = list(deficit.cash_repayments.select_related('topup__platform_transaction'))
        if (deficit.remaining or not deficit.closed_at or not deficit.account_business_cycle
                or sum(row.amount for row in income_rows + cash_rows) != deficit.amount):
            invalid = True
            continue
        record(deficit.closed_at, no_earlier_than=deficit.created_at)
        for row in income_rows:
            application, debit, credit = row.application, row.provider_transaction, row.store_transaction
            if (application.profile_id != profile.pk or application.account_id != account_id
                    or not wallet or debit.wallet_id != wallet.pk or debit.amount != -row.amount
                    or debit.tx_type != Transaction.TxType.DEPOSIT or debit.status != Transaction.Status.SUCCESS
                    or credit.deposit_pair_id != debit.pk or credit.amount != row.amount
                    or credit.tx_type != Transaction.TxType.DEPOSIT_INCOME or credit.status != Transaction.Status.SUCCESS
                    or debit.deposit_owner_id != account_id or credit.deposit_owner_id != account_id
                    or debit.deposit_business_cycle != deficit.account_business_cycle
                    or credit.deposit_business_cycle != deficit.account_business_cycle
                    or debit.balance_after != debit.balance_before + debit.amount
                    or credit.balance_after != credit.balance_before + credit.amount
                    or debit.reversal_of_id or credit.reversal_of_id
                    or debit.reversals.exists() or credit.reversals.exists()):
                invalid = True
                continue
            record(application.source_transaction.created_at)
            record(debit.created_at, no_earlier_than=application.source_transaction.created_at)
            record(credit.created_at, no_earlier_than=debit.created_at)
            record(row.created_at, no_earlier_than=credit.created_at)
        for row in cash_rows:
            topup, credit = row.topup, row.topup.platform_transaction
            if (topup.profile_id != profile.pk or topup.account_id != account_id
                    or topup.account_business_cycle != deficit.account_business_cycle
                    or topup.kind != 'SUPPLEMENT' or not credit or row.amount > topup.amount
                    or credit.tx_type != Transaction.TxType.DEPOSIT_INCOME
                    or credit.status != Transaction.Status.SUCCESS or credit.amount != topup.amount
                    or credit.balance_after != credit.balance_before + credit.amount
                    or credit.reversal_of_id or credit.reversals.exists()):
                invalid = True
                continue
            record(credit.created_at)
            record(topup.created_at, no_earlier_than=credit.created_at)
            record(row.created_at, no_earlier_than=topup.created_at)
    for permit in RefundDepositRecoveryPermit.objects.filter(profile=profile, account_id=account_id).select_related('order'):
        has_business = True
        if not permit.closed_at or not permit.close_reason:
            invalid = True
            continue
        record(permit.closed_at, no_earlier_than=permit.created_at)

    owner = Q(provider_account_id=account_id)
    order_owner = owner | Q(providers__provider_account_id=account_id)
    if user_id is not None:
        owner |= Q(provider_id=user_id)
        order_owner |= Q(provider_id=user_id) | Q(providers__provider_id=user_id)
    orders = list(Order.objects.filter(order_owner).distinct())
    order_ids = [order.pk for order in orders]
    order_created_at = {order.pk: order.created_at for order in orders}
    orders_with_shares = set(OrderProvider.objects.filter(order_id__in=order_ids).values_list('order_id', flat=True))
    shares = list(OrderProvider.objects.filter(owner, order_id__in=order_ids).select_related('order'))
    own_share_orders = {share.order_id for share in shares}
    if addon_evidence is None:
        from .deposit_addon_clearance import addon_clearance
        addon_evidence = addon_clearance(profile, wallet, now=now)
    incomes = {}
    if wallet:
        from django.db.models import Exists, OuterRef
        for income in Transaction.objects.filter(wallet=wallet, order_id__in=order_ids,
                tx_type__in=[Transaction.TxType.INCOME, Transaction.TxType.GIFT],
                status=Transaction.Status.SUCCESS).exclude(
                    pk__in=addon_evidence['income_transaction_ids']).annotate(
                    has_reversal=Exists(Transaction.objects.filter(reversal_of_id=OuterRef('pk')))).iterator():
            incomes.setdefault((income.order_id, income.amount, income.tx_type), []).append(income)
    for order in orders:
        has_business = True
        if (user_id is not None and ((order.provider_id == user_id and order.provider_account_id not in (None, account_id))
                or (order.provider_account_id == account_id and order.provider_id not in (None, user_id)))):
            invalid = True
        if (((user_id is not None and order.provider_id == user_id) or order.provider_account_id == account_id)
                and order.pk in orders_with_shares and order.pk not in own_share_orders):
            invalid = True
        if order.status == Order.Status.COMPLETED:
            # A gift captures its payment time before inserting the order and
            # credits the recipient later in the same transaction.
            if order.is_gift_snapshot:
                record(order.completed_at)
                record(order.created_at, no_earlier_than=order.completed_at)
            else:
                record(order.completed_at, no_earlier_than=order.created_at)
        elif order.status == Order.Status.CANCELLED:
            record(order.cancelled_at, no_earlier_than=order.created_at)
            if (order.amount > 0 and order.payment_status in
                    (Order.PaymentStatus.PAID, Order.PaymentStatus.PARTIALLY_REFUNDED) and not order.settled_at):
                invalid = True
        else:
            invalid = True
        # Participant-specific settlement timestamps are considered below;
        # an unrelated teammate settling later does not reset this clock.
        if order.settled_at and order.pk not in orders_with_shares:
            record(order.settled_at)
            if order.provider_income:
                kind = Transaction.TxType.GIFT if order.is_gift_snapshot else Transaction.TxType.INCOME
                income = incomes.get((order.pk, order.provider_income, kind), [])
                if (len(income) != 1 or income[0].balance_after != income[0].balance_before + income[0].amount
                        or income[0].reversal_of_id or income[0].has_reversal):
                    invalid = True
                elif order.is_gift_snapshot:
                    gift_income = income[0]
                    clocks = (order.gift_aftersale_hours, order.gift_freeze_hours)
                    payments = list(Transaction.objects.filter(order=order, tx_type=Transaction.TxType.GIFT,
                        amount__lt=0, status=Transaction.Status.SUCCESS).select_related('wallet'))
                    if (order.settled_at != order.completed_at or not order.completed_at
                            or gift_income.created_at < order.created_at
                            or not order.customer_account_id or len(payments) != 1
                            or payments[0].wallet.account_id != order.customer_account_id
                            or payments[0].wallet.user_id != order.customer_id
                            or payments[0].amount != -order.amount
                            or payments[0].balance_after != payments[0].balance_before + payments[0].amount
                            or payments[0].reversal_of_id or payments[0].reversals.exists()
                            or not order.created_at <= payments[0].created_at <= gift_income.created_at
                            or any(type(hours) is not int or hours < 0 for hours in clocks)):
                        invalid = True
                    else:
                        record(gift_income.created_at, no_earlier_than=order.created_at)
                        hold = IncomeHold.objects.filter(income_transaction=gift_income).first()
                        hours = max(clocks)
                        if ((hours and not hold) or (hold and (not hold.released_at or hold.cancelled_at
                                or hold.wallet_id != wallet.pk or hold.amount != gift_income.amount
                                or hold.release_at != order.completed_at + timedelta(hours=hours)))):
                            invalid = True
                        elif hold:
                            freezes = list(Transaction.objects.filter(wallet=wallet, order=order,
                                tx_type=Transaction.TxType.EARNINGS_FREEZE, status=Transaction.Status.SUCCESS))
                            releases = list(Transaction.objects.filter(wallet=wallet, order=order,
                                tx_type=Transaction.TxType.EARNINGS_RELEASE, status=Transaction.Status.SUCCESS))
                            from wallet.income import PRIORITY_GIFT_FREEZE_REMARK
                            if (len(freezes) != 1 or len(releases) != 1 or hold.recovered_amount > hold.amount
                                    or freezes[0].amount != -hold.amount
                                    or (not hours and freezes[0].remark != PRIORITY_GIFT_FREEZE_REMARK)
                                    or releases[0].amount != hold.amount - hold.recovered_amount
                                    or any(tx.balance_after != tx.balance_before + tx.amount or tx.reversal_of_id
                                           or tx.reversals.exists() for tx in freezes + releases)):
                                invalid = True
                            else:
                                record(freezes[0].created_at, no_earlier_than=gift_income.created_at)
                                record(releases[0].created_at, no_earlier_than=gift_income.created_at)
                else:
                    record(income[0].created_at, no_earlier_than=order.created_at)
        elif (order.pk not in orders_with_shares and order.provider_income
              and order.status == Order.Status.COMPLETED and not order.settled_at):
            invalid = True
        if (order.amount > 0 and order.payment_status == Order.PaymentStatus.REFUNDED
                and not order.refund_records.exists()):
            invalid = True

    for share in shares:
        if (share.provider_account_id not in (None, account_id)
                or (user_id is not None and share.provider_id not in (None, user_id))):
            invalid = True
        segmented, amount = verified_segment_credit(share.order, account_id)
        if segmented:
            person = OrderSegmentPersonSettlement.objects.filter(
                manifest__order_id=share.order_id, account_id=account_id).select_related(
                    'income_transaction__wallet').first()
            if amount is None or not person or person.amount != amount:
                invalid = True
            else:
                record(person.settled_at, no_earlier_than=share.order.created_at)
                income = person.income_transaction
                if income:
                    if (not wallet or income.wallet_id != wallet.pk
                            or income.wallet.account_id != account_id
                            or (user_id is not None and income.wallet.user_id != user_id)):
                        invalid = True
                    else:
                        record(income.created_at, no_earlier_than=share.order.created_at)
            continue
        from .deposit_points_clearance import verified_initial_person, verified_refunded_person
        points_refund, person = verified_refunded_person(share.order, share)
        if not points_refund:
            points_refund, person = _verified_ordinary_negotiated_person(share.order, share)
        if not points_refund:
            points_refund, person = verified_initial_person(share.order, share)
        if points_refund:
            if not person:
                invalid = True
            else:
                record(person.settled_at, no_earlier_than=share.order.created_at)
                income = person.income_transaction
                if income:
                    if (not wallet or income.wallet_id != wallet.pk
                            or income.wallet.account_id != account_id
                            or (user_id is not None and income.wallet.user_id != user_id)):
                        invalid = True
                    else:
                        record(income.created_at, no_earlier_than=share.order.created_at)
            continue
        if share.settled_at:
            record(share.settled_at, no_earlier_than=share.created_at)
            if share.provider_income:
                income = incomes.get((share.order_id, share.provider_income, Transaction.TxType.INCOME), [])
                if (len(income) != 1 or income[0].balance_after != income[0].balance_before + income[0].amount
                        or income[0].reversal_of_id or income[0].has_reversal):
                    invalid = True
                else:
                    record(income[0].created_at, no_earlier_than=share.created_at)
        elif (share.order.status == Order.Status.COMPLETED
              and (share.settlement_base > 0 or share.provider_income > 0)):
            invalid = True

    subsidy_owner = Q(assignment__provider_account_id=account_id)
    if user_id is not None:
        subsidy_owner |= Q(assignment__provider_id=user_id)
    for subsidy in OrderPlatformSubsidyShare.objects.filter(
            subsidy_owner,
            subsidy__order_id__in=order_ids, amount__gt=0).select_related(
                'subsidy', 'income_transaction__wallet', 'expense_transaction__wallet__user', 'assignment').iterator():
        record(subsidy.paid_at)
        income, expense = subsidy.income_transaction, subsidy.expense_transaction
        from django.conf import settings
        if (not subsidy.assignment.settled_at or not income or not expense
                or not wallet or income.wallet_id != wallet.pk
                or income.order_id != subsidy.subsidy.order_id or expense.order_id != subsidy.subsidy.order_id
                or income.tx_type != 'INCOME' or income.status != 'SUCCESS' or income.amount != subsidy.amount
                or expense.tx_type != 'SUBSIDY_EXPENSE' or expense.status != 'SUCCESS'
                or expense.amount != -subsidy.amount or not expense.wallet.user
                or expense.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
                or income.balance_after != income.balance_before + income.amount
                or expense.balance_after != expense.balance_before + expense.amount
                or income.reversal_of_id or expense.reversal_of_id
                or income.reversals.exists() or expense.reversals.exists()):
            invalid = True
        else:
            record(income.created_at, no_earlier_than=subsidy.assignment.created_at)
            record(expense.created_at, no_earlier_than=subsidy.assignment.created_at)
        if subsidy.assignment.settled_at:
            record(subsidy.assignment.settled_at)

    for refund in OrderRefund.objects.filter(order_id__in=order_ids).iterator():
        # A complete negotiated allocation can establish that only another
        # teammate bore the refund. Ambiguous historical refunds reset the
        # clock conservatively instead of being silently ignored.
        if not _verified_unaffected_refund(refund, account_id):
            record(refund.created_at, no_earlier_than=order_created_at[refund.order_id])

    if addon_evidence['pending'] or addon_evidence['needs_review']:
        invalid = True
    for closed_at in addon_evidence['timestamps']:
        has_business = True
        record(closed_at)

    for case in AfterSale.objects.filter(order_id__in=order_ids).iterator():
        if applies_to(case, account_id):
            if case.status == 'OPEN':
                invalid = True
            else:
                record(case.resolved_at, no_earlier_than=case.created_at)
        if not isinstance(case.history, list):
            invalid = True
            continue
        for item in case.history:
            if not isinstance(item, dict):
                invalid = True
                continue
            scope = item.get('affected_provider_account_ids')
            if scope is not None and (not isinstance(scope, list) or not scope
                    or any(type(value) is not int or value <= 0 for value in scope)
                    or len(scope) != len(set(scope))):
                invalid = True
                continue
            if scope is not None and account_id not in scope:
                continue
            if item.get('status') == 'OPEN':
                continue
            value = item.get('resolved_at')
            created = history_time(item.get('created_at'))
            if not created:
                invalid = True
            record(history_time(value), no_earlier_than=created)

    if wallet:
        # Every order-backed earnings deposit (including a refund reversal)
        # must carry its own owner/cycle and an explicit opposing ledger row.
        for debit in Transaction.objects.filter(wallet=wallet, order__isnull=False,
                tx_type=Transaction.TxType.DEPOSIT, status=Transaction.Status.SUCCESS).iterator():
            has_business = True
            counterpart = Transaction.objects.filter(deposit_pair_id=debit.pk).select_related('wallet__user').first()
            from django.conf import settings
            if (debit.deposit_owner_id != account_id or not debit.deposit_business_cycle
                    or not counterpart or counterpart.deposit_owner_id != account_id
                    or counterpart.deposit_business_cycle != debit.deposit_business_cycle
                    or counterpart.order_id != debit.order_id
                    or counterpart.tx_type != Transaction.TxType.DEPOSIT_INCOME
                    or counterpart.status != Transaction.Status.SUCCESS or counterpart.amount != -debit.amount
                    or not counterpart.wallet.user or counterpart.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
                    or debit.balance_after != debit.balance_before + debit.amount
                    or counterpart.balance_after != counterpart.balance_before + counterpart.amount):
                invalid = True
        for hold in IncomeHold.objects.filter(wallet=wallet).select_related(
                'income_transaction', 'release_transaction').iterator():
            has_business = True
            if (hold.income_transaction.wallet_id != wallet.pk
                    or hold.income_transaction.created_at > hold.created_at):
                invalid = True
            if bool(hold.released_at) == bool(hold.cancelled_at):
                invalid = True
            else:
                record(hold.released_at or hold.cancelled_at, no_earlier_than=hold.created_at)
                if hold.released_at and hold.released_at < hold.release_at:
                    invalid = True
                if hold.released_at:
                    release = hold.release_transaction
                    if (not release or release.wallet_id != wallet.pk
                            or release.order_id != hold.income_transaction.order_id
                            or release.tx_type != Transaction.TxType.EARNINGS_RELEASE
                            or release.status != Transaction.Status.SUCCESS
                            or hold.recovered_amount > hold.amount
                            or release.amount != hold.amount - hold.recovered_amount
                            or release.balance_after != release.balance_before + release.amount
                            or release.reversal_of_id or release.reversals.exists()):
                        invalid = True
                    else:
                        # The worker's released_at is sampled before it locks
                        # the wallet and writes this transaction. The wait can
                        # only begin once the money was actually released.
                        record(release.created_at, no_earlier_than=hold.released_at)

    for report in ProviderReport.objects.filter(owner, order__isnull=True).select_related('transaction').iterator():
        if not report.amount and not report.payout_amount and not report.transaction_id:
            continue
        has_business = True
        if ((user_id is not None and report.provider_id != user_id)
                or report.provider_account_id not in (None, account_id)
                or (user_id is None and report.provider_account_id != account_id)
                or (report.transaction_id and (not wallet or report.transaction.wallet_id != wallet.pk))):
            invalid = True
        if report.status == ProviderReport.Status.REJECTED:
            record(report.audited_at, no_earlier_than=report.created_at)
        elif report.status == ProviderReport.Status.APPROVED and report.transaction:
            record(report.audited_at, no_earlier_than=report.created_at)
            record(report.transaction.created_at, no_earlier_than=report.audited_at)
        elif report.status == ProviderReport.Status.APPROVED and not report.payout_amount:
            record(report.audited_at, no_earlier_than=report.created_at)
        else:
            invalid = True

    return ClearanceEvidence(max(timestamps) if timestamps else None, invalid, has_business)


def refund_wait_state(profile, wallet, *, now=None, addon_evidence=None, reservation_evidence=None):
    """Describe the fixed seven-day wait without mutating history or funds."""
    now = now or timezone.now()
    evidence = clearance_evidence(profile, wallet, now=now, addon_evidence=addon_evidence,
                                  reservation_evidence=reservation_evidence)
    if evidence.needs_review:
        return {'status': 'REVIEW', 'cleared_at': None, 'eligible_at': None}
    if not evidence.has_business:
        cleared_at = _deposit_paid_at(profile, wallet, now=now)
        if not cleared_at:
            return {'status': 'REVIEW', 'cleared_at': None, 'eligible_at': None}
        eligible_at = cleared_at + timedelta(days=7)
        return {'status': 'READY' if now >= eligible_at else 'WAITING',
                'cleared_at': cleared_at, 'eligible_at': eligible_at}
    if not evidence.last_closed_at:
        return {'status': 'REVIEW', 'cleared_at': None, 'eligible_at': None}
    cleared_at = evidence.last_closed_at
    if profile.deposit_mode == 'CASH':
        # A provider may finish all business before completing a later cash
        # deposit. The seven-day period cannot start before that deposit is
        # actually paid; verify it using the same receipt and ledger facts as
        # the no-order path instead of accepting the earlier order timestamp.
        from wallet.deposit_payment import snapshot_storage_available
        from wallet.models import CashDepositTopup, DepositPaymentSnapshot

        cash_after = CashDepositTopup.objects.filter(
            profile=profile, created_at__gt=cleared_at).exists()
        online_after = (snapshot_storage_available() and DepositPaymentSnapshot.objects.filter(
            profile=profile, created_at__gt=cleared_at).exists())
        if cash_after or online_after:
            paid_at = _deposit_paid_at(profile, wallet, now=now)
            if not paid_at:
                return {'status': 'REVIEW', 'cleared_at': None, 'eligible_at': None}
            cleared_at = max(cleared_at, paid_at)
    eligible_at = cleared_at + timedelta(days=7)
    return {'status': 'READY' if now >= eligible_at else 'WAITING',
            'cleared_at': cleared_at, 'eligible_at': eligible_at}


def _deposit_paid_at(profile, wallet, *, now):
    """Use a verified complete deposit; ambiguous older data needs review."""
    from django.conf import settings
    from wallet.deposit_payment import snapshot_storage_available
    from wallet.models import CashDepositTopup, DepositPaymentSnapshot, Transaction

    if profile.deposit_mode != 'CASH':
        return None
    cash = CashDepositTopup.objects.filter(profile=profile).select_related(
        'platform_transaction__wallet__user').order_by('-created_at', '-pk').first()
    snapshot_available = snapshot_storage_available()
    online = (DepositPaymentSnapshot.objects.filter(profile=profile).select_related(
        'provider_transaction', 'platform_transaction__wallet__user').order_by('-created_at', '-pk').first()
        if snapshot_available else None)
    if cash and online and cash.created_at == online.created_at:
        return None
    row = max((item for item in (cash, online) if item), key=lambda item: item.created_at, default=None)
    if not row:
        return None
    tx = row.platform_transaction
    if (row.account_id != profile.account_id or row.account_business_cycle != profile.deposit_business_cycle
            or row.amount <= 0 or row.target_snapshot <= 0
            or row.paid_after != row.paid_before + row.amount or row.paid_after < row.target_snapshot
            or not row.created_at or timezone.is_naive(row.created_at) or row.created_at > now
            or not tx or not tx.wallet.user or tx.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
            or tx.tx_type != Transaction.TxType.DEPOSIT_INCOME or tx.status != Transaction.Status.SUCCESS
            or tx.amount != row.amount or tx.order_id or tx.balance_after != tx.balance_before + tx.amount
            or tx.created_at > row.created_at or tx.reversal_of_id or tx.reversals.exists()):
        return None
    if isinstance(row, DepositPaymentSnapshot):
        source = row.provider_transaction
        if (not wallet or source.wallet_id != wallet.pk or source.order_id
                or source.tx_type != Transaction.TxType.DEPOSIT or source.status != Transaction.Status.SUCCESS
                or source.amount != -row.amount or source.balance_after != source.balance_before + source.amount
                or source.created_at > row.created_at or source.reversal_of_id or source.reversals.exists()):
            return None
    balance = row.paid_after
    if wallet:
        earlier_snapshot_ids = (set(DepositPaymentSnapshot.objects.filter(profile=profile,
            created_at__lte=row.created_at).values_list('provider_transaction_id', flat=True))
            if snapshot_available else set())
        for movement in Transaction.objects.filter(wallet=wallet, tx_type=Transaction.TxType.DEPOSIT,
                created_at__gte=row.created_at).order_by('created_at', 'pk').iterator():
            if movement.pk in earlier_snapshot_ids:
                continue
            if (movement.status != Transaction.Status.SUCCESS or movement.amount <= 0 or movement.order_id
                    or not movement.remark.startswith('退还押金：') or movement.reversal_of_id
                    or movement.reversals.exists() or movement.created_at > now
                    or movement.balance_after != movement.balance_before + movement.amount):
                return None
            balance -= movement.amount
    if balance != profile.deposit_paid or balance < 0:
        return None
    return row.created_at
