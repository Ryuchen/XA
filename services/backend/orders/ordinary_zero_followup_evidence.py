"""Receipt-local facts for an initial zero-refund team termination.

This reader never locks an order or an unrelated receipt. Its caller must
independently prove the later funding allocation and release events.
"""
from copy import copy

from rest_framework.exceptions import ValidationError


def verified_first(execution, *, boundary):
    from .models import OrderOriginalShareTransfer, OrderPlatformSubsidy, OrderPrestartHandoff
    from .negotiation_execution import _allocation_hash
    from .negotiation_proposals import _decisions, _verified
    from .negotiation_zero import verify_zero_termination
    from .points_service_payment import needs_integrated_processing
    from .settlement import compute_provider_income
    from engagement.funding_wallet import validate_order_funding_sources

    order, proposal = execution.zero_order, execution.proposal
    if (not order or execution.refund_id or execution.customer_transaction_id
            or order.pk != proposal.case.order_id or order.is_gift_snapshot or order.source_order_id
            or order.required_provider_count != 2 or needs_integrated_processing(order)
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
            or OrderPrestartHandoff.objects.filter(order=order).exists()
            or OrderPlatformSubsidy.objects.filter(order=order).exists()
            or proposal.refund_units != 0 or proposal.status != 'ACCEPTED'
            or not proposal.accepted_at or execution.accepted_at != proposal.accepted_at
            or not execution.termination_processed_at or not boundary
            or not execution.accepted_at <= execution.termination_processed_at < boundary
            or execution.terms_hash != proposal.terms_hash or execution.rounding_to_store_units
            or not isinstance(execution.zero_request_id, str) or not 1 <= len(execution.zero_request_id) <= 64
            or execution.zero_request_id != execution.zero_request_id.strip()
            or not isinstance(execution.zero_allocation, list)
            or execution.allocation_hash != _allocation_hash(execution.zero_allocation)):
        raise ValidationError('首次零退款协商或业务时间边界待核对')
    episode = copy(proposal.case)
    episode.created_at, episode.client_request_id = proposal.case_opened_at, proposal.case_request_id
    _verified(episode, proposal)
    decisions = _decisions(episode, proposal)
    if not proposal.created_at or any(row.decided_at < proposal.created_at for row in decisions):
        raise ValidationError('首次零退款双方确认时序待核对')
    verify_zero_termination(execution)
    payments = list(order.transactions.filter(tx_type='PAY', status='SUCCESS', reversal_of__isnull=True)
                    .select_related('wallet'))
    if (len(payments) != 1 or payments[0].amount != -order.amount
            or payments[0].wallet.account_id != order.customer_account_id
            or payments[0].wallet.user_id != order.customer_id
            or payments[0].balance_after != payments[0].balance_before + payments[0].amount
            or payments[0].reversals.exists()):
        raise ValidationError('首次零退款原付款流水待核对')
    funding = list(order.funding_allocations.select_related('lot').order_by('pk'))
    if (not funding or sum(row.amount for row in funding) != order.amount
            or any(row.lot.account_id != order.customer_account_id for row in funding)):
        raise ValidationError('首次零退款原付款来源待核对')
    validate_order_funding_sources(order, funding)
    assignments = list(order.providers.order_by('pk'))
    if (len(assignments) != 2 or len(execution.zero_allocation) != 2
            or len({row.provider_account_id for row in assignments}) != 2
            or any(not row.provider_account_id or not row.provider_id
                   or row.provider_income != compute_provider_income(row.settlement_base, row.commission_type,
                       row.commission_rate, row.commission_fixed) for row in assignments)
            or sum(row.settlement_base for row in assignments) != order.amount
            or sum(row.provider_income for row in assignments) != order.provider_income
            or order.provider_income + order.inviter_commission + order.shop_income != order.amount):
        raise ValidationError('首次零退款双陪原始份额待核对')
    for assignment, item in zip(assignments, execution.zero_allocation):
        expected = {'key': f'provider:{assignment.pk}', 'role': 'PROVIDER',
                    'account_id': assignment.provider_account_id, 'user_id': assignment.provider_id,
                    'share_id': assignment.pk, 'entitlement': assignment.provider_income,
                    'amount': 0, 'retained': assignment.provider_income}
        if (not isinstance(item, dict) or type(item.get('settled')) is not bool
                or {key: value for key, value in item.items() if key != 'settled'} != expected):
            raise ValidationError('首次零退款原陪玩分配待核对')
    from .addon_main_deduction import deductions_at
    prior_deductions = deductions_at(order, before=execution.created_at)
    shares = list(execution.shares.select_related('assignment').order_by('account_id'))
    if (len(shares) != 1 or proposal.affected_accounts != [shares[0].account_id]
            or shares[0].assignment.order_id != order.pk
            or shares[0].assignment.provider_account_id != shares[0].account_id
            or shares[0].assignment.released_at != execution.accepted_at
            or shares[0].original_income != shares[0].assignment.provider_income - prior_deductions.get(shares[0].account_id, 0)
            or shares[0].retained_income != shares[0].original_income
            or any((shares[0].burden, shares[0].pending_deduction, shares[0].wallet_deduction,
                    shares[0].deposit_deduction, shares[0].wallet_transaction_id, shares[0].deposit_deficit_id))):
        raise ValidationError('首次零退款受影响陪玩或终止事实待核对')
    if proposal.provider_terms != [{'account_id': shares[0].account_id,
            'original_income_units': shares[0].original_income, 'burden_units': 0,
            'retained_units': shares[0].retained_income, 'additional_units': 0}]:
        raise ValidationError('首次零退款确认方案与本人收益不一致')
    return shares


