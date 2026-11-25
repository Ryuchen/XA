"""Ledger primitive for explicitly funded VIP safety-box provider shares.

This module is enabled only for the staged VIP safety-box settlement path.
"""
from stores.db import transaction
from django.db.models import Exists, OuterRef, Q, Sum

from .financial_lock import lock_order_money
from .models import Order, OrderPlatformSubsidyShare
from wallet.income import hold_provider_income
from wallet.models import Transaction, get_platform_wallet
from wallet.services import get_wallet
from wallet.settlement_clocks import (
    ClockConflict, clock_scope, receipt as clock_receipt, record as record_clock,
    require_clock, validate_receipt, verify as verify_clock,
)


def released_before_service(order, assignment):
    """Only skip a report when recorded timestamps prove no service occurred."""
    if (not order.created_at or not order.in_service_at or not assignment.created_at
            or not assignment.released_at or not order.completed_at
            or not (order.created_at <= assignment.created_at <= assignment.released_at
                    < order.in_service_at <= order.completed_at)):
        return False
    from .models import OrderParticipationSegment
    return not OrderParticipationSegment.objects.filter(
        attribution__order=order, provider_account_id=assignment.provider_account_id,
        started_at__isnull=False).exists()


def _aftersale_ready(order, assignment, *, now):
    if (order.status != Order.Status.COMPLETED or order.payment_status != Order.PaymentStatus.PAID
            or not order.completed_at or order.completed_at > now or order.service_clock_version is None
            or order.service_aftersale_hours is None or order.service_freeze_hours is None
            or order.service_deposit_percent is None or not 0 <= order.service_deposit_percent <= 100):
        return False
    from datetime import timedelta
    if now < order.completed_at + timedelta(hours=order.service_aftersale_hours):
        return False
    from .aftersale_scope import applies_to
    if any(applies_to(case, assignment.provider_account_id) for case in order.aftersales.filter(status='OPEN')):
        return False
    return True


def _share_ready(order, assignment, *, now):
    """A subsidy is service income: honor the order and this person's clocks."""
    if not _aftersale_ready(order, assignment, now=now):
        return False
    from datetime import timedelta
    from wallet.models import ProviderReport
    report = ProviderReport.objects.filter(order=order, provider_id=assignment.provider_id,
        provider_account_id=assignment.provider_account_id).first()
    if (not report or report.status != ProviderReport.Status.APPROVED or not report.submitted_at
            or not report.audited_at or not (report.auditor_id or report.auditor_account_id)
            or report.audited_at < report.submitted_at or report.audited_at > now
            or report.amount != assignment.settlement_base or report.payout_amount != assignment.provider_income
            or report.commission_rate != assignment.commission_rate
            or now < report.submitted_at + timedelta(hours=order.service_freeze_hours)):
        return False
    from .services import missing_report_evidence
    return not missing_report_evidence(report)


