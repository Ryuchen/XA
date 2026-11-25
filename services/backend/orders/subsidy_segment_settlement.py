"""Isolated test path for platform-funded, transferred safety-box earnings."""
from dataclasses import asdict, dataclass, replace

from django.conf import settings
from django.db.models import F
from stores.db import transaction

from wallet.settlement_clocks import (clock_scope, record as record_clock,
                                      require_clock, verify as verify_clock)

from .models import OrderSegmentPersonSettlement, OrderSegmentSettlementManifest
from .settlement import SettlementError


@dataclass(frozen=True)
class NegotiatedSegmentAmount:
    segment_id: int
    account_id: int
    basis: int
    fee: int
    income: int
    original_income: int
    negotiated_reduction: int


def _plan(order):
    """Apply a verified current-segment agreement without changing old snapshots."""
    from .segment_settlement_plan import build_segment_settlement_plan
    from .subsidy_execution import table_available
    from .models import SubsidyAgreementExecution
    raw = build_segment_settlement_plan(order, allow_recorded=True, _allow_subsidy=True)
    execution = SubsidyAgreementExecution.objects.filter(order=order).select_related('order', 'proposal__case').first() if table_available() else None
    if not execution:
        return raw
    from .subsidy_transfer_capability import stage_enabled
    if not stage_enabled('VIP_SAFE_BOX_TRANSFER_NEGOTIATION_SETTLEMENT_TESTING'):
        raise SettlementError('安全箱转单后协商结算尚未开放')
    from .subsidy_transfer_agreement_evidence import verify_committed
    from rest_framework.exceptions import ValidationError
    try:
        terms = verify_committed(execution)
    except ValidationError as exc:
        raise SettlementError('安全箱转单协商依据待核对') from exc
    reduction = terms.original_income - terms.retained_income
    matched = [line for line in raw.segments if line.segment_id == terms.segment_id]
    people = [person for person in raw.persons if person.account_id == terms.account_id]
    if (len(matched) != 1 or len(people) != 1 or terms.segment_id not in people[0].segment_ids
            or people[0].income < reduction):
        raise SettlementError('安全箱协商报酬与履约记录不一致')
    lines = tuple(NegotiatedSegmentAmount(line.segment_id, line.account_id, line.basis, line.fee,
                  terms.retained_income, line.income, reduction) if line.segment_id == terms.segment_id else line
                  for line in raw.segments)
    persons = tuple(replace(person, income=person.income-reduction) if person.account_id == terms.account_id else person
                    for person in raw.persons)
    return replace(raw, segments=lines, persons=persons)


def enabled():
    from .subsidy_segment_capture import transfer_testing_enabled
    from .subsidy_transfer_capability import stage_enabled
    return bool(stage_enabled('VIP_SAFE_BOX_SEGMENT_SETTLEMENT_TESTING') and transfer_testing_enabled())


def _terms(order, plan):
    result = {'kind':'PLATFORM_SUBSIDY', 'deal_amount':plan.deal_amount,
        'segments':[asdict(row) for row in plan.segments],
        'people':[{'account_id':row.account_id, 'segment_ids':list(row.segment_ids), 'income':row.income}
                  for row in plan.persons],
        'buyer_account_id':order.customer_account_id, 'deposit_percent':order.service_deposit_percent,
        'aftersale_hours':order.service_aftersale_hours, 'freeze_hours':order.service_freeze_hours,
        'completed_at':order.completed_at.isoformat() if order.completed_at else None}
    if any(isinstance(row, NegotiatedSegmentAmount) for row in plan.segments):
        from .models import SubsidyAgreementExecution, SubsidyTransferAgreementTerms
        execution = SubsidyAgreementExecution.objects.get(order=order)
        evidence = SubsidyTransferAgreementTerms.objects.get(proposal_id=execution.proposal_id)
        result['agreement'] = {'execution_id':execution.pk, 'proposal_id':execution.proposal_id,
                               'terms_hash':execution.terms_hash, 'evidence_hash':evidence.evidence_hash}
    return result


