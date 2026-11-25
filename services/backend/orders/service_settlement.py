"""Release eligible service shares after overlapping order/report clocks.

The caller holds the order row in a tenant-routed atomic transaction.  The
order remains open for settlement until every required share is posted; the
inviter and shop portions are posted once in the same final transaction.
"""
from datetime import timedelta

from django.db.models import F

from club_accounts.models import LegacyAccountMap
from users.models import EscortProfile
from wallet.models import ProviderReport, Transaction
from wallet.services import get_wallet
from wallet.settlement_clocks import (
    ClockConflict, receipt as clock_receipt, require_clock, validate_receipt,
)
from .settlement import SettlementError, assert_order_conserved


def supports_partial_service_settlement(order):
    if order.is_gift_snapshot or order.service_clock_version is None:
        return False
    # Existing zero-payment safety-box subsidy accounting uses a separate
    # funding protocol and must not be bypassed by the ordinary paid path.
    from django.conf import settings
    if getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False):
        from .models import OrderPlatformSubsidy
        if OrderPlatformSubsidy.objects.filter(order=order).exists():
            return False
    return True


def legacy_report_clock_chain_enabled(order):
    """Only the ordinary versioned service path uses legacy report receipts."""
    if (not supports_partial_service_settlement(order) or order.funding_terms is not None
            or order.source_order_id or order.refund_records.exists()):
        return False
    from .models import OrderOriginalShareTransfer, OrderPlatformSubsidy, OrderSegmentSettlementManifest
    if (OrderPlatformSubsidy.objects.filter(order=order).exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
            or OrderSegmentSettlementManifest.objects.filter(order=order).exists()):
        return False
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order):
        return False
    from .direct_payment_models import DirectPaymentIntent
    return not DirectPaymentIntent.objects.filter(order=order).exists()


# The receipt key is global across tables. Encode the report and review round
# instead of colliding with raw segment-submission IDs. Rejected reviews use a
# separate outcome namespace, so flipping status cannot invent an approval.
_LEGACY_REPORT_BASE = 1 << 62
_LEGACY_REPORT_REJECT_OFFSET = 1 << 61
_LEGACY_REPORT_ROUND_STRIDE = 1024
_LEGACY_REPORT_MAX_ID = (1 << 63) - 1


def _legacy_report_bounds(report_id):
    if type(report_id) is not int or report_id < 1:
        raise ClockConflict('旧报单时钟主体编号无效')
    start = _LEGACY_REPORT_BASE + report_id * _LEGACY_REPORT_ROUND_STRIDE
    if start + _LEGACY_REPORT_ROUND_STRIDE - 1 + _LEGACY_REPORT_REJECT_OFFSET > _LEGACY_REPORT_MAX_ID:
        raise ClockConflict('旧报单时钟主体编号超出可核对范围')
    return start


def _legacy_report_subject_id(report_id, round_id):
    start = _legacy_report_bounds(report_id)
    if type(round_id) is not int or not 1 <= round_id < _LEGACY_REPORT_ROUND_STRIDE:
        raise ClockConflict('旧报单轮次超出可核对范围')
    return start + round_id


def _legacy_report_latest_receipt(order, report_id, operation):
    from wallet.models import SettlementClockEvidence
    start = _legacy_report_bounds(report_id)
    return SettlementClockEvidence.objects.filter(
        order=order, subject_type='SUBMISSION', operation=operation,
        subject_id__gt=start, subject_id__lt=start + _LEGACY_REPORT_ROUND_STRIDE,
    ).order_by('-subject_id').first()


def legacy_report_next_subject_id(order, report_id):
    latest = _legacy_report_latest_receipt(order, report_id, 'SUBMIT')
    start = _legacy_report_bounds(report_id)
    round_id = 0 if latest is None else latest.subject_id - start
    return _legacy_report_subject_id(report_id, round_id + 1)


def verify_legacy_report_submission(order, report, clock):
    clock = require_clock(clock)
    latest = _legacy_report_latest_receipt(order, report.pk, 'SUBMIT')
    if (report.order_id != order.pk or not report.submitted_at or latest is None
            or latest.observed_at != report.submitted_at):
        raise ClockConflict('旧报单原提交回执缺失或时间已变化')
    validate_receipt(latest.clock_evidence, latest.observed_at, clock)
    return latest.subject_id


def legacy_report_review_subject_id(order, report, clock, *, approved):
    subject_id = verify_legacy_report_submission(order, report, clock)
    return subject_id if approved else subject_id + _LEGACY_REPORT_REJECT_OFFSET


def _verify_legacy_report_review(order, report, clock):
    subject_id = verify_legacy_report_submission(order, report, clock)
    if (report.status != ProviderReport.Status.APPROVED or not report.audited_at
            or not (report.auditor_id or report.auditor_account_id)
            or report.audited_at < report.submitted_at):
        raise ClockConflict('旧报单原审核事实不完整')
    review = clock_receipt(order, 'SUBMISSION', subject_id, 'REVIEW')
    if review is None or review.observed_at != report.audited_at:
        raise ClockConflict('旧报单原审核通过回执缺失或时间已变化')
    validate_receipt(review.clock_evidence, review.observed_at, clock)


