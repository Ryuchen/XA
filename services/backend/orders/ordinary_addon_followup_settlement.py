"""Settle the remaining person and final destinations of an add-on follow-up."""
from datetime import timedelta

from django.conf import settings
from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .points_checkout_test import require_isolated_checkout
from .settlement import SettlementError


def is_followup(link):
    return isinstance(link.projected_ledger, dict) and link.projected_ledger.get('ordinary_addon_followup_version') == 1


def funding_status_locked(order, link):
    """Verify current allocations against the post-refund whole-order liability."""
    from .ordinary_addon_followup_posting import verify_posting_locked
    from .negotiation_followup_accounting import verify_followup_termination
    from .points_funding_evidence import verified_order_allocations_locked
    from .points_funding_plan import PointsFundingPlanError
    from .addon_funding_release import addon_release_totals
    try:
        link = verify_posting_locked(link)
        verify_followup_termination(link)
        allocations = verified_order_allocations_locked(order)
        released = addon_release_totals(allocations)
    except (ValidationError, ValueError, PointsFundingPlanError) as exc:
        raise SettlementError('追加售后退款或门店补资凭证待核对') from exc
    if link.order_id != order.pk:
        raise SettlementError('追加售后订单归属待核对')
    allocated = sum(row.amount - released[row.pk] for row in allocations)
    estimate = link.projected_ledger['estimate']
    required = estimate['required_funding']
    if allocated > required:
        raise SettlementError('追加售后门店补资存在未释放余额')
    from .points_funding_evidence import VerifiedOrderFunding
    return link, VerifiedOrderFunding(required, allocated, required - allocated, allocated == required)


def _load_locked(order, link, *, require_funded=True):
    link, funding = funding_status_locked(order, link)
    if require_funded and not funding.fully_funded:
        raise SettlementError('追加售后门店补资尚未足额')
    settlement = order.points_settlement
    economics = settlement.ordinary_refund_addon_evidence.facts
    estimate = link.projected_ledger['estimate']
    facts = [item for item in economics['people'] if item['account_id'] == estimate['account_id']]
    if len(facts) != 1:
        raise SettlementError('追加售后陪玩份额待核对')
    return link, settlement, economics, facts[0]