def _verified_records(order, shares, manifest, plan):
    from .segment_settlement import _hash, _validate_records
    from wallet.models import Transaction
    terms = _terms(order, plan)
    if (not manifest or manifest.plan != terms or manifest.plan_hash != _hash(terms)
            or manifest.inviter_transaction_id or manifest.store_transaction_id):
        raise SettlementError('安全箱逐段结算快照待核对')
    records = _validate_records(order, manifest, plan, subsidy_shares=shares)
    by_account = {share.assignment.provider_account_id:share for share in shares}
    if len(by_account) != len(shares):
        raise SettlementError('安全箱逐段陪玩身份待核对')
    for account_id, record in records.items():
        share = by_account.get(account_id)
        if (not share or not share.paid_at or share.amount != record.amount
                or share.income_transaction_id != record.income_transaction_id
                or share.paid_at != record.settled_at or share.assignment.settled_at != record.settled_at):
            raise SettlementError('安全箱逐段收益与补贴流水待核对')
    planned_accounts = {person.account_id for person in plan.persons}
    for share in shares:
        if share.paid_at and share.assignment.provider_account_id not in records:
            account_id = share.assignment.provider_account_id
            if (account_id in planned_accounts or share.amount or share.income_transaction_id
                    or share.expense_transaction_id or share.assignment.settled_at != share.paid_at
                    or any(line.account_id == account_id and (line.basis or line.income) for line in plan.segments)):
                raise SettlementError('安全箱零收益履约记录待核对')
    expenses = set(order.transactions.filter(tx_type=Transaction.TxType.SUBSIDY_EXPENSE,
        status='SUCCESS', reversal_of__isnull=True).values_list('pk', flat=True))
    expected = {share.expense_transaction_id for share in shares if share.paid_at and share.amount}
    if expenses != expected:
        raise SettlementError('安全箱平台补贴支出流水待核对')
    return records


def verify_paid_shares_locked(order, shares):
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
    if not manifest or bool(order.settled_at) != bool(manifest.finalized_at):
        raise SettlementError('安全箱逐段结算凭证缺失')
    with clock_scope(require_clock()):
        plan = _plan(order)
        _verified_records(order, shares, manifest, plan)


def _finalize_locked(order, shares, manifest, plan):
    if not manifest or order.aftersales.filter(status='OPEN').exists():
        return False
    clock = require_clock()
    records = _verified_records(order, shares, manifest, plan)
    if any(person.account_id not in records for person in plan.persons):
        return False
    now = clock.at
    zero_participants = []
    for share in shares:
        if not share.paid_at:
            if share.amount or any(line.account_id == share.assignment.provider_account_id and (line.basis or line.income)
                                   for line in plan.segments):
                raise SettlementError('安全箱未结算陪玩份额待核对')
            share.paid_at = now; share.save(update_fields=['paid_at'])
            share.assignment.settled_at = now; share.assignment.save(update_fields=['settled_at'])
            from .models import OrderParticipationSegment
            if OrderParticipationSegment.objects.filter(attribution__order=order,
                    provider_account_id=share.assignment.provider_account_id, started_at__isnull=False).exists():
                zero_participants.append(share.assignment.provider_id)
    _verified_records(order, shares, manifest, plan)
    if zero_participants:
        from users.models import EscortProfile
        EscortProfile.objects.filter(user_id__in=zero_participants).update(completed_order_count=F('completed_order_count')+1)
    if not plan.persons or any(person.freeze_until is None for person in plan.persons):
        raise SettlementError('安全箱逐段报单时间待核对')
    from django.utils.dateparse import parse_datetime
    submitted = [parse_datetime(item['submitted_at']) for row in manifest.people.all() for item in row.report_versions]
    if not submitted or any(value is None for value in submitted):
        raise SettlementError('安全箱逐段报单时间待核对')
    order.report_ready_at = max(submitted); order.consumption_ready_at = max(p.freeze_until for p in plan.persons)
    order.settled_at = now
    order.save(update_fields=['report_ready_at', 'consumption_ready_at', 'settled_at'])
    manifest.finalized_at = now; manifest.save(update_fields=['finalized_at'])
    record_clock(order, 'MANIFEST', manifest.pk, 'FINALIZE', clock)
    return True


def verify_locked(order, manifest, *, finalized=False):
    """Recheck report, funding ledger and every completed person under lock."""
    if not transaction.get_connection().in_atomic_block or not enabled():
        raise SettlementError('安全箱逐段结算凭证尚不可核对')
    from .financial_lock import lock_order_money
    lock_order_money()
    with clock_scope(require_clock()):
        return _verify_locked_clocked(order, manifest, finalized=finalized)