def verify_legacy_service_reports(order, clock, *, require_approved):
    """Verify current report rounds and their original provider economics."""
    if not legacy_report_clock_chain_enabled(order):
        return
    clock = require_clock(clock)
    from .services import required_report_providers
    required = required_report_providers(order)
    reports = list(order.provider_reports.filter(provider_id__in=required).order_by('pk'))
    by_provider = {}
    for report in reports:
        if report.provider_id in by_provider:
            raise SettlementError('旧报单陪玩身份重复，需人工核对')
        by_provider[report.provider_id] = report
    assignments = {row.provider_id: row for row in order.providers.all()}
    settled_ids = set(order.providers.filter(settled_at__isnull=False).values_list('provider_id', flat=True))
    for user_id in required:
        report = by_provider.get(user_id)
        if report is None or report.status != ProviderReport.Status.APPROVED:
            if require_approved or user_id in settled_ids:
                raise SettlementError('旧报单审核状态与原结算事实不一致')
            continue
        _verify_legacy_report_review(order, report, clock)
        assignment = assignments.get(user_id)
        if assignment is not None:
            expected = (assignment.provider_account_id, assignment.settlement_base,
                        assignment.provider_income, assignment.commission_rate)
        elif not assignments and order.provider_id == user_id and order.required_provider_count == 1:
            expected = (order.provider_account_id, order.amount, order.provider_income, order.commission_rate)
        else:
            raise SettlementError('旧报单陪玩不属于本订单')
        actual = (report.provider_account_id, report.amount, report.payout_amount, report.commission_rate)
        if report.order_id != order.pk or actual != expected:
            raise SettlementError('旧报单订单、陪玩或金额与原份额不一致')