def _verify_person(order, link, settlement, economics, fact):
    from wallet.models import ProviderReport
    from .addon_settlement import verify_addon_records
    from .negotiation_settlement import _check_credit
    from .ordinary_refunded_addon_settlement import _parts
    from .services import missing_report_evidence
    share = link.execution.shares.select_for_update().get(account_id=fact['account_id'])
    assignment = order.providers.select_for_update().get(pk=fact['assignment_id'])
    person = settlement.persons.select_for_update().filter(segment_id=fact['segment_id']).select_related(
        'report', 'income_transaction__wallet').first()
    if not person:
        if assignment.settled_at or share.settled_at or share.income_transaction_id:
            raise SettlementError('追加售后待结算状态不一致')
        return None
    report = person.report
    if (person.amount != share.retained_income or person.income_transaction_id != share.income_transaction_id
            or share.assignment_id != assignment.pk or assignment.provider_account_id != fact['account_id']
            or assignment.provider_id != fact['user_id'] or person.segment.provider_account_id != fact['account_id']
            or person.segment.attribution.order_id != order.pk
            or person.settled_at != assignment.settled_at or person.settled_at != share.settled_at
            or share.settled_report_id != report.pk or share.report_submitted_at != report.submitted_at
            or report.order_id != order.pk or report.provider_account_id != fact['account_id']
            or report.provider_id != fact['user_id'] or report.status != ProviderReport.Status.APPROVED
            or report.payout_amount != assignment.provider_income or not report.submitted_at or not report.audited_at
            or not report.submitted_at <= report.audited_at <= person.settled_at
            or person.settled_at < order.completed_at + timedelta(hours=order.service_aftersale_hours)
            or person.settled_at < report.submitted_at + timedelta(hours=order.service_freeze_hours)
            or person.settled_at > timezone.now() or missing_report_evidence(report)):
        raise SettlementError('追加售后陪玩报单或收益凭证异常')
    _check_credit(person.income_transaction, order=order, amount=person.amount,
                  account_id=fact['account_id'], user_id=fact['user_id'])
    credits = list(order.transactions.filter(tx_type='INCOME', status='SUCCESS',
        wallet__account_id=fact['account_id'], wallet__user_id=fact['user_id'],
        remark=f'订单收入：{order.order_no}'))
    if len(credits) != (1 if person.amount else 0) or (credits and credits[0].pk != person.income_transaction_id):
        raise SettlementError('追加售后主单收益流水异常')
    parts = [part for part in _parts(economics) if part.segment_id == fact['segment_id']]
    paid = verify_addon_records(order, settlement, parts, [person], segment_ids=[fact['segment_id']])
    if paid != {(part.payment_id, part.segment_id) for part in parts}:
        raise SettlementError('追加售后加购收益凭证不完整')
    for addon in settlement.addon_settlements.filter(segment_id=fact['segment_id']).select_related('income_transaction__wallet'):
        tx = addon.income_transaction
        if tx and (tx.wallet.user_id != fact['user_id'] or tx.reversal_of_id or tx.reversals.exists()):
            raise SettlementError('追加售后加购收益流水异常')
    from wallet.models import RefundDepositRecoveryPermit
    permit = RefundDepositRecoveryPermit.objects.filter(zero_ordinary_addon_person=person).first()
    if permit and (person.amount or person.income_transaction_id or permit.account_id != fact['account_id']
            or permit.order_id != order.pk or permit.profile.account_id != fact['account_id']
            or not permit.closed_at or permit.close_reason != 'ZERO_ADDON_SETTLED'
            or permit.income_application_id or permit.zero_income_settlement_id
            or permit.zero_negotiated_share_id or permit.zero_points_refund_person_id
            or any(part.provider_income_units for part in parts)):
        raise SettlementError('追加售后零收益补差凭证异常')
    return person


def verify_finalized_locked(order, link):
    link, settlement, economics, fact = _load_locked(order, link)
    person = _verify_person(order, link, settlement, economics, fact)
    from .ordinary_refunded_addon_settlement import _verify_paid_people
    prior = [row['segment_id'] for row in economics['people'] if row['affected']]
    prior_people = _verify_paid_people(order, settlement, link.previous, economics, segment_ids=prior)
    current, estimate = link.execution, link.projected_ledger['estimate']
    if not person or len(prior_people) != 1 or not order.settled_at or current.finalized_at != order.settled_at:
        raise SettlementError('追加售后整单结算凭证不完整')
    from .negotiation_settlement import _check_credit
    _check_credit(current.inviter_transaction, order=order, amount=estimate['inviter_income'],
                  account_id=order.inviter_account_id, user_id=order.inviter_id)
    addon_refunds = list(order.addon_refunds.select_related('rounding_transaction').order_by('pk'))
    rounding = (link.previous.rounding_to_store_units + current.rounding_to_store_units
                + sum(row.rounding_surplus for row in addon_refunds))
    if rounding > estimate['store_income']:
        raise SettlementError('追加售后取整差额资金待核对')
    _check_credit(current.shop_transaction, order=order, amount=estimate['store_income'] - rounding,
                  kind='SHOP_INCOME', platform=True)
    for receipt in (link.previous, current):
        _check_credit(receipt.rounding_transaction, order=order, amount=receipt.rounding_to_store_units,
                      kind='SHOP_INCOME', platform=True)
    for refund in addon_refunds:
        _check_credit(refund.rounding_transaction, order=order, amount=refund.rounding_surplus,
                      kind='SHOP_INCOME', platform=True)
    addon_contribution = max(0, sum(row['principal_units'] + row['recovery_units'] - row['provider_units']
                                    for row in economics['addon_history']))
    if (order.settlement_store_funding != estimate['required_funding']
            or order.settlement_addon_contribution != addon_contribution):
        raise SettlementError('追加售后最终补资记录不一致')
    expected = {pk for pk in (current.inviter_transaction_id, current.shop_transaction_id,
                              link.previous.rounding_transaction_id, current.rounding_transaction_id) if pk}
    expected.update(row.rounding_transaction_id for row in addon_refunds if row.rounding_transaction_id)
    actual = set(order.transactions.filter(status='SUCCESS').filter(
        Q(tx_type='SHOP_INCOME') | Q(tx_type='INCOME', remark=f'推荐分佣：{order.order_no}')).values_list('pk', flat=True))
    if actual != expected:
        raise SettlementError('追加售后推荐或门店收入流水不一致')
    if current.inviter_transaction_id and order.inviter_account_id:
        from club_accounts.models import ClubAccount
        from engagement.models import PointFundingLot
        if (ClubAccount.objects.filter(pk=order.inviter_account_id, account_type=ClubAccount.AccountType.BOSS).exists()
                and not PointFundingLot.objects.filter(account_id=order.inviter_account_id,
                    source_key=f'negotiation-referral:{current.pk}', kind=PointFundingLot.Kind.PREPAID,
                    remaining__lte=estimate['inviter_income']).exists()):
            raise SettlementError('追加售后推荐佣金本金来源待核对')
    return person