def _verify_locked_clocked(order, manifest, *, finalized=False):
    from .subsidies import _locked_subsidy_shares
    shares = _locked_subsidy_shares(order)
    plan = _plan(order)
    records = _verified_records(order, shares, manifest, plan)
    if finalized:
        if not order.settled_at:
            raise SettlementError('安全箱逐段结算完成凭证待核对')
        verify_clock(order, 'MANIFEST', manifest.pk, 'FINALIZE', order.settled_at, require_clock())
        from django.utils.dateparse import parse_datetime
        submitted = [parse_datetime(item['submitted_at']) for row in records.values() for item in row.report_versions]
        if (manifest.finalized_at != order.settled_at
                or not plan.persons or any(not share.paid_at for share in shares)
                or len(records) != len(plan.persons) or order.aftersales.filter(status='OPEN').exists()
                or any(person.freeze_until is None for person in plan.persons)
                or not submitted or any(value is None for value in submitted) or order.report_ready_at != max(submitted)
                or order.consumption_ready_at != max(person.freeze_until for person in plan.persons)):
            raise SettlementError('安全箱逐段结算完成凭证待核对')
    elif bool(order.settled_at) != bool(manifest.finalized_at):
        raise SettlementError('安全箱逐段结算完成状态待核对')
    return plan, records


def person_credit(order, account_id):
    """A read is trusted only after rechecking the actual subsidy and reports."""
    from .models import Order
    from django.core.exceptions import ObjectDoesNotExist
    try:
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            manifest = OrderSegmentSettlementManifest.objects.select_for_update().get(order=locked)
            _, records = verify_locked(locked, manifest, finalized=bool(locked.settled_at))
            record = records.get(account_id)
            return record.amount if record else None
    except (SettlementError, ObjectDoesNotExist, TypeError, ValueError, KeyError, AttributeError):
        return None