def verified_funding_at(execution, *, boundary, allocated_before, allow_post_addons=False,
                        witness_receipt_id=None, current_refund_id=None):
    """Rebuild the no-add-on first-zero funding plan at a real later boundary."""
    from dataclasses import asdict, replace
    from engagement.funding_plan import RefundablePart, plan_refund
    from engagement.models import OrderPointAllocation
    from .models import OrderAddonHandover, OrderAddonPayment
    from .points_negotiation_preview import funding_after_refund

    if type(allocated_before) is not int or allocated_before < 0:
        raise ValidationError('首次零退款前补资金额待核对')
    shares = verified_first(execution, boundary=boundary)
    order = execution.zero_order
    payment_query = OrderAddonPayment.objects.filter(intent__order=order)
    if allow_post_addons:
        payment_query = payment_query.filter(buyer_transaction__created_at__lt=boundary)
    payments = list(payment_query.select_related('buyer_transaction'))
    handovers = list(OrderAddonHandover.objects.filter(payment__in=payments, status='ACCEPTED', accepted_at__lt=boundary)
                     .select_related('payment__intent__order', 'source_segment__attribution',
                                     'target_segment__attribution').order_by('pk'))
    if (OrderPointAllocation.objects.filter(order=order).exists()
            or (not allow_post_addons and any(payment.buyer_transaction.created_at >= execution.accepted_at
                   or payment.refund_records.filter(created_at__gte=execution.accepted_at).exists() for payment in payments))
            or OrderAddonHandover.objects.filter(payment__in=payments, status='PENDING').exists()
            or any(not item.accepted_at or (item.accepted_at >= execution.accepted_at
                   and (not allow_post_addons or item.created_at < execution.termination_processed_at
                        or item.accepted_at >= boundary)) for item in handovers)):
        raise ValidationError('首次零退款加购或积分资金版本尚待核对')
    if allow_post_addons:
        continuing = list(order.providers.exclude(provider_account_id=shares[0].account_id)
                          .values_list('provider_account_id', flat=True))
        if len(continuing) != 1 or not continuing[0]:
            raise ValidationError('零退款后继续履约的陪玩待核对')
        for payment in payments:
            if execution.accepted_at <= payment.buyer_transaction.created_at < execution.termination_processed_at:
                raise ValidationError('零退款和加购付款时序待核对')
            if (payment.buyer_transaction.created_at >= execution.termination_processed_at
                    and (payment.intent.provider_id != continuing[0]
                         or payment.intent.segment.provider_account_id != continuing[0]
                         or not payment.intent.segment.started_at
                         or payment.intent.segment.started_at > payment.buyer_transaction.created_at
                         or (payment.intent.segment.released_at and
                             payment.intent.segment.released_at <= payment.buyer_transaction.created_at))):
                raise ValidationError('零退款后加购负责人待核对')
            for refund in payment.refund_records.filter(created_at__gte=execution.termination_processed_at,
                                                         created_at__lt=boundary):
                if set(refund.proposal.participants.values_list('account_id', flat=True)) != {continuing[0]}:
                    raise ValidationError('零退款后加购退款范围待核对')
    if handovers:
        for handover in handovers:
            if handover.accepted_at < execution.accepted_at:
                from .ordinary_team_pre_main_handover import verify as verify_handover
            else:
                from .ordinary_refunded_team_handover import verify as verify_handover
            verify_handover(handover)
    if any(payment.refund_records.filter(created_at__lt=boundary).exists() for payment in payments):
        from .ordinary_zero_post_addon import verify_addon_refunds_before
        verify_addon_refunds_before(order, execution, boundary, witness_receipt_id=witness_receipt_id,
                                    current_refund_id=current_refund_id)
    sources = list(order.funding_allocations.order_by('pk'))
    refunds = list(order.refund_records.order_by('created_at', 'pk'))
    if len(refunds) > 1 or any(row.created_at <= boundary for row in refunds):
        raise ValidationError('首次零退款后退款时间边界待核对')
    expected_current = plan_refund([RefundablePart(row.pk, row.kind, row.amount) for row in sources],
                                   sum(row.amount for row in refunds))
    if any(row.refunded_amount != expected_current[row.pk] for row in sources):
        raise ValidationError('首次零退款后付款来源累计退回待核对')
    totals = {kind: sum(row.amount for row in sources if row.kind == kind)
              for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
    rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    if order.inviter_commission != order.amount * rate // 100:
        raise ValidationError('首次零退款推荐佣金快照待核对')
    combined = funding_after_refund(principal_units=totals['PREPAID'], gift_units=totals['GIFT'],
        compensation_units=totals['COMPENSATION'], returned_prepaid_units=0, returned_gift_units=0,
        returned_compensation_units=0, provider_income_units=order.provider_income,
        pending_deduction_units=0, provider_external_units=0, inviter_rate=rate,
        allocated_units=allocated_before)
    history = {'addon_history': []}
    if payments:
        from .addon_funding_history import combined_at
        from .refunded_addon_funding import combine_refunded_history
        assignments = list(order.providers.order_by('pk'))
        retained = {row.provider_account_id: row.provider_income for row in assignments}
        segment_shares = [(row.pk, row.provider_account_id, 0, 0, retained[row.provider_account_id])
                          for row in assignments]
        history = combined_at(order, combined, boundary, segment_shares=segment_shares)
        combined = combine_refunded_history(combined, history, main_retained_by_account=retained)
    rounding = execution.rounding_to_store_units + sum(order.addon_refunds.filter(
        created_at__lt=boundary).values_list('rounding_surplus', flat=True))
    if rounding > combined.store_income_units:
        extra = rounding - combined.store_income_units
        required = combined.required_units + extra
        combined = replace(combined, store_income_units=combined.store_income_units + extra,
            required_units=required, release_units=max(allocated_before - required, 0),
            remaining_units=max(required - allocated_before, 0))
    return {'execution_id': execution.pk, 'refund_id': None, 'combined': asdict(combined),
            'addon_history': list(history['addon_history']), 'affected_account_id': shares[0].account_id}


def _verify_income_application(order, tx, first_share, assignment, before):
    from wallet.models import RefundDepositDeficit, RefundDepositIncomeApplication
    if tx:
        from django.conf import settings
        from wallet.refund_deposit import protection_enabled
        application = RefundDepositIncomeApplication.objects.filter(source_transaction=tx).select_related('profile').first()
        if not application and protection_enabled() and RefundDepositDeficit.objects.filter(
                account_id=first_share.account_id, created_at__lte=tx.created_at).exists():
            raise ValidationError('首次零退款陪玩收益补押金记录缺失')
        if application:
            repayments = list(application.repayments.select_related(
                'deficit__profile', 'provider_transaction__wallet', 'store_transaction__wallet__user'))
            if (application.account_id != first_share.account_id or application.profile.account_id != first_share.account_id
                    or application.profile.user_id != assignment.provider_id or application.available_amount != tx.amount
                    or application.created_at >= before
                    or application.applied_amount > tx.amount
                    or sum(item.amount for item in repayments) != application.applied_amount):
                raise ValidationError('首次零退款陪玩收益补押金汇总待核对')
            for item in repayments:
                deficit, debit, credit = item.deficit, item.provider_transaction, item.store_transaction
                if (item.amount <= 0 or item.created_at >= before or deficit.account_id != first_share.account_id
                        or deficit.profile_id != application.profile_id or not deficit.account_business_cycle
                        or debit.wallet_id != tx.wallet_id or debit.order_id != order.pk
                        or debit.tx_type != 'DEPOSIT' or debit.status != 'SUCCESS' or debit.amount != -item.amount
                        or credit.order_id != order.pk or credit.tx_type != 'DEPOSIT_INCOME'
                        or credit.status != 'SUCCESS' or credit.amount != item.amount
                        or not credit.wallet.user or credit.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
                        or debit.deposit_owner_id != first_share.account_id or credit.deposit_owner_id != first_share.account_id
                        or debit.deposit_business_cycle != deficit.account_business_cycle
                        or credit.deposit_business_cycle != deficit.account_business_cycle
                        or credit.deposit_pair_id != debit.pk
                        or debit.balance_after != debit.balance_before + debit.amount
                        or credit.balance_after != credit.balance_before + credit.amount
                        or debit.reversal_of_id or credit.reversal_of_id
                        or debit.reversals.exists() or credit.reversals.exists()):
                    raise ValidationError('首次零退款陪玩收益补押金流水待核对')
                if deficit.source_kind == 'DIRECT':
                    from .ordinary_refund_receipt_evidence import _verify_direct_deposit
                    source = deficit.refund if deficit.refund_id else deficit.addon_refund
                    if not source:
                        raise ValidationError('首次零退款陪玩押金缺口来源缺失')
                    _verify_direct_deposit(deficit, refund=source, account_id=first_share.account_id,
                        user_id=assignment.provider_id, amount=deficit.amount, addon=bool(deficit.addon_refund_id))
                elif deficit.source_kind == 'PAIRED':
                    source, provider, store = (deficit.refund, deficit.source_provider_transaction,
                                               deficit.source_store_transaction)
                    if (not source or not provider or not store or deficit.addon_refund_id
                            or provider.order_id != source.order_id or store.order_id != source.order_id
                            or provider.wallet.account_id != first_share.account_id
                            or provider.wallet.user_id != assignment.provider_id
                            or provider.tx_type != 'DEPOSIT' or provider.status != 'SUCCESS'
                            or provider.amount != deficit.amount or store.tx_type != 'DEPOSIT_INCOME'
                            or store.status != 'SUCCESS' or store.amount != -deficit.amount
                            or not store.wallet.user or store.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
                            or provider.deposit_owner_id != first_share.account_id
                            or store.deposit_owner_id != first_share.account_id
                            or provider.deposit_business_cycle != deficit.account_business_cycle
                            or store.deposit_business_cycle != deficit.account_business_cycle
                            or store.deposit_pair_id != provider.pk
                            or provider.balance_after != provider.balance_before + provider.amount
                            or store.balance_after != store.balance_before + store.amount):
                        raise ValidationError('其他订单退款押金缺口原流水待核对')
                    provider_line = source.recovery_lines.filter(source='DEPOSIT', amount=deficit.amount,
                        reversal_transaction=provider, wallet=provider.wallet).first()
                    store_line = source.recovery_lines.filter(source='DEPOSIT', amount=-deficit.amount,
                        reversal_transaction=store, wallet=store.wallet).first()
                    if (not provider_line or not store_line
                            or provider_line.original_transaction_id != provider.reversal_of_id
                            or store_line.original_transaction_id != store.reversal_of_id):
                        raise ValidationError('其他订单退款押金追回记录待核对')
                else:
                    raise ValidationError('退款押金缺口类型待核对')


def _verify_prior_person(order, first_share, settlement, *, before):
    """Read A's already-posted income without taking a wallet or order lock."""
    from datetime import timedelta
    from wallet.models import ProviderReport
    from .services import missing_report_evidence

    assignment = first_share.assignment
    people = list(settlement.persons.filter(segment__provider_account_id=first_share.account_id,
        settled_at__lt=before).select_related('report', 'segment', 'income_transaction__wallet'))
    if len(people) != 1:
        raise ValidationError('首次零退款陪玩已结算凭证待核对')
    person = people[0]
    report, tx = person.report, person.income_transaction
    if (person.segment.attribution.order_id != order.pk or person.amount != first_share.retained_income
            or assignment.settled_at != person.settled_at or first_share.settled_at != person.settled_at
            or first_share.income_transaction_id != person.income_transaction_id
            or first_share.settled_report_id != report.pk or first_share.report_submitted_at != report.submitted_at
            or not order.completed_at or person.settled_at < order.completed_at
            or order.service_aftersale_hours is None or order.service_freeze_hours is None
            or person.settled_at < first_share.execution.accepted_at + timedelta(hours=order.service_aftersale_hours)
            or report.order_id != order.pk or report.provider_account_id != first_share.account_id
            or report.provider_id != assignment.provider_id or report.status != ProviderReport.Status.APPROVED
            or not report.submitted_at or not report.audited_at or report.audited_at < report.submitted_at
            or report.audited_at > person.settled_at
            or person.settled_at < report.submitted_at + timedelta(hours=order.service_freeze_hours)
            or report.payout_amount != assignment.provider_income or missing_report_evidence(report)):
        raise ValidationError('首次零退款陪玩报单或结算时限待核对')
    credits = list(order.transactions.filter(tx_type='INCOME', status='SUCCESS',
        wallet__account_id=first_share.account_id, wallet__user_id=assignment.provider_id,
        remark=f'订单收入：{order.order_no}'))
    if (len(credits) != (1 if person.amount else 0) or (credits and credits[0].pk != person.income_transaction_id)
            or (person.amount and (not tx or tx.order_id != order.pk or tx.wallet.account_id != first_share.account_id
                or tx.wallet.user_id != assignment.provider_id or tx.tx_type != 'INCOME' or tx.status != 'SUCCESS'
                or tx.amount != person.amount or tx.balance_after != tx.balance_before + tx.amount
                or tx.reversal_of_id or tx.reversals.exists()))
            or (not person.amount and tx)):
        raise ValidationError('首次零退款陪玩实际入账流水待核对')
    _verify_income_application(order, tx, first_share, assignment, before)
    from .models import OrderAddonPayment
    from .addon_handover import current_responsibilities
    expected = []
    for payment in OrderAddonPayment.objects.filter(intent__order=order).order_by('pk'):
        _, responsibilities = current_responsibilities(payment, before=before)
        expected.extend((payment.pk, part['segment_id'], part['income']) for part in responsibilities
                        if part['provider_id'] == first_share.account_id)
    addon_rows = list(settlement.addon_settlements.filter(segment_id=person.segment_id,
        settled_at__lt=before).select_related('income_transaction__wallet').order_by('payment_id', 'segment_id'))
    if [(row.payment_id, row.segment_id, row.amount) for row in addon_rows] != sorted(expected):
        raise ValidationError('首次零退款陪玩加购收益凭证待核对')
    for addon in addon_rows:
        income = addon.income_transaction
        if (addon.report_id != report.pk or addon.settled_at != person.settled_at
                or (addon.amount and (not income or income.order_id != order.pk
                    or income.wallet.account_id != first_share.account_id
                    or income.wallet.user_id != assignment.provider_id or income.tx_type != 'INCOME'
                    or income.status != 'SUCCESS' or income.amount != addon.amount
                    or income.balance_after != income.balance_before + income.amount
                    or income.reversal_of_id or income.reversals.exists()))
                or (not addon.amount and income)):
            raise ValidationError('首次零退款陪玩加购入账流水待核对')
        _verify_income_application(order, income, first_share, assignment, before)
    return person


def verified_release(row, *, witness_receipt_id):
    """Verify a later positive refund's no-add-on release under its receipt lock."""
    from datetime import timedelta
    from console.deposit_clearance import _verified_refund_execution
    from engagement.funding_plan import RefundablePart, plan_refund
    from users.models import PassReceiptUsageState
    from users.receipt_usage_ledger import _locked_receipt
    from users.receipt_usage_sources import _digest, _require_current_unlocked_reads, order_facts
    from .models import (NegotiationFollowupLink, OrderAddonFundingRelease, OrderPointsFundingAllocation,
                         OrderSegmentFinancialSnapshot,
                         OrderPointsFundingRelease, OrderPointsTransferFundingRelease, PointsPostRefundTransferRelease)
    from .negotiation_execution import _allocation_hash
    from .negotiation_followup_accounting import verify_followup_termination
    from .ordinary_followup_receipt import _allocation_source
    from .ordinary_refunded_addon_release import _digest as release_digest
    from .ordinary_refunded_addon_settlement import _digest as evidence_digest
    from .points_funding_allocation import plan_release

    run, witness = row.run, row.allocation
    _locked_receipt(witness_receipt_id)
    _require_current_unlocked_reads()
    link = NegotiationFollowupLink.objects.select_related('order', 'previous__zero_order',
        'execution__refund').filter(execution_id=run.negotiation_id).first()
    if not link or link.previous.refund_id or not link.execution.refund_id:
        raise ValidationError('零退款后的补资释放缺少真实协商来源')
    order, first, second = link.order, link.previous, link.execution
    refund = second.refund
    boundary = refund.created_at - timedelta(microseconds=1)
    if (first.zero_order_id != order.pk or refund.order_id != order.pk
            or first.termination_processed_at is None or first.termination_processed_at >= boundary
            or not second.termination_processed_at or second.termination_processed_at >= run.created_at
            or refund.created_at >= run.created_at or witness.service_order_id != order.pk
            or row.amount <= 0 or link.prior_refund_units != 0
            or link.original_amount_units != order.amount or link.previous_terms_hash != first.terms_hash
            or not isinstance(link.projected_ledger, dict)
            or link.projected_ledger.get('ordinary_addon_followup_version') != 1
            or link.ledger_hash != _allocation_hash(link.projected_ledger)):
        raise ValidationError('零退款后的资金事件归属或时序待核对')
    first_shares = verified_first(first, boundary=boundary)
    checked = _verified_refund_execution(refund)
    verify_followup_termination(link)
    shares = list(second.shares.select_related('assignment', 'wallet_transaction__wallet',
        'deposit_deficit__profile', 'deposit_deficit__source_store_transaction__wallet__user'))
    if (not checked or checked.pk != second.pk or len(shares) != 1
            or shares[0].account_id == first_shares[0].account_id
            or shares[0].assignment.order_id != order.pk
            or shares[0].assignment.provider_account_id != shares[0].account_id):
        raise ValidationError('后续退款与双方确认范围待核对')
    allocations = list(OrderPointsFundingAllocation.objects.filter(service_order=order,
        created_at__lt=refund.created_at).select_related('receipt').order_by('pk'))
    if (not allocations or not isinstance(run.allocation_snapshot, list)
            or len(allocations) != len(run.allocation_snapshot)
            or witness_receipt_id not in {item.receipt_id for item in allocations}):
        raise ValidationError('零退款后的补资分配缺失')
    state = PassReceiptUsageState.objects.filter(receipt_id=witness_receipt_id).first()
    if not state:
        raise ValidationError('订单补资到账用途账本缺失')
    normalized = []
    for allocation, snap in zip(allocations, run.allocation_snapshot):
        _allocation_source(allocation, order, refund.created_at)
        if (allocation.redemption_id or not isinstance(snap, list) or len(snap) != 4
                or snap[0] != allocation.pk or snap[1] != allocation.amount
                or type(snap[2]) is not int or snap[2] < 0 or snap[3] != []
                or OrderPointsFundingRelease.objects.filter(allocation=allocation, created_at__lte=run.created_at).exists()
                or OrderPointsTransferFundingRelease.objects.filter(allocation=allocation,
                    revision__created_at__lte=run.created_at).exists()
                or PointsPostRefundTransferRelease.objects.filter(allocation=allocation,
                    revision__created_at__lte=run.created_at).exists()
                or OrderAddonFundingRelease.objects.filter(allocation=allocation,
                    run__created_at=run.created_at).exclude(run=run).exists()):
            raise ValidationError('零退款之前或之后存在未核验的补资释放')
        earlier = list(OrderAddonFundingRelease.objects.filter(allocation=allocation,
            run__created_at__lt=run.created_at).exclude(run=run).select_related('run', 'allocation').order_by('pk'))
        def supported_prior_release(item):
            source = item.run
            if (source.ordinary_prestart_snapshot and source.ordinary_prestart_hash
                    and source.created_at < first.accepted_at):
                return True
            facts = source.ordinary_refund_snapshot
            if not isinstance(facts, dict) or source.created_at >= refund.created_at:
                return False
            if source.payment_id:
                return (facts.get('kind') == 'ordinary_refund_addon_payment_v1'
                        and source.payment.intent.order_id == order.pk
                        and source.payment.buyer_transaction.created_at >= first.termination_processed_at
                        and source.payment.buyer_transaction.created_at < source.created_at)
            if source.refund_id:
                return (facts.get('kind') == 'ordinary_addon_after_main_refund_v1'
                        and source.refund.order_id == order.pk
                        and source.refund.created_at >= first.termination_processed_at
                        and source.refund.created_at < source.created_at)
            if source.handover_id:
                return (facts.get('kind') == 'ordinary_refund_addon_handover_v1'
                        and source.handover.payment.intent.order_id == order.pk
                        and source.handover.accepted_at >= first.termination_processed_at
                        and source.handover.accepted_at < source.created_at)
            return False
        if (sum(item.amount for item in earlier) != snap[2] or snap[2] > allocation.amount
                or any(not supported_prior_release(item) for item in earlier)):
            raise ValidationError('零退款前本单补资释放来源待核对')
        if allocation.receipt_id == witness_receipt_id:
            kind, key, request, amount, digest = order_facts(allocation)
            if not state.events.filter(kind=kind, allocation_key=key, request_key=request,
                                       amount=amount, source_digest=digest).exists():
                raise ValidationError('订单补资到账用途缺失')
            from users.receipt_usage_sources import _addon_release_facts
            for item in earlier:
                kind, key, request, amount, digest = _addon_release_facts(item, witness_receipt_id=witness_receipt_id)
                if not state.events.filter(kind=kind, allocation_key=key, request_key=request,
                                           amount=amount, source_digest=digest).exists():
                    raise ValidationError('零退款前本单释放用途凭证缺失')
        normalized.append([allocation.pk, allocation.amount, snap[2]])
    allocated = sum(item[1] - item[2] for item in normalized)
    original = verified_funding_at(first, boundary=boundary, allocated_before=allocated,
                                   allow_post_addons=True, witness_receipt_id=witness_receipt_id)
    evidence = order.points_settlement.ordinary_refund_addon_evidence
    data, projection = evidence.facts, link.projected_ledger
    estimate = projection.get('estimate')
    if (not isinstance(data, dict) or evidence.execution_id != first.pk
            or evidence.facts_hash != evidence_digest(data) or projection.get('prior_facts_hash') != evidence.facts_hash
            or data.get('combined') != original['combined'] or data.get('addon_history') != original['addon_history']
            or data.get('funding_uses') !=
               [[pk, amount - released] for pk, amount, released in normalized if amount > released]
            or not isinstance(estimate, dict) or estimate.get('prior_paid') not in (0, 1)):
        raise ValidationError('零退款后的原结算预留待核对')
    paid = list(order.points_settlement.persons.filter(settled_at__lt=refund.created_at)
                .values_list('segment__provider_account_id', flat=True))
    if (len(paid) != estimate['prior_paid'] or (paid and paid != [first_shares[0].account_id])
            or order.providers.filter(provider_account_id=shares[0].account_id,
                                      settled_at__lte=refund.created_at).exists()):
        raise ValidationError('零退款后已结算陪玩范围待核对')
    if paid:
        _verify_prior_person(order, first_shares[0], order.points_settlement, before=refund.created_at)
    people = data.get('people')
    if not isinstance(people, list) or len(people) != 2:
        raise ValidationError('零退款后的双陪收益范围待核对')
    accounts = {item.get('account_id') for item in people if isinstance(item, dict)}
    if (accounts != {first_shares[0].account_id, shares[0].account_id}
            or estimate.get('prior_settled_account_id') != first_shares[0].account_id
            or estimate.get('account_id') != shares[0].account_id):
        raise ValidationError('零退款后的陪玩范围待核对')
    by_account = {item['account_id']: item for item in people}
    from .addon_main_deduction import deductions_at
    prior_deductions = deductions_at(order, before=first.created_at)
    current_deductions = deductions_at(order, before=refund.created_at)
    if current_deductions.get(first_shares[0].account_id, 0) != prior_deductions.get(first_shares[0].account_id, 0):
        raise ValidationError('已终止陪玩的加购退款责任发生变化')
    for assignment in order.providers.all():
        person = by_account.get(assignment.provider_account_id)
        snapshot = (OrderSegmentFinancialSnapshot.objects.select_related('segment').filter(
            segment_id=person.get('segment_id'), segment__attribution__order=order,
            segment__provider_account_id=assignment.provider_account_id).first() if person else None)
        if (not person or person.get('assignment_id') != assignment.pk
                or person.get('user_id') != assignment.provider_id
                or person.get('main_income') != assignment.provider_income - current_deductions.get(assignment.provider_account_id, 0)
                or not snapshot or not snapshot.segment.started_at
                or snapshot.incoming_basis != assignment.settlement_base
                or snapshot.income_at_entry != assignment.provider_income
                or snapshot.commission_type != assignment.commission_type
                or snapshot.commission_rate != assignment.commission_rate
                or snapshot.commission_fixed != assignment.commission_fixed
                or snapshot.game_commission_snapshot != assignment.game_commission_snapshot
                or person.get('affected') is not (assignment.provider_account_id == first_shares[0].account_id)):
            raise ValidationError('零退款后的陪玩收益事实待核对')
    from .addon_handover import current_responsibilities
    from .models import OrderAddonPayment
    addons = []
    for payment in OrderAddonPayment.objects.filter(intent__order=order).order_by('pk'):
        _, responsibilities = current_responsibilities(payment, before=refund.created_at)
        for part in responsibilities:
            person = by_account.get(part['provider_id'])
            if not person or person['segment_id'] != part['segment_id']:
                raise ValidationError('零退款前加购收益履约归属待核对')
            addons.append({'payment_id': payment.pk, 'segment_id': part['segment_id'],
                           'account_id': part['provider_id'], 'basis': part['basis'], 'income': part['income']})
    if data.get('addons') != addons:
        raise ValidationError('零退款前加购收益和资金预留不一致')
    share = shares[0]
    if (share.original_income != share.assignment.provider_income - current_deductions.get(share.account_id, 0)
            or share.retained_income != share.original_income - share.pending_deduction
            or share.burden != share.pending_deduction + share.wallet_deduction + share.deposit_deduction
            or share.burden != refund.amount or refund.recovered_amount != share.wallet_deduction + share.deposit_deduction
            or estimate.get('pending_deduction') != share.pending_deduction
            or estimate.get('wallet_deduction') != share.wallet_deduction
            or estimate.get('deposit_deduction') != share.deposit_deduction
            or estimate.get('retained_main_income') != share.retained_income or estimate.get('insufficient') != 0):
        raise ValidationError('后续退款本人承担或收益待核对')
    if (second.proposal.affected_accounts != [share.account_id] or second.proposal.provider_terms != [{
            'account_id': share.account_id, 'original_income_units': share.original_income,
            'burden_units': share.burden, 'retained_units': share.retained_income,
            'additional_units': share.wallet_deduction + share.deposit_deduction}]):
        raise ValidationError('后续退款与本人确认方案待核对')
    tx = share.wallet_transaction
    if bool(tx) != bool(share.wallet_deduction) or (tx and (
            tx.order_id != order.pk or tx.wallet.account_id != share.account_id
            or tx.wallet.user_id != share.assignment.provider_id or tx.tx_type != 'REFUND'
            or tx.status != 'SUCCESS' or tx.amount != -share.wallet_deduction
            or tx.balance_after != tx.balance_before + tx.amount or tx.reversal_of_id or tx.reversals.exists()
            or tx.operator_account_id != second.operator_id or tx.operator_id != refund.operator_id)):
        raise ValidationError('后续退款陪玩钱包流水待核对')
    if bool(share.deposit_deficit_id) != bool(share.deposit_deduction):
        raise ValidationError('后续退款陪玩押金来源待核对')
    if share.deposit_deficit_id:
        from .ordinary_refund_receipt_evidence import _verify_direct_deposit
        _verify_direct_deposit(share.deposit_deficit, refund=refund, account_id=share.account_id,
                               user_id=share.assignment.provider_id, amount=share.deposit_deduction, addon=False)
    sources = list(order.funding_allocations.order_by('pk'))
    returned = plan_refund([RefundablePart(item.pk, item.kind, item.amount) for item in sources], refund.amount)
    for kind, key in (('PREPAID', 'returned_principal'), ('GIFT', 'returned_gift'),
                      ('COMPENSATION', 'returned_compensation')):
        if estimate.get(key) != sum(returned[item.pk] for item in sources if item.kind == kind):
            raise ValidationError('追加退款原付款来源待核对')
    combined = original['combined']
    provider = combined['provider_income_units'] - share.pending_deduction
    inviter = (order.amount - refund.amount) * (order.inviter_commission_rate_snapshot
        if order.inviter_id or order.inviter_account_id else 0) // 100
    margin = (combined['net_principal_units'] - estimate['returned_principal']
              + combined['planned_provider_external_units'] + refund.recovered_amount - provider - inviter)
    rounding = first.rounding_to_store_units + sum(order.addon_refunds.filter(
        created_at__lt=refund.created_at).values_list('rounding_surplus', flat=True))
    required = max(0, rounding - margin)
    release = max(allocated - required, 0)
    if (estimate.get('refund_units') != refund.amount or estimate.get('provider_income') != provider
            or estimate.get('inviter_income') != inviter or estimate.get('store_income') != margin + required
            or estimate.get('required_funding') != required or estimate.get('allocated_funding') != allocated
            or estimate.get('funding_to_release') != release or run.required_amount != required
            or run.allocated_before != allocated or run.released_amount != release
            or run.ordinary_refund_hash != release_digest(run, run.ordinary_refund_snapshot)):
        raise ValidationError('零退款后追加退款补资计算待核对')
    expected = {'followup_link_id': link.pk, 'execution_id': second.pk, 'refund_id': refund.pk,
        'prior_facts_hash': projection['prior_facts_hash'], 'required_units': required,
        'allocated_before': allocated, 'release_units': release}
    parts = plan_release(allocations=normalized, amount=release)
    if (run.ordinary_refund_snapshot != expected
            or [(item.allocation_id, item.amount) for item in run.releases.order_by('-allocation_id')]
               != [(item.allocation_id, item.amount) for item in parts]
            or not run.releases.filter(pk=row.pk, allocation=witness, amount=row.amount).exists()):
        raise ValidationError('零退款后到账释放与快照不一致')
    return ('RELEASE', f'order:{witness.pk}', f'ordinary-followup-release:{row.pk}', row.amount,
            _digest(['ordinary-followup-release', row.pk, row.amount, witness.pk, witness.receipt_id,
                     run.pk, run.negotiation_id, run.ordinary_refund_hash, run.allocation_snapshot]))
