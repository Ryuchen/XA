"""Internal settlement for a completed, linked pair of negotiations.

The settlement entry invokes this for linked receipts. The caller must lock the
order and perform all writes in one tenant transaction. Creating links remains
behind the follow-up execution gate.
"""
from datetime import timedelta
from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from club_accounts.models import LegacyAccountMap
from users.models import EscortProfile
from wallet.models import ProviderReport
from .financial_lock import lock_existing_order_money
from .models import NegotiationFollowupLink
from .negotiation_followup_accounting import verify_followup_accounting, verify_followup_termination
from .settlement import SettlementError


def settle_followup_internal(order, link, *, now=None, trace_id=None):
    """Pay each person's verified net share after their own overlapping clocks."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('追加协商结算必须在门店事务内执行')
    lock_existing_order_money()
    link = NegotiationFollowupLink.objects.select_for_update().select_related(
        'previous', 'execution', 'order').get(pk=link.pk)
    if link.order_id != order.pk:
        raise SettlementError('追加协商订单归属待核对')
    try:
        previous_share, current_share = verify_followup_accounting(link)
        verify_followup_termination(link)
    except (ValidationError, ValueError) as exc:
        raise SettlementError('追加协商凭证待核对') from exc
    previous, current = link.previous, link.execution
    funding = list(order.funding_allocations.select_for_update().order_by('pk'))
    if (not funding or sum(row.amount for row in funding) != order.amount
            or any(row.kind in ('GIFT', 'COMPENSATION') and row.amount > row.refunded_amount for row in funding)):
        raise SettlementError('追加协商的赠送或赔付来源须先通过门店补资结算')
    if (previous.finalized_at or previous.shop_transaction_id or previous.inviter_transaction_id
            or previous.unaffected_report_evidence or current.unaffected_report_evidence
            or order.service_clock_version is None or order.service_aftersale_hours is None
            or order.service_freeze_hours is None or order.service_deposit_percent is None
            or not 0 <= order.service_deposit_percent <= 100):
        raise SettlementError('追加协商结算快照待核对')
    from .negotiation_settlement import _check_credit, _credit, _verify_settled_assignment
    from .aftersale_scope import applies_to
    from .services import missing_report_evidence, refresh_escort_status
    from wallet.income import hold_provider_income
    from wallet.refund_recovery_order import close_for_zero_negotiated
    pairs = [(previous, previous_share), (current, current_share)]
    rows = list(order.providers.select_for_update().order_by('pk'))
    if len(rows) != 2 or {row.pk for row in rows} != {share.assignment_id for _, share in pairs}:
        raise SettlementError('追加协商陪玩份额待核对')
    reports = {row.provider_account_id:row for row in ProviderReport.objects.select_for_update().filter(order=order)}
    open_cases = list(order.aftersales.filter(status='OPEN'))
    now = now or timezone.now()
    if now < order.completed_at:
        return []
    settled_users, settled_accounts = [], []
    for receipt, share in pairs:
        assignment = next(row for row in rows if row.pk == share.assignment_id)
        account_id, user_id = assignment.provider_account_id, assignment.provider_id
        if LegacyAccountMap.objects.filter(account_id=account_id, legacy_user_id=user_id).count() != 1:
            raise SettlementError('陪玩结算身份待核对')
        report = reports.get(account_id)
        if assignment.settled_at:
            _verify_settled_assignment(order, receipt, assignment, share, report)
            continue
        if share.settled_at:
            raise SettlementError('追加协商陪玩结算状态不一致')
        # A post-completion agreement cannot restart B's service-completion
        # clock; the independent first termination retains its original clock.
        aftersale_start = (order.completed_at if receipt.pk == current.pk
                           and link.projected_ledger.get('completion_evidence') is not None else receipt.accepted_at)
        if aftersale_start + timedelta(hours=order.service_aftersale_hours) > now:
            continue
        if any(applies_to(case, account_id) for case in open_cases):
            continue
        if not report or report.status != ProviderReport.Status.APPROVED or not report.submitted_at:
            continue
        if report.submitted_at + timedelta(hours=order.service_freeze_hours) > now:
            continue
        if (report.provider_id != user_id or report.payout_amount != assignment.provider_income
                or missing_report_evidence(report)):
            raise SettlementError('陪玩报单或原收益凭证待核对')
        if order.transactions.filter(tx_type='INCOME', status='SUCCESS', wallet__account_id=account_id,
                                     remark=f'订单收入：{order.order_no}').exists():
            raise SettlementError('陪玩已有未关联的收益流水')
        profile = EscortProfile.objects.select_for_update().filter(account_id=account_id, user_id=user_id).first()
        if not profile:
            raise SettlementError('陪玩资料与收益归属不一致')
        income = _credit(order, amount=share.retained_income, kind='INCOME', remark=f'订单收入：{order.order_no}',
                         event='order.settle.negotiated', trace_id=trace_id, account_id=account_id, user_id=user_id)
        hold_provider_income(income, profile=profile, hold_hours=0,
                             deposit_percent=order.service_deposit_percent, apply_without_hold=True)
        assignment.settled_at = now; assignment.save(update_fields=['settled_at'])
        share.settled_at = now; share.income_transaction = income
        share.settled_report = report; share.report_submitted_at = report.submitted_at
        share.save(update_fields=['settled_at', 'income_transaction', 'settled_report', 'report_submitted_at'])
        if not income:
            close_for_zero_negotiated(profile, share)
        settled_users.append(user_id); settled_accounts.append(account_id)
    if settled_users:
        EscortProfile.objects.filter(user_id__in=settled_users).update(completed_order_count=F('completed_order_count') + 1)
        refresh_escort_status(provider_ids=settled_users, account_ids=settled_accounts, exclude_order_ids=[order.pk])
    if not open_cases and all(row.settled_at for row in rows):
        inviter_income = link.projected_ledger['inviter_income_units']
        store_income = link.projected_ledger['store_income_units']
        if current.finalized_at or order.settled_at:
            if current.finalized_at != order.settled_at:
                raise SettlementError('追加协商订单最终结算状态不一致')
            _check_credit(current.shop_transaction, order=order, amount=store_income, kind='SHOP_INCOME', platform=True)
            for receipt in (previous, current):
                _check_credit(receipt.rounding_transaction, order=order, amount=receipt.rounding_to_store_units,
                              kind='SHOP_INCOME', platform=True)
            _check_credit(current.inviter_transaction, order=order, amount=inviter_income,
                          account_id=order.inviter_account_id, user_id=order.inviter_id)
        else:
            if order.transactions.filter(Q(tx_type='SHOP_INCOME') | Q(tx_type='INCOME', remark=f'推荐分佣：{order.order_no}'),
                                         status='SUCCESS', reversal_of__isnull=True).exists():
                raise SettlementError('门店或推荐收益已有未关联流水')
            if inviter_income and not (order.inviter_id or order.inviter_account_id):
                raise SettlementError('推荐收益身份待核对')
            current.inviter_transaction = _credit(order, amount=inviter_income, kind='INCOME',
                remark=f'推荐分佣：{order.order_no}', event='order.settle.inviter', trace_id=trace_id,
                account_id=order.inviter_account_id, user_id=order.inviter_id)
            if current.inviter_transaction and current.inviter_transaction.wallet.account_id:
                from club_accounts.models import ClubAccount
                if ClubAccount.objects.filter(pk=current.inviter_transaction.wallet.account_id,
                        account_type=ClubAccount.AccountType.BOSS).exists():
                    from engagement.models import PointFundingLot
                    PointFundingLot.objects.create(account_id=current.inviter_transaction.wallet.account_id,
                        source_key=f'negotiation-referral:{current.pk}', kind=PointFundingLot.Kind.PREPAID,
                        remaining=inviter_income)
            current.shop_transaction = _credit(order, amount=store_income, kind='SHOP_INCOME',
                remark=f'平台收入：{order.order_no}', event='order.settle.platform', trace_id=trace_id, platform=True)
            for receipt in (previous, current):
                receipt.rounding_transaction = _credit(order, amount=receipt.rounding_to_store_units,
                    kind='SHOP_INCOME', remark=f'退款取整差额：{order.order_no}（协商 {receipt.pk}）',
                    event='order.settle.rounding', trace_id=trace_id, platform=True)
                receipt.save(update_fields=['rounding_transaction'])
            current.finalized_at = now
            current.save(update_fields=['inviter_transaction', 'shop_transaction', 'finalized_at'])
            order.settled_at = now; order.save(update_fields=['settled_at'])
    return settled_users