def _settle_prior_due(order, link, settlement, economics, *, trace_id=None):
    """Release the first participant after the follow-up, when their own clocks permit."""
    from .ordinary_refunded_addon_settlement import _parts, _verify_paid_people
    from .aftersale_scope import applies_to
    from .services import _credit_wallet, missing_report_evidence, refresh_escort_status
    from .addon_settlement import settle_person_addons
    from .models import OrderPointsSettlementPerson, OrderSegmentFinancialSnapshot
    from wallet.models import ProviderReport, RefundDepositDeficit, RefundDepositRecoveryPermit, Transaction
    from wallet.income import hold_provider_income
    from users.models import EscortProfile

    facts = [row for row in economics['people'] if row['affected']]
    if len(facts) != 1:
        raise SettlementError('前次售后陪玩份额待核对')
    fact = facts[0]
    paid = _verify_paid_people(order, settlement, link.previous, economics, segment_ids=[fact['segment_id']])
    if paid:
        return []
    assignment = order.providers.select_for_update().get(pk=fact['assignment_id'])
    share = link.previous.shares.select_for_update().get(account_id=fact['account_id'])
    report = ProviderReport.objects.select_for_update().filter(order=order, provider_account_id=fact['account_id']).first()
    now = timezone.now()
    from .negotiation_settlement import negotiated_aftersale_start
    aftersale_start = negotiated_aftersale_start(order, link.previous, assignment, share)
    if (any(applies_to(case, fact['account_id']) for case in order.aftersales.filter(status='OPEN'))
            or not order.completed_at or now < order.completed_at or not aftersale_start
            or now < aftersale_start + timedelta(hours=order.service_aftersale_hours)
            or not report or report.status != ProviderReport.Status.APPROVED or not report.submitted_at
            or now < report.submitted_at + timedelta(hours=order.service_freeze_hours)):
        return []
    if (assignment.settled_at or share.settled_at or share.income_transaction_id
            or report.provider_id != fact['user_id'] or not report.audited_at
            or not report.submitted_at <= report.audited_at <= now
            or report.amount != assignment.settlement_base or report.commission_rate != assignment.commission_rate
            or report.payout_amount != assignment.provider_income or missing_report_evidence(report)
            or order.transactions.filter(tx_type='INCOME', status='SUCCESS', wallet__account_id=fact['account_id'],
                remark=f'订单收入：{order.order_no}').exists()):
        raise SettlementError('前次售后陪玩报单或收益凭证待核对')
    profile = EscortProfile.objects.select_for_update().filter(account_id=fact['account_id'], user_id=fact['user_id']).first()
    if not profile:
        raise SettlementError('前次售后陪玩身份待核对')
    from wallet.refund_deposit import enabled as deficit_enabled
    from wallet.refund_recovery_order import tracking_enabled
    gap = RefundDepositDeficit.objects.filter(profile=profile, remaining__gt=0).exists()
    permit = RefundDepositRecoveryPermit.objects.filter(profile=profile, closed_at__isnull=True).exists()
    if ((gap or permit) and not deficit_enabled()) or (permit and not tracking_enabled()):
        raise SettlementError('前次售后押金缺口或补差机会尚未接通')
    snapshot = OrderSegmentFinancialSnapshot.objects.select_for_update().get(segment_id=fact['segment_id'])
    income = _credit_wallet(order=order, amount=fact['main_income'], tx_type=Transaction.TxType.INCOME,
        remark=f'订单收入：{order.order_no}', event='order.ordinary_addon_followup.prior_provider', trace_id=trace_id,
        user_id=fact['user_id'], account_id=fact['account_id'], log_after_commit=True)
    hold_provider_income(income, profile=profile, hold_hours=0, deposit_percent=order.service_deposit_percent,
                         apply_without_hold=True)
    person = OrderPointsSettlementPerson.objects.create(settlement=settlement, segment=snapshot.segment,
        report=report, income_transaction=income, amount=fact['main_income'], settled_at=now)
    parts = _parts(economics)
    settle_person_addons(order, settlement, parts, snapshot=snapshot, assignment=assignment,
                         report=report, profile=profile, now=now, trace_id=trace_id)
    assignment.settled_at = now
    assignment.save(update_fields=['settled_at'])
    share.settled_at, share.income_transaction, share.settled_report, share.report_submitted_at = now, income, report, report.submitted_at
    share.save(update_fields=['settled_at', 'income_transaction', 'settled_report', 'report_submitted_at'])
    if permit and not fact['main_income'] and not any(
            part.provider_account_id == fact['account_id'] and part.provider_income_units for part in parts):
        from wallet.refund_recovery_order import close_for_zero_ordinary_addon
        close_for_zero_ordinary_addon(profile, person)
    EscortProfile.objects.filter(pk=profile.pk).update(completed_order_count=F('completed_order_count') + 1)
    refresh_escort_status(provider_ids=[fact['user_id']], account_ids=[fact['account_id']], exclude_order_ids=[order.pk])
    from .notifier import notify_report_update
    transaction.on_commit(lambda: notify_report_update(report))
    return [fact['user_id']]