def settle_due_service_shares(order, *, operator=None, operator_account=None, trace_id=None):
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order):
        raise SettlementError('积分订单收益结算尚未开放，请联系客服核对')
    from stores.db import transaction
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('服务收益结算必须在门店业务事务内执行')
    from engagement.confirmation import aftersale_wait_elapsed
    from wallet.income import hold_provider_income
    from wallet.notifier import notify_wallet_update
    from .financial_lock import lock_order_money
    from .services import _credit_wallet, missing_report_evidence, refresh_escort_status
    from common.logging_utils import new_trace_id, log_money_event

    if order.settled_at:
        return []
    clock = require_clock()
    if not aftersale_wait_elapsed(order, clock=clock):
        return []
    if order.status != order.Status.COMPLETED or order.payment_status != order.PaymentStatus.PAID:
        return []
    if (order.service_aftersale_hours is None or order.service_freeze_hours is None
            or order.service_deposit_percent is None or not 0 <= order.service_deposit_percent <= 100):
        raise SettlementError('订单结算时限或押金比例快照待核对')
    if not order.customer_account_id:
        raise SettlementError('订单缺少老板业务账号，请先核对')
    lock_order_money()
    customer_wallet = get_wallet(account_id=order.customer_account_id, user_id=order.customer_id, for_update=True)
    payments = list(order.transactions.filter(tx_type=Transaction.TxType.PAY, status=Transaction.Status.SUCCESS,
                                               reversal_of__isnull=True).order_by('pk'))
    if (len(payments) != 1 or payments[0].wallet_id != customer_wallet.pk or payments[0].amount != -order.amount
            or payments[0].balance_after != payments[0].balance_before + payments[0].amount):
        raise SettlementError('订单付款流水不完整，请先核对')
    if order.refund_records.exists():
        raise SettlementError('退款后的服务收益须先核对，暂不能自动结算')

    rows = list(order.providers.select_for_update().order_by('pk'))
    if rows:
        relevant = [row for row in rows if row.settlement_base > 0 or not row.released_at]
        if not relevant or any(not row.provider_id for row in relevant):
            raise SettlementError('订单陪玩份额或身份待核对')
        participants = [(row, row.provider_id, row.provider_account_id, row.provider_income) for row in relevant]
    elif order.provider_id and order.required_provider_count == 1:
        participants = [(None, order.provider_id, order.provider_account_id, order.provider_income)]
    else:
        raise SettlementError('订单陪玩份额待核对')

    planned = [item[3] for item in participants]
    if sum(planned) != order.provider_income or (rows and sum(item[0].settlement_base for item in participants) != order.amount):
        raise SettlementError('订单陪玩份额与订单快照不一致，请先核对')
    total = assert_order_conserved(order.amount, planned,
        order.inviter_commission if order.inviter_id or order.inviter_account_id else 0, order.shop_income)
    if total != order.amount:
        raise SettlementError('订单分账与实付不一致，请先核对')
    reports = {report.provider_id: report for report in order.provider_reports.filter(
        provider_id__in=[item[1] for item in participants])}
    verify_legacy_service_reports(order, clock, require_approved=False)
    from .aftersale_scope import applies_to
    open_cases = list(order.aftersales.filter(status='OPEN'))
    now = clock.at
    eligible = []
    for row, user_id, account_id, amount in participants:
        report = reports.get(user_id)
        if row and row.settled_at:
            # Existing partial credits must remain attributable before another
            # share can be paid or the order can be finalized.
            credits = order.transactions.filter(tx_type=Transaction.TxType.INCOME,
                status=Transaction.Status.SUCCESS, reversal_of__isnull=True,
                remark=f'订单收入：{order.order_no}', wallet__user_id=user_id)
            if account_id:
                mapped = LegacyAccountMap.objects.filter(legacy_user_id=user_id).values_list('account_id', flat=True).first()
                if mapped != account_id:
                    raise SettlementError('已结算陪玩账号归属待核对')
                credits = credits.filter(wallet__account_id=account_id)
            if (amount > 0 and (credits.count() != 1 or not credits.filter(amount=amount).exists())) or (amount == 0 and credits.exists()):
                raise SettlementError('已结算陪玩收益流水待核对')
            for credit in credits:
                if credit.balance_after != credit.balance_before + credit.amount or credit.reversals.exists():
                    raise SettlementError('已结算陪玩收益流水待核对')
            continue
        if any(applies_to(case, account_id) for case in open_cases):
            continue
        if not report or report.status != ProviderReport.Status.APPROVED or not report.submitted_at:
            continue
        if report.submitted_at + timedelta(hours=order.service_freeze_hours) > now:
            continue
        if (report.payout_amount != amount or (report.provider_account_id and account_id != report.provider_account_id)
                or missing_report_evidence(report)):
            raise SettlementError('陪玩报单金额或履约凭证待核对')
        if order.transactions.filter(tx_type=Transaction.TxType.INCOME, status=Transaction.Status.SUCCESS,
                remark=f'订单收入：{order.order_no}', wallet__user_id=user_id).exists():
            raise SettlementError('该陪玩已有收入流水但结算状态不一致，请先核对')
        eligible.append((row, user_id, account_id, amount))

    trace_id = trace_id or new_trace_id()
    ids = sorted({item[1] for item in eligible})
    profiles = {profile.user_id: profile for profile in EscortProfile.objects.select_for_update().filter(user_id__in=ids).order_by('user_id')}
    settled_users, settled_accounts = [], []
    for row, user_id, account_id, amount in eligible:
        income = _credit_wallet(order=order, amount=amount, tx_type=Transaction.TxType.INCOME,
            remark=f'订单收入：{order.order_no}', event='order.settle.provider', trace_id=trace_id,
            user_id=user_id, account_id=account_id)
        hold_provider_income(income, profile=profiles.get(user_id), hold_hours=0,
            deposit_percent=order.service_deposit_percent, apply_without_hold=True)
        if row:
            row.settled_at = now
            row.save(update_fields=['settled_at'])
            if not amount:
                from wallet.refund_recovery_order import close_for_zero_service
                close_for_zero_service(profiles.get(user_id), row, now=clock)
        settled_users.append(user_id)
        if account_id:
            settled_accounts.append(account_id)
    if settled_users:
        EscortProfile.objects.filter(user_id__in=settled_users).update(completed_order_count=F('completed_order_count') + 1)
        refresh_escort_status(provider_ids=settled_users, account_ids=settled_accounts, exclude_order_ids=[order.pk])

    # A single-provider legacy-shaped new order finalizes with its one credit;
    # otherwise all relevant participant rows must be settled before shop and
    # referral are posted. Each operation is protected by this same order lock.
    all_settled = not open_cases and (bool(eligible) if not rows else all(row.settled_at for row, _, _, _ in participants))
    if all_settled:
        from django.db.models import Q
        if order.transactions.filter(status=Transaction.Status.SUCCESS, reversal_of__isnull=True).filter(
                Q(tx_type=Transaction.TxType.SHOP_INCOME) |
                Q(tx_type=Transaction.TxType.INCOME, remark=f'推荐分佣：{order.order_no}')).exists():
            raise SettlementError('订单已有门店或推荐收益流水，请先核对')
        if order.inviter_id or order.inviter_account_id:
            inviter = _credit_wallet(order=order, amount=order.inviter_commission, tx_type=Transaction.TxType.INCOME,
                remark=f'推荐分佣：{order.order_no}', event='order.settle.inviter', trace_id=trace_id,
                user_id=order.inviter_id, account_id=order.inviter_account_id)
            if inviter:
                from .referral_evidence import record_referral_principal
                record_referral_principal(inviter)
                transaction.on_commit(lambda: notify_wallet_update(inviter.wallet))
        _credit_wallet(order=order, amount=order.shop_income, tx_type=Transaction.TxType.SHOP_INCOME,
            remark=f'平台收入：{order.order_no}', event='order.settle.platform', trace_id=trace_id, platform=True)
        order.settled_at = now
        order.save(update_fields=['settled_at'])
        from engagement.services import award_order_points
        award_order_points(order)
        log_money_event('order.settle', account_id=getattr(operator_account, 'pk', None),
            user_id=getattr(operator, 'pk', None), order_no=order.order_no, order_id=order.pk,
            amount=order.amount, trace_id=trace_id, operator_id=getattr(operator, 'pk', None),
            reason=f'providers={settled_users}')
    return settled_users