def _locked_subsidy_shares(order):
    """Verify identities and any already-paid subsidy before another credit."""
    from club_accounts.models import LegacyAccountMap
    from django.conf import settings
    from users.models import EscortProfile
    from .settlement import SettlementError
    shares = list(OrderPlatformSubsidyShare.objects.select_for_update().filter(
        subsidy__order=order).select_related('assignment', 'subsidy').order_by('pk'))
    assignments = list(order.providers.select_for_update().order_by('pk'))
    current = [row.assignment for row in shares if not row.assignment.released_at]
    if (not shares or {row.assignment_id for row in shares} != {row.pk for row in assignments}
            or len(current) != 1
            or current[0].provider_id != order.provider_id or current[0].provider_account_id != order.provider_account_id
            or order.amount or order.provider_income or order.shop_income or order.inviter_commission
            or sum(row.amount for row in shares) > shares[0].subsidy.basis
            or not order.customer_id or not order.customer_account_id
            or order.business_cycle != order.customer_account.business_cycle
            or not LegacyAccountMap.objects.filter(legacy_user_id=order.customer_id,
                                                    account_id=order.customer_account_id).exists()
            or any(row.assignment.order_id != order.pk or not row.assignment.provider_id
                   or not row.assignment.provider_account_id for row in shares)):
        raise SettlementError('安全箱补贴或参与人份额待核对')
    pairs={(row.assignment.provider_id, row.assignment.provider_account_id) for row in shares}
    profiles=set(EscortProfile.objects.select_for_update().filter(
        user_id__in=[user for user, _ in pairs]).values_list('user_id', 'account_id'))
    mappings=set(LegacyAccountMap.objects.filter(
        legacy_user_id__in=[user for user, _ in pairs]).values_list('legacy_user_id', 'account_id'))
    if len(pairs) != len(shares) or mappings != pairs or profiles != pairs:
        raise SettlementError('安全箱陪玩身份待核对')
    for row in shares:
        income, expense = row.income_transaction, row.expense_transaction
        if not row.paid_at:
            if income or expense or row.assignment.settled_at:
                raise SettlementError('安全箱未结算份额与流水不一致')
            continue
        if not row.assignment.settled_at:
            raise SettlementError('安全箱已支付份额的结算时间待核对')
        if not row.amount:
            if income or expense:
                raise SettlementError('安全箱零收益份额的流水待核对')
            continue
        if (not income or not expense or income.order_id != order.pk or expense.order_id != order.pk
                or income.tx_type != 'INCOME' or income.status != 'SUCCESS' or income.amount != row.amount
                or expense.tx_type != 'SUBSIDY_EXPENSE' or expense.status != 'SUCCESS' or expense.amount != -row.amount
                or income.wallet.user_id != row.assignment.provider_id
                or income.wallet.account_id != row.assignment.provider_account_id
                or not expense.wallet.user or expense.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
                or income.balance_after != income.balance_before + income.amount
                or expense.balance_after != expense.balance_before + expense.amount
                or income.reversal_of_id or expense.reversal_of_id
                or income.reversals.exists() or expense.reversals.exists()):
            raise SettlementError('安全箱已支付补贴流水待核对')
    from .subsidy_execution import table_available, verify_execution
    execution = None
    if table_available():
        from .models import SubsidyAgreementExecution
        execution = SubsidyAgreementExecution.objects.filter(order=order).select_related(
            'order', 'proposal__case').first()
        if execution:
            from rest_framework.exceptions import ValidationError
            try:
                verify_execution(execution, locked_shares=shares)
            except ValidationError as error:
                raise SettlementError('安全箱协商凭证待核对') from error
    elif getattr(settings, 'VIP_SAFE_BOX_NEGOTIATION_ENABLED', False):
        raise SettlementError('安全箱协商凭证表尚未就绪')
    from .subsidy_segment_capture import verify_entry_capture
    verify_entry_capture(order, shares, execution)
    if any(row.paid_at for row in shares):
        from .models import OrderOriginalShareTransfer, OrderSegmentFinancialCoverage
        if (OrderSegmentFinancialCoverage.objects.filter(attribution__order=order, source='PLATFORM_SUBSIDY').exists()
                and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
            from .subsidy_segment_settlement import verify_paid_shares_locked
            verify_paid_shares_locked(order, shares)
    return shares


def _verify_direct_original_payment(order, subsidy):
    """A zero-paid subsidized order still needs its original payment fact."""
    from .settlement import SettlementError
    payments = list(Transaction.objects.filter(
        order=order, tx_type=Transaction.TxType.PAY, status=Transaction.Status.SUCCESS,
        reversal_of__isnull=True,
    ).select_related('wallet'))
    if (subsidy.order_id != order.pk or type(subsidy.basis) is not int or subsidy.basis < 1
            or type(subsidy.coupon_id_snapshot) is not int or subsidy.coupon_id_snapshot < 1
            or len(payments) != 1):
        raise SettlementError('安全箱原付款或补贴事实待核对')
    payment = payments[0]
    if (payment.amount != 0 or payment.balance_after != payment.balance_before
            or payment.wallet.user_id != order.customer_id
            or payment.wallet.account_id != order.customer_account_id
            or payment.reversal_of_id or payment.reversals.exists()):
        raise SettlementError('安全箱原付款流水待核对')


_DIRECT_SUBJECT_NAMESPACE = 1 << 60


def _direct_subject_id(pk):
    # Receipt uniqueness is global to subject_type/id/operation, not per order.
    # Keep direct subsidy/share IDs apart from raw segment manifest/person IDs.
    if type(pk) is not int or not 0 < pk < _DIRECT_SUBJECT_NAMESPACE:
        raise ClockConflict('安全箱直接份额时钟主体编号待核对')
    return _DIRECT_SUBJECT_NAMESPACE + pk


def _verify_direct_clock_history(order, subsidy, shares, clock):
    """Recheck original facts and every paid person before another direct credit."""
    _verify_direct_original_payment(order, subsidy)
    plan_id = _direct_subject_id(subsidy.pk)
    plan = clock_receipt(order, 'MANIFEST', plan_id, 'PLAN')
    paid = [row for row in shares if row.paid_at]
    if plan is None:
        if paid:
            raise ClockConflict('安全箱直接份额缺少原清单时钟回执')
    else:
        validate_receipt(plan.clock_evidence, plan.observed_at, clock)
        for row in paid:
            if row.paid_at < plan.observed_at:
                raise ClockConflict('安全箱直接份额支付早于原清单，需人工核对')
            verify_clock(order, 'PERSON', _direct_subject_id(row.pk), 'PERSON', row.paid_at, clock)
    if any(clock_receipt(order, 'PERSON', _direct_subject_id(row.pk), 'PERSON') is not None
           for row in shares if not row.paid_at):
        raise ClockConflict('安全箱未支付份额已存在时钟回执，需人工核对')
    final = clock_receipt(order, 'MANIFEST', plan_id, 'FINALIZE')
    if (order.settled_at is None) != (final is None):
        raise ClockConflict('安全箱直接份额整单完成凭证状态不一致')
    if final is not None:
        verify_clock(order, 'MANIFEST', plan_id, 'FINALIZE', order.settled_at, clock)
    return plan


def subsidy_funds(platform):
    """Exclude refundable provider deposits from platform-owned funds."""
    deposits = (Transaction.objects.filter(wallet=platform, tx_type=Transaction.TxType.DEPOSIT_INCOME,
        status=Transaction.Status.SUCCESS).aggregate(total=Sum('amount'))['total'] or 0)
    reserved = max(0, deposits)
    return {'balance': platform.balance, 'deposits': reserved, 'available': max(0, platform.balance - reserved)}


def pending_subsidy_shares():
    """Unpaid completed, terminated, and completion-mode transferred shares."""
    from .models import OrderRefund
    terminated = OrderRefund.objects.filter(order_id=OuterRef('subsidy__order_id'),
                                             benefit_effects__subsidy_negotiated=True)
    return (OrderPlatformSubsidyShare.objects.filter(paid_at__isnull=True, amount__gt=0,
            subsidy__order__direct_payment__isnull=True).annotate(_negotiated=Exists(terminated)).filter(
        Q(subsidy__order__status='COMPLETED', subsidy__order__payment_status='PAID', subsidy__order__settled_at__isnull=True)
        | Q(subsidy__order__status='IN_SERVICE', subsidy__order__payment_status='PAID',
            subsidy__order__settlement_mode='COMPLETION', assignment__released_at__isnull=False)
        | Q(subsidy__order__status='CANCELLED', subsidy__order__payment_status='REFUNDED',
            subsidy__order__settled_at__isnull=True, _negotiated=True)))


def schedule_safety_box_replenishment(order):
    """Re-evaluate the buyer's current VIP benefit only after commit."""
    account_id = order.customer_account_id
    if not account_id:
        return
    def replenish():
        import logging
        try:
            from club_accounts.models import ClubAccount
            from coupons.benefits import grant_periodic
            account = ClubAccount.objects.filter(pk=account_id).first()
            if account:
                grant_periodic(account)
        except Exception:
            logging.getLogger(__name__).exception('Safety-box voucher replenishment failed for order %s', order.pk)
    transaction.on_commit(replenish)


@transaction.atomic
def settle_platform_subsidy_share(share_id, *, finishing_order=None, transferring_order=None):
    """Pay once, or return False without writes if platform funds are insufficient."""
    order_id = OrderPlatformSubsidyShare.objects.values_list('subsidy__order_id', flat=True).get(pk=share_id)
    locked_order = Order.objects.select_for_update().get(pk=order_id)
    # The state machine holds the same row lock and changes status in memory
    # before its final save. Other callers use the persisted status.
    if finishing_order is not None and transferring_order is not None:
        raise ValueError('结算上下文不一致')
    order = finishing_order or transferring_order or locked_order
    if order.pk != order_id or ((finishing_order is not None or transferring_order is not None)
                                and locked_order.status != Order.Status.IN_SERVICE):
        raise ValueError('订单完成状态已变化')
    from .addon_payment import guard_main_settlement
    guard_main_settlement(order)
    lock_order_money()
    share = OrderPlatformSubsidyShare.objects.select_for_update().select_related('assignment', 'subsidy').get(pk=share_id)
    assignment = share.assignment
    if assignment.order_id != order.pk or share.subsidy.order_id != order.pk:
        raise ValueError('补贴份额与订单不一致')
    if share.paid_at:
        if order.status == Order.Status.COMPLETED:
            shares = _locked_subsidy_shares(order)
            from .models import OrderSegmentSettlementManifest
            if not OrderSegmentSettlementManifest.objects.filter(order=order).exists():
                clock = require_clock()
                _verify_direct_clock_history(order, share.subsidy, shares, clock)
        return True
    from .segment_financial_capture import transfer_settlement_pending
    if transfer_settlement_pending(order):
        return False
    # A transferred or finishing order is still in service at the database
    # boundary; neither that event nor a legacy cancellation authorizes payout.
    clock = require_clock()
    now = clock.at
    ready = _share_ready(order, assignment, now=now)
    if not ready and not share.amount:
        ready = _aftersale_ready(order, assignment, now=now) and released_before_service(order, assignment)
    if finishing_order is not None or transferring_order is not None or not ready:
        return False
    if order.refund_records.exists():
        return False
    shares = _locked_subsidy_shares(order)
    transferring = transferring_order is not None and order.status == Order.Status.IN_SERVICE and assignment.released_at is not None
    negotiated_termination = (order.status == Order.Status.CANCELLED
        and order.payment_status == Order.PaymentStatus.REFUNDED
        and order.refund_records.filter(benefit_effects__subsidy_negotiated=True).exists())
    if ((order.status != Order.Status.COMPLETED and not transferring and not negotiated_termination)
            or (order.payment_status != Order.PaymentStatus.PAID and not negotiated_termination)):
        raise ValueError('订单尚未完成或未付款，不能支付平台补贴')
    if not assignment.provider_id and not assignment.provider_account_id:
        raise ValueError('补贴份额缺少陪玩')
    plan = _verify_direct_clock_history(order, share.subsidy, shares, clock)
    if not share.amount:
        if plan is None:
            record_clock(order, 'MANIFEST', _direct_subject_id(share.subsidy.pk), 'PLAN', clock)
        share.paid_at = now
        share.save(update_fields=['paid_at'])
        assignment.settled_at = now
        assignment.save(update_fields=['settled_at'])
        record_clock(order, 'PERSON', _direct_subject_id(share.pk), 'PERSON', clock)
        return True
    from users.models import EscortProfile
    profile = EscortProfile.objects.select_for_update().filter(user_id=assignment.provider_id).first()
    platform = get_platform_wallet(for_update=True)
    unpaid = OrderPlatformSubsidyShare.objects.filter(subsidy=share.subsidy, paid_at__isnull=True).aggregate(
        total=Sum('amount'))['total'] or 0
    if subsidy_funds(platform)['available'] < unpaid:
        return False
    provider = get_wallet(user_id=assignment.provider_id, account_id=assignment.provider_account_id, for_update=True)
    if plan is None:
        record_clock(order, 'MANIFEST', _direct_subject_id(share.subsidy.pk), 'PLAN', clock)
    platform_before = platform.balance
    platform.balance -= share.amount
    platform.save(update_fields=['balance'])
    expense = Transaction.objects.create(wallet=platform, order=order, amount=-share.amount,
        tx_type=Transaction.TxType.SUBSIDY_EXPENSE, balance_before=platform_before,
        balance_after=platform.balance, remark=f'六级会员安全箱补贴：{order.order_no}')
    provider_before = provider.balance
    provider.balance += share.amount
    provider.save(update_fields=['balance'])
    income = Transaction.objects.create(wallet=provider, order=order, amount=share.amount,
        tx_type=Transaction.TxType.INCOME, balance_before=provider_before,
        balance_after=provider.balance, remark=f'订单收入：{order.order_no}（会员安全箱补贴）')
    # Both waits have already elapsed; do not create a second frozen period.
    hold_provider_income(income, profile=profile, hold_hours=0,
                         deposit_percent=order.service_deposit_percent, apply_without_hold=True)
    share.expense_transaction = expense
    share.income_transaction = income
    share.paid_at = now
    share.save(update_fields=['expense_transaction', 'income_transaction', 'paid_at'])
    assignment.settled_at = now
    assignment.save(update_fields=['settled_at'])
    record_clock(order, 'PERSON', _direct_subject_id(share.pk), 'PERSON', clock)
    from wallet.notifier import notify_wallet_update
    transaction.on_commit(lambda: notify_wallet_update(platform))
    return True


def settle_due_subsidy_order(order):
    """Settle each ready share once after proving the whole remaining funding."""
    from .addon_payment import guard_main_settlement
    guard_main_settlement(order)
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('安全箱补贴结算须在订单事务内进行')
    if order.settled_at or order.status != Order.Status.COMPLETED or order.payment_status != Order.PaymentStatus.PAID:
        return []
    from .segment_financial_capture import transfer_settlement_pending
    if transfer_settlement_pending(order):
        from .subsidy_segment_settlement import enabled, settle_locked
        return settle_locked(order) if enabled() else []
    from .direct_payment_models import DirectPaymentIntent
    if DirectPaymentIntent.objects.filter(order=order).exists():
        return []
    lock_order_money()
    shares = _locked_subsidy_shares(order)
    if order.refund_records.exists():
        return []
    clock = require_clock()
    subsidy = shares[0].subsidy
    with clock_scope(clock):
        _verify_direct_clock_history(order, subsidy, shares, clock)
        outstanding=sum(row.amount for row in shares if not row.paid_at)
        if outstanding and subsidy_funds(get_platform_wallet(for_update=True))['available'] < outstanding:
            return []
        paid=[]
        for share in shares:
            if share.paid_at:
                continue
            unstarted = not share.amount and released_before_service(order, share.assignment)
            if settle_platform_subsidy_share(share.pk):
                share.refresh_from_db(fields=['paid_at'])
                if not unstarted:
                    paid.append(share.assignment.provider_id)
        if paid:
            from django.db.models import F
            from users.models import EscortProfile
            from .services import refresh_escort_status
            EscortProfile.objects.filter(user_id__in=paid).update(completed_order_count=F('completed_order_count')+1)
            refresh_escort_status(provider_ids=paid, exclude_order_ids=[order.pk])
        if all(row.paid_at for row in shares) and not order.aftersales.filter(status='OPEN').exists():
            _verify_direct_clock_history(order, subsidy, shares, clock)
            order.settled_at=clock.at
            order.save(update_fields=['settled_at'])
            record_clock(order, 'MANIFEST', _direct_subject_id(subsidy.pk), 'FINALIZE', clock)
        return paid


@transaction.atomic
def settle_terminated_subsidy(order_id):
    """Pay the entire agreed remainder when the platform can fund it."""
    order = Order.objects.select_for_update().get(pk=order_id)
    if (order.status != Order.Status.CANCELLED or order.payment_status != Order.PaymentStatus.REFUNDED
            or not order.refund_records.filter(benefit_effects__subsidy_negotiated=True).exists()):
        return False
    if order.settled_at:
        schedule_safety_box_replenishment(order)
        return True
    # Historical termination lacks the new per-party agreement and clocks.
    # Preserve its records for reconciliation rather than paying immediately.
    return False