def settle_locked(order, link, *, trace_id=None):
    from .ordinary_followup_capability import require_access, stage_enabled
    require_access()
    if (not transaction.get_connection().in_atomic_block
            or not stage_enabled('ORDINARY_ADDON_FOLLOWUP_SETTLEMENT_INTERNAL_TESTING')):
        raise SettlementError('带加购的追加售后结算尚未开放')
    link, funding = funding_status_locked(order, link)
    if not funding.fully_funded:
        return []
    link, settlement, economics, fact = _load_locked(order, link)
    person = _verify_person(order, link, settlement, economics, fact)
    if order.settled_at:
        verify_finalized_locked(order, link)
        return []
    current, estimate = link.execution, link.projected_ledger['estimate']
    if current.finalized_at or current.inviter_transaction_id or current.shop_transaction_id:
        raise SettlementError('追加售后最终收益状态异常')
    settled = _settle_prior_due(order, link, settlement, economics, trace_id=trace_id)
    if not person:
        from wallet.models import ProviderReport, RefundDepositDeficit, RefundDepositRecoveryPermit, Transaction
        from .aftersale_scope import applies_to
        from .services import missing_report_evidence
        assignment = order.providers.select_for_update().get(pk=fact['assignment_id'])
        if order.transactions.filter(tx_type='INCOME', status='SUCCESS', wallet__account_id=fact['account_id'],
                                     remark=f'订单收入：{order.order_no}').exists():
            raise SettlementError('追加售后陪玩已有未关联收益流水')
        report = ProviderReport.objects.select_for_update().filter(order=order, provider_account_id=fact['account_id']).first()
        now = timezone.now()
        if (any(applies_to(case, fact['account_id']) for case in order.aftersales.filter(status='OPEN'))
                or now < order.completed_at + timedelta(hours=order.service_aftersale_hours)
                or not report or report.status != ProviderReport.Status.APPROVED or not report.submitted_at
                or now < report.submitted_at + timedelta(hours=order.service_freeze_hours)):
            return settled
        if (report.provider_id != fact['user_id'] or not report.audited_at
                or not report.submitted_at <= report.audited_at <= now or report.amount != assignment.settlement_base
                or report.commission_rate != assignment.commission_rate or report.payout_amount != assignment.provider_income
                or missing_report_evidence(report)):
            raise SettlementError('追加售后陪玩报单凭证待核对')
        from users.models import EscortProfile
        profile = EscortProfile.objects.select_for_update().filter(account_id=fact['account_id'], user_id=fact['user_id']).first()
        if not profile:
            raise SettlementError('追加售后陪玩账号待核对')
        from wallet.refund_deposit import enabled as deficit_enabled
        from wallet.refund_recovery_order import tracking_enabled
        gap = RefundDepositDeficit.objects.filter(profile=profile, remaining__gt=0).exists()
        permit = RefundDepositRecoveryPermit.objects.filter(profile=profile, closed_at__isnull=True).exists()
        if ((gap or permit) and not deficit_enabled()) or (permit and not tracking_enabled()):
            raise SettlementError('追加售后押金缺口或补差机会尚未接通')
        from .models import OrderPointsSettlementPerson, OrderSegmentFinancialSnapshot
        from .services import _credit_wallet
        from wallet.income import hold_provider_income
        from .addon_settlement import settle_person_addons
        from .ordinary_refunded_addon_settlement import _parts
        snapshot = OrderSegmentFinancialSnapshot.objects.select_for_update().get(segment_id=fact['segment_id'])
        income = _credit_wallet(order=order, amount=current.shares.get(account_id=fact['account_id']).retained_income,
            tx_type=Transaction.TxType.INCOME, remark=f'订单收入：{order.order_no}',
            event='order.ordinary_addon_followup.provider', trace_id=trace_id,
            user_id=fact['user_id'], account_id=fact['account_id'], log_after_commit=True)
        hold_provider_income(income, profile=profile, hold_hours=0, deposit_percent=order.service_deposit_percent,
                             apply_without_hold=True)
        share = current.shares.get(account_id=fact['account_id'])
        person = OrderPointsSettlementPerson.objects.create(settlement=settlement, segment=snapshot.segment,
            report=report, income_transaction=income, amount=share.retained_income, settled_at=now)
        parts = _parts(economics)
        settle_person_addons(order, settlement, parts, snapshot=snapshot, assignment=assignment,
                             report=report, profile=profile, now=now, trace_id=trace_id)
        assignment.settled_at = now
        assignment.save(update_fields=['settled_at'])
        share.settled_at, share.income_transaction, share.settled_report, share.report_submitted_at = now, income, report, report.submitted_at
        share.save(update_fields=['settled_at', 'income_transaction', 'settled_report', 'report_submitted_at'])
        if permit and not share.retained_income and not any(
                part.provider_account_id == fact['account_id'] and part.provider_income_units for part in parts):
            from wallet.refund_recovery_order import close_for_zero_ordinary_addon
            close_for_zero_ordinary_addon(profile, person)
        EscortProfile.objects.filter(pk=profile.pk).update(completed_order_count=F('completed_order_count') + 1)
        from .services import refresh_escort_status
        refresh_escort_status(provider_ids=[fact['user_id']], account_ids=[fact['account_id']], exclude_order_ids=[order.pk])
        from .notifier import notify_report_update
        transaction.on_commit(lambda: notify_report_update(report))
        settled.append(fact['user_id'])
    from .ordinary_refunded_addon_settlement import _verify_paid_people
    prior_segments = [row['segment_id'] for row in economics['people'] if row['affected']]
    if (order.aftersales.filter(status='OPEN').exists() or
            not _verify_paid_people(order, settlement, link.previous, economics, segment_ids=prior_segments)):
        return settled
    _verify_person(order, link, settlement, economics, fact)
    from .services import _credit_wallet
    if order.transactions.filter(status='SUCCESS').filter(
            Q(tx_type='SHOP_INCOME') | Q(tx_type='INCOME', remark=f'推荐分佣：{order.order_no}')).exists():
        raise SettlementError('追加售后门店或推荐收入已有未关联流水')
    if estimate['inviter_income'] and not (order.inviter_id or order.inviter_account_id):
        raise SettlementError('追加售后推荐人身份待核对')
    current.inviter_transaction = _credit_wallet(order=order, amount=estimate['inviter_income'], tx_type='INCOME',
        remark=f'推荐分佣：{order.order_no}', event='order.ordinary_addon_followup.inviter', trace_id=trace_id,
        account_id=order.inviter_account_id, user_id=order.inviter_id, log_after_commit=True)
    if current.inviter_transaction_id and order.inviter_account_id:
        from club_accounts.models import ClubAccount
        if ClubAccount.objects.filter(pk=order.inviter_account_id, account_type=ClubAccount.AccountType.BOSS).exists():
            from engagement.models import PointFundingLot
            PointFundingLot.objects.create(account_id=order.inviter_account_id, source_key=f'negotiation-referral:{current.pk}',
                                            kind=PointFundingLot.Kind.PREPAID, remaining=estimate['inviter_income'])
    addon_refunds = list(order.addon_refunds.select_for_update().order_by('pk'))
    rounding = (link.previous.rounding_to_store_units + current.rounding_to_store_units
                + sum(row.rounding_surplus for row in addon_refunds))
    if rounding > estimate['store_income'] or any(row.rounding_transaction_id for row in addon_refunds):
        raise SettlementError('追加售后取整差额资金待核对')
    current.shop_transaction = _credit_wallet(order=order, amount=estimate['store_income'] - rounding, tx_type='SHOP_INCOME',
        remark=f'平台收入：{order.order_no}', event='order.ordinary_addon_followup.store', trace_id=trace_id,
        platform=True, log_after_commit=True)
    for receipt in (link.previous, current):
        receipt.rounding_transaction = _credit_wallet(order=order, amount=receipt.rounding_to_store_units,
            tx_type='SHOP_INCOME', remark=f'退款取整差额：{order.order_no}（协商 {receipt.pk}）',
            event='order.ordinary_addon_followup.rounding', trace_id=trace_id, platform=True, log_after_commit=True)
        receipt.save(update_fields=['rounding_transaction'])
    for refund in addon_refunds:
        refund.rounding_transaction = _credit_wallet(order=order, amount=refund.rounding_surplus,
            tx_type='SHOP_INCOME', remark=f'加购退款取整差额：{order.order_no}（退款 {refund.pk}）',
            event='order.ordinary_addon_followup.addon_rounding', trace_id=trace_id,
            platform=True, log_after_commit=True)
        refund.save(update_fields=['rounding_transaction'])
    now = timezone.now()
    current.finalized_at = now
    current.save(update_fields=['inviter_transaction', 'shop_transaction', 'finalized_at'])
    order.settlement_store_funding = estimate['required_funding']
    order.settlement_addon_contribution = max(0, sum(row['principal_units'] + row['recovery_units'] - row['provider_units']
                                                      for row in economics['addon_history']))
    order.settled_at = now
    order.save(update_fields=['settlement_store_funding', 'settlement_addon_contribution', 'settled_at'])
    verify_finalized_locked(order, link)
    return settled