def settle_locked(order):
    """Release ready people only after funding the full outstanding order."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('安全箱逐段结算须在订单事务内执行')
    if not enabled() or order.settled_at:
        return []
    from .financial_lock import lock_order_money
    if (order.status != order.Status.COMPLETED or order.payment_status != order.PaymentStatus.PAID
            or order.settlement_mode != order.SettlementMode.REPORT_APPROVAL
            or type(order.service_deposit_percent) is not int or not 0 <= order.service_deposit_percent <= 100):
        return []
    lock_order_money()
    clock = require_clock()
    with clock_scope(clock):
        return _settle_locked_clocked(order, clock=clock)


def _settle_locked_clocked(order, *, clock):
    from .segment_settlement import _hash, _report_versions
    from .subsidies import _locked_subsidy_shares, subsidy_funds
    from wallet.models import Transaction, get_platform_wallet
    from wallet.services import get_wallet
    from wallet.income import hold_provider_income
    from users.models import EscortProfile
    shares = _locked_subsidy_shares(order)
    plan = _plan(order)
    terms = _terms(order, plan)
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
    if manifest:
        if manifest.finalized_at:
            raise SettlementError('安全箱结算完成状态待核对')
        records = _verified_records(order, shares, manifest, plan)
    else:
        if (any(share.paid_at for share in shares) or order.transactions.filter(
                tx_type__in=(Transaction.TxType.INCOME, Transaction.TxType.SUBSIDY_EXPENSE, Transaction.TxType.SHOP_INCOME),
                status='SUCCESS', reversal_of__isnull=True).exists()):
            raise SettlementError('安全箱存在未归属的历史收益或补贴支出')
        records = {}
    platform = get_platform_wallet(for_update=True)
    outstanding = sum(share.amount for share in shares if not share.paid_at)
    if subsidy_funds(platform)['available'] < outstanding:
        return []
    due = [person for person in plan.persons if person.account_id not in records and not person.blockers]
    if not due:
        _finalize_locked(order, shares, manifest, plan)
        return []
    from django.db.models import Q, Sum
    from .models import RefundRecoveryLine
    from wallet.refund_deposit import enabled as deficit_enabled
    from wallet.models import RefundDepositDeficit
    account_ids = [person.account_id for person in due]
    profiles = {profile.account_id:profile for profile in EscortProfile.objects.select_for_update()
                .filter(account_id__in=account_ids).order_by('user_id')}
    if len(profiles) != len(due):
        raise SettlementError('安全箱陪玩资料待核对')
    for profile in profiles.values():
        if profile.deposit_mode != 'EARNINGS' and profile.deposit_paid < profile.effective_deposit_required:
            gap = profile.effective_deposit_required - profile.deposit_paid
            recorded = (RefundDepositDeficit.objects.filter(profile=profile).aggregate(v=Sum('remaining'))['v'] or 0
                        if deficit_enabled() else 0)
            if recorded < gap:
                raise SettlementError('现金押金不足，补足情况待核对')
    legacy_recoveries = RefundRecoveryLine.objects.filter(source='DEPOSIT').filter(
        Q(wallet__account_id__in=account_ids) | Q(wallet__user_id__in=[p.user_id for p in profiles.values()]))
    if deficit_enabled():
        registered = RefundDepositDeficit.objects.filter(account_id__in=account_ids,
            source_provider_transaction__isnull=False).values('source_provider_transaction_id')
        legacy_recoveries = legacy_recoveries.exclude(reversal_transaction_id__in=registered)
    if legacy_recoveries.exists():
        raise SettlementError('陪玩存在历史退款押金扣减，补足情况待核对')
    if not manifest:
        manifest = OrderSegmentSettlementManifest.objects.create(order=order, plan=terms, plan_hash=_hash(terms))
        record_clock(order, 'MANIFEST', manifest.pk, 'PLAN', clock)
    by_account = {share.assignment.provider_account_id:share for share in shares}
    now = clock.at
    paid_users = []
    for person in due:
        share = by_account.get(person.account_id)
        if not share or share.paid_at or share.amount != person.income:
            raise SettlementError('安全箱待付金额与逐段快照不一致')
        profile = profiles[person.account_id]
        if profile.user_id != share.assignment.provider_id:
            raise SettlementError('安全箱陪玩身份映射待核对')
        income = expense = debit = credit = None
        if person.income:
            wallet = get_wallet(user_id=share.assignment.provider_id, account_id=person.account_id, for_update=True)
            before = platform.balance; platform.balance -= person.income; platform.save(update_fields=['balance'])
            expense = Transaction.objects.create(wallet=platform, order=order, tx_type='SUBSIDY_EXPENSE',
                amount=-person.income, balance_before=before, balance_after=platform.balance, remark=f'安全箱逐段补贴：{order.order_no}')
            before = wallet.balance; wallet.balance += person.income; wallet.save(update_fields=['balance'])
            income = Transaction.objects.create(wallet=wallet, order=order, tx_type='INCOME', amount=person.income,
                balance_before=before, balance_after=wallet.balance, remark=f'订单收入：{order.order_no}（安全箱逐段补贴）')
            before_deposit = profile.deposit_paid
            hold_provider_income(income, profile=profile, hold_hours=0, deposit_percent=order.service_deposit_percent,
                                 apply_without_hold=True)
            deficit = 0
            if deficit_enabled():
                from wallet.models import RefundDepositIncomeApplication
                application = RefundDepositIncomeApplication.objects.filter(source_transaction=income).first()
                if not application:
                    from wallet.income_priority_evidence import pending_repayment_hold
                    if not pending_repayment_hold(income):
                        raise SettlementError('安全箱收益补押金处理记录缺失')
                else:
                    deficit = application.applied_amount
            ordinary_deposit = profile.deposit_paid - before_deposit - deficit
            if ordinary_deposit:
                debit = Transaction.objects.filter(order=order, wallet=wallet, pk__gt=income.pk,
                    tx_type='DEPOSIT', amount=-ordinary_deposit).order_by('-pk').first()
                credit = Transaction.objects.filter(order=order, wallet=platform, pk__gt=income.pk,
                    tx_type='DEPOSIT_INCOME', amount=ordinary_deposit).order_by('-pk').first()
                if not debit or not credit:
                    raise SettlementError('安全箱押金配对流水缺失')
        record = OrderSegmentPersonSettlement.objects.create(manifest=manifest, account_id=person.account_id,
            amount=person.income, segment_ids=list(person.segment_ids), report_versions=_report_versions(person, clock=clock),
            income_transaction=income, deposit_debit=debit, deposit_credit=credit, settled_at=now)
        record_clock(order, 'PERSON', record.pk, 'PERSON', clock)
        if not person.income:
            from wallet.refund_recovery_order import close_for_zero_settlement
            close_for_zero_settlement(profile, record, now=clock)
        share.income_transaction, share.expense_transaction, share.paid_at = income, expense, now
        share.save(update_fields=['income_transaction', 'expense_transaction', 'paid_at'])
        share.assignment.settled_at = now; share.assignment.save(update_fields=['settled_at'])
        paid_users.append(share.assignment.provider_id)
    EscortProfile.objects.filter(user_id__in=paid_users).update(completed_order_count=F('completed_order_count')+1)
    from .services import refresh_escort_status
    refresh_escort_status(provider_ids=paid_users, exclude_order_ids=[order.pk])
    _finalize_locked(order, shares, manifest, plan)
    from wallet.notifier import notify_wallet_update
    transaction.on_commit(lambda: notify_wallet_update(platform))
    return paid_users
