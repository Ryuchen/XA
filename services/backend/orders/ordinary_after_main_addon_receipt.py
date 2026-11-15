"""Receipt-local proof for a restricted ordinary post-main add-on refund."""
from dataclasses import asdict, replace
from contextvars import ContextVar

from django.core.exceptions import ValidationError


_verification_state = ContextVar('ordinary_after_main_addon_verification', default=None)


def verified_release(run, witness_receipt_id, *, order_locked=False):
    """Memoize only within one receipt-local reconstruction, never across reads."""
    if order_locked:
        from django.conf import settings
        if (getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is True
                or getattr(settings, 'RECEIPT_USAGE_LEDGER_INTERNAL_TESTING', False) is True):
            raise ValidationError('启用到账用途账本时必须按到账核验')
    state = _verification_state.get()
    token = None
    if state is None:
        state = {}
        token = _verification_state.set(state)
    key = (run.pk, witness_receipt_id, order_locked)
    try:
        if state.get(key) is True:
            return run
        if key in state:
            raise ValidationError('普通加购退款资金版本循环引用')
        state[key] = False
        result = _verified_release(run, witness_receipt_id, order_locked=order_locked)
        state[key] = True
        return result
    finally:
        if token is not None:
            _verification_state.reset(token)


def _verified_release(run, witness_receipt_id, *, order_locked=False):
    from engagement.funding_plan import RefundablePart, plan_refund
    from users.receipt_usage_sources import _addon_release_facts, _require_current_unlocked_reads, order_facts
    from users.models import PassReceiptUsageState
    from .addon_funding_history import combined_at
    from .addon_payment import verify_receipt
    from .models import (NegotiationExecution, OrderAddonFundingRelease, OrderAddonFundingReleaseRun, OrderAddonPayment,
                         OrderAddonRefund, OrderPointsFundingAllocation)
    from .ordinary_addon_after_main_release import _digest
    from .ordinary_refund_receipt_evidence import verified_funding, verified_shares
    from .points_funding_allocation import plan_release
    from .points_negotiation_preview import funding_after_refund
    from .refunded_addon_funding import combine_refunded_history

    if order_locked and witness_receipt_id is not None:
        raise ValidationError('订单锁核验不能指定单笔到账')
    if witness_receipt_id is not None:
        _require_current_unlocked_reads()
        from users.receipt_usage_ledger import _locked_receipt
        _locked_receipt(witness_receipt_id)
    else:
        # Empty historical allocations have no receipt to lock or fabricate.
        # This variant is only used from the order/financial transaction.
        from stores.db import transaction
        from .financial_lock import lock_order_money
        from .models import Order
        if not transaction.get_connection().in_atomic_block:
            raise ValidationError('空补资退款核验须在订单事务内执行')
        Order.objects.select_for_update().get(pk=run.refund.order_id)
        lock_order_money()
        if not order_locked and OrderPointsFundingAllocation.objects.filter(service_order_id=run.refund.order_id,
                                                        created_at__lt=run.created_at).exists():
            raise ValidationError('退款历史并非空补资分配')
    refund = run.refund
    if not refund or not run.created_at or refund.created_at >= run.created_at:
        raise ValidationError('普通加购退款资金事件时序异常')
    order, payment, proposal = refund.order, refund.payment, refund.proposal
    from .negotiation_zero import marked_execution_id
    zero_execution_id = marked_execution_id(order)
    from .addon_refund_share_allocation import verify as verify_share_allocations
    verify_share_allocations(refund)
    from .models import PointsHandoverRefundInvalidation
    from .ordinary_pending_handover_refund import capture as capture_invalidation
    from .points_handover_refund_invalidation import digest as invalidation_digest
    invalidations = PointsHandoverRefundInvalidation.objects.filter(
        handover__payment__intent__order=order, refund__created_at__lte=refund.created_at).select_related(
            'handover__payment__intent__order', 'handover__source_segment', 'handover__target_segment', 'refund')
    for event in invalidations:
        if (not isinstance(event.snapshot, dict) or event.snapshot.get('kind') != 'ORDINARY_PENDING_REFUND_V1'
                or event.evidence_hash != invalidation_digest(event.snapshot)
                or event.created_at < event.refund.created_at
                or capture_invalidation(event.handover, event.refund) != event.snapshot):
            raise ValidationError('退款导致接替失效的历史异常')
    main_runs = list(OrderAddonFundingReleaseRun.objects.filter(negotiation__refund__order=order,
                     created_at__lt=refund.created_at)
                     .select_related('negotiation__refund', 'negotiation__proposal').order_by('pk')[:2])
    refunds = list(OrderAddonRefund.objects.filter(order=order, created_at__lte=run.created_at)
                   .select_related('payment__intent', 'proposal').order_by('created_at', 'pk'))
    if (len(main_runs) > 1 or not refunds or refunds[-1].pk != refund.pk
            or len({row.created_at for row in refunds}) != len(refunds)
            or order.refund_records.filter(created_at__lte=run.created_at).count() > 2
            or payment.intent.order_id != order.pk or proposal.payment_id != payment.pk
            or proposal.status != 'ACCEPTED' or proposal.amount != refund.amount
            or (refund.addon_pending_deduction + refund.main_pending_deduction
                + refund.wallet_deduction + refund.deposit_deduction != refund.amount + refund.rounding_surplus)):
        raise ValidationError('普通主单后加购退款尚不在核验范围')
    if order.providers.filter(settled_at__lte=refund.created_at).exists():
        from .ordinary_refunded_addon_paid_receipt import verify as verify_paid_refund
        verify_paid_refund(run)
    if main_runs:
        first = main_runs[0]
        verified_funding(first)
    else:
        from datetime import timedelta
        from types import SimpleNamespace
        executions = list(NegotiationExecution.objects.filter(refund__order=order,
            refund__created_at__lt=refund.created_at).select_related('refund__order').order_by('pk')[:2])
        if not executions and zero_execution_id:
            executions = list(NegotiationExecution.objects.filter(pk=zero_execution_id, zero_order=order,
                termination_processed_at__lt=refund.created_at).select_related('zero_order', 'proposal'))
        if len(executions) != 1 or not executions[0].termination_processed_at:
            raise ValidationError('首次退款资金时点缺失')
        execution = executions[0]
        boundary = execution.termination_processed_at + timedelta(microseconds=1)
        is_zero_first = bool(execution.zero_order_id and not execution.refund_id)
        if (boundary >= refund.created_at or (not is_zero_first and OrderPointsFundingAllocation.objects.filter(
                service_order=order, created_at__lte=boundary).exists())):
            raise ValidationError('首次退款零分配时序异常')
        first = SimpleNamespace(pk=None, created_at=boundary, negotiation=execution,
            negotiation_id=execution.pk, allocated_before=0)
        if is_zero_first:
            from .ordinary_zero_followup_evidence import verified_first
            verified_first(execution, boundary=refund.created_at)
        else:
            verified_funding(first, verify_snapshot=False)
    is_zero_first = bool(first.negotiation.zero_order_id and not first.negotiation.refund_id)
    main_at = (first.negotiation.termination_processed_at if is_zero_first
               else first.negotiation.refund.created_at)
    from .models import OrderOriginalShareTransfer
    first_transfer = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).order_by('created_at', 'pk').first()
    positive_pretransfer = bool(not is_zero_first and first_transfer and first.negotiation.termination_processed_at
                                and first.negotiation.termination_processed_at < first_transfer.created_at)
    if positive_pretransfer:
        from .ordinary_positive_pretransfer import verify as verify_positive_pretransfer
        origin = verify_positive_pretransfer(order, before=first_transfer.created_at)
        if origin.pk != first.negotiation_id:
            raise ValidationError('加购退款的转单前主单协商身份异常')
    pre_main = [row for row in refunds if row.created_at < main_at]
    post_main = [row for row in refunds if row.created_at > main_at]
    if (len(pre_main) + len(post_main) != len(refunds)
            or not post_main or post_main[-1].pk != refund.pk
            or (not first.pk and not is_zero_first and any(
                item.created_at >= first.negotiation.created_at for item in pre_main))):
        raise ValidationError('普通主单退款前后加购退款时序异常')
    previous_runs = []
    for prior_refund in pre_main:
        previous = OrderAddonFundingReleaseRun.objects.filter(refund=prior_refund).first()
        if not previous:
            if OrderPointsFundingAllocation.objects.filter(service_order=order,
                    created_at__lte=prior_refund.created_at).exists():
                raise ValidationError('普通主单前加购退款资金版本缺失')
            continue
        if (not previous.ordinary_prestart_snapshot or not previous.ordinary_prestart_hash
                or previous.created_at <= prior_refund.created_at or previous.created_at >= main_at):
            raise ValidationError('普通主单前加购退款资金版本异常')
        from .addon_funding_release import verify_run
        verify_run(previous, _skip_usage=True, witness_receipt_id=witness_receipt_id)
        previous_runs.append(previous.pk)
    previous_at = first.created_at
    for prior_refund in post_main[:-1]:
        previous = OrderAddonFundingReleaseRun.objects.filter(refund=prior_refund).first()
        if (prior_refund.created_at <= previous_at or not previous
                or previous.created_at <= prior_refund.created_at or previous.created_at >= refund.created_at):
            raise ValidationError('普通加购此前退款版本或责任人异常')
        verified_release(previous, witness_receipt_id, order_locked=order_locked)
        previous_runs.append(previous.pk)
        previous_at = previous.created_at
    if refund.created_at <= previous_at:
        raise ValidationError('普通加购退款与此前资金版本时序异常')
    verify_receipt(payment)
    buyer_tx = refund.buyer_transaction
    if (not buyer_tx or buyer_tx.wallet.account_id != order.customer_account_id
            or buyer_tx.amount != refund.amount or buyer_tx.tx_type != 'REFUND' or buyer_tx.status != 'SUCCESS'
            or buyer_tx.balance_after != buyer_tx.balance_before + buyer_tx.amount
            or buyer_tx.operator_account_id != refund.operator_id or buyer_tx.operator_id != refund.legacy_operator_id
            or buyer_tx.reversal_of_id or buyer_tx.reversals.exists()):
        raise ValidationError('普通加购退款老板实际流水异常')
    people = list(proposal.participants.order_by('account_id'))
    decisions = list(proposal.decisions.values_list('account_id', 'role', 'accepted', 'decided_at'))
    expected_decisions = {(payment.intent.buyer_id, 'BOSS', True)} | {
        (person.account_id, 'PROVIDER', True) for person in people}
    if (not people or len({person.account_id for person in people}) != len(people)
            or sum(person.burden for person in people) != refund.amount + refund.rounding_surplus
            or len(decisions) != len(expected_decisions)
            or {(a, role, answer) for a, role, answer, _ in decisions} != expected_decisions
            or not proposal.accepted_at or any(not at or at < proposal.created_at for _, _, _, at in decisions)
            or max(at for _, _, _, at in decisions) != proposal.accepted_at
            or proposal.accepted_at > refund.created_at):
        raise ValidationError('普通加购退款确认或责任异常')
    from .addon_handover import current_responsibilities
    from .addon_refund import _allocate_refund_basis, _allocate_segment_burden
    from .ordinary_refunded_team_handover import verify as verify_team_handover
    _, responsibilities = current_responsibilities(payment, before=refund.created_at, exclude_refund_id=refund.pk)
    original = {person.account_id: [row for row in responsibilities if row['provider_id'] == person.account_id]
                for person in people}
    weights = [(person.account_id, sum(row['basis'] for row in original[person.account_id])) for person in people]
    if any(not basis for _, basis in weights):
        raise ValidationError('加购退款陪玩份额异常')
    allocated = _allocate_refund_basis(refund.amount, weights)
    total_weight = sum(basis for _, basis in weights)
    historical_rounding = {}
    for earlier in refunds[:-1]:
        if earlier.payment_id != payment.pk:
            continue
        for person in earlier.proposal.participants.all():
            for entry in person.share_plan:
                key = (person.account_id, entry['segment_id'])
                historical_rounding[key] = historical_rounding.get(key, 0) + entry['burden'] - entry['refund_basis']
    payments = list(OrderAddonPayment.objects.filter(intent__order=order,
                    buyer_transaction__created_at__lt=refund.created_at)
                    .select_related('intent', 'buyer_transaction').order_by('pk'))
    payments.sort(key=lambda item: (item.pk != payment.pk, item.pk))
    for addon in payments:
        if addon.intent.segment.attribution.order_id != order.pk:
            raise ValidationError('加购付款履约归属异常')
        for handover in addon.handovers.filter(status='ACCEPTED', accepted_at__lt=refund.created_at):
            if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
                    created_at__lt=handover.created_at).exists():
                from .ordinary_transfer_pre_main_addon import verify_handover_at
                verify_handover_at(addon, handover, refund.created_at)
            elif handover.accepted_at < main_at:
                from .ordinary_team_pre_main_handover import verify as verify_pre_main_handover
                verify_pre_main_handover(handover)
            else:
                verify_team_handover(handover)
        verify_receipt(addon)
    if is_zero_first:
        from .ordinary_zero_followup_evidence import verified_first
        shares = verified_first(first.negotiation, boundary=refund.created_at)
    else:
        shares = verified_shares(first.negotiation)
    evidence = getattr(refund, 'zero_wallet_evidence', None)
    evidence_used = False
    participant_evidence = list(refund.participant_zero_wallet_evidence.select_related('wallet').order_by('participant_id'))
    evidence_by_person = {row.participant_id: row for row in participant_evidence}
    used_evidence = set()
    if evidence and participant_evidence:
        raise ValidationError('退款存在重复零余额凭证')
    for person in people:
        rows = original[person.account_id]
        basis = sum(row['basis'] for row in rows)
        by_segment = {}
        for row in rows:
            if not row['basis']:
                continue
            segment = by_segment.setdefault(row['segment_id'], {'basis': 0, 'income': 0})
            segment['basis'] += row['basis']
            segment['income'] += row['income']
        parts = _allocate_refund_basis(allocated[person.account_id],
                                       [(segment_id, row['basis']) for segment_id, row in sorted(by_segment.items())])
        burden = ((refund.amount * basis + total_weight * 10 - 1) // (total_weight * 10)) * 10
        expected_entries = []
        for segment_id, row in sorted(by_segment.items()):
            capacity = max(0, min(row['basis'], row['basis'] - historical_rounding.get((person.account_id, segment_id), 0)))
            expected_entries.append({'segment_id': segment_id, 'basis': row['basis'], 'income': row['income'],
                                     'refund_basis': parts[segment_id], 'burden_capacity': capacity})
        from .models import OrderParticipationSegment
        latest = list(OrderParticipationSegment.objects.filter(pk__in=parts)
                      .order_by('-started_at', '-accepted_at', '-pk').values_list('pk', flat=True))
        excess = _allocate_segment_burden(expected_entries, burden, latest)
        if (person.basis != basis or person.burden != burden or person.excess_burden != excess
                or person.share_plan != expected_entries):
            raise ValidationError('加购退款各陪玩分摊异常')
        remaining, expected_deductions = person.burden, []
        for addon in payments:
            _, addon_shares = current_responsibilities(addon, before=refund.created_at, exclude_refund_id=refund.pk)
            grouped = {}
            for row in addon_shares:
                if row['provider_id'] == person.account_id:
                    grouped[row['segment_id']] = grouped.get(row['segment_id'], 0) + row['income']
            for segment_id, available in sorted(grouped.items()):
                take = min(remaining, available)
                if take:
                    expected_deductions.append((addon.pk, segment_id, take))
                    remaining -= take
        deductions = list(person.earning_deductions.order_by('pk').values_list('payment_id', 'segment_id', 'amount'))
        if deductions != expected_deductions or sum(row[2] for row in deductions) != person.addon_pending_deduction:
            raise ValidationError('加购退款未按本人逐付款逐段收益承担')
        matching = [row for row in shares if row.account_id == person.account_id]
        assignments = list(order.providers.filter(provider_account_id=person.account_id)[:2])
        if len(matching) > 1 or len(assignments) != 1 or (matching and matching[0].assignment_id != assignments[0].pk):
            raise ValidationError('普通加购退款本人主单凭证缺失')
        assignment = assignments[0]
        from .ordinary_after_main_segment_income import deduction_at
        expected_parts = deduction_at(order, first.negotiation, shares, person.account_id,
                                      refund.created_at, remaining)
        expected_main = sum(amount for _, amount in expected_parts)
        main_parts = list(person.main_segment_deductions.order_by('pk').values_list('segment_id', 'amount'))
        if person.main_pending_deduction != expected_main or main_parts != expected_parts:
            raise ValidationError('加购退款主单逐段承担异常')
        remaining -= expected_main
        tx = person.provider_transaction
        if person.wallet_deduction:
            if (not tx or tx.wallet.account_id != person.account_id or tx.wallet.user_id != assignment.provider_id
                    or tx.order_id is not None or tx.tx_type != 'REFUND' or tx.status != 'SUCCESS'
                    or tx.amount != -person.wallet_deduction or tx.balance_before < 0
                    or tx.balance_after != tx.balance_before + tx.amount
                    or person.wallet_deduction != min(remaining, tx.balance_before)
                    or tx.operator_account_id != refund.operator_id or tx.operator_id != refund.legacy_operator_id
                    or tx.reversal_of_id or tx.reversals.exists()):
                raise ValidationError('加购退款陪玩钱包实际扣款异常')
        elif tx:
            raise ValidationError('零钱包扣款存在多余流水')
        remaining -= person.wallet_deduction
        deficits = list(refund.deposit_deficits.filter(account_id=person.account_id)
                        .select_related('profile', 'source_store_transaction__wallet__user'))
        if remaining != person.deposit_deduction or len(deficits) != (1 if remaining else 0):
            raise ValidationError('加购退款押金承担异常')
        if remaining:
            from .ordinary_refund_receipt_evidence import _verify_direct_deposit
            _verify_direct_deposit(deficits[0], refund=refund, account_id=person.account_id,
                                   user_id=assignment.provider_id, amount=remaining, addon=True)
        if remaining and not person.wallet_deduction:
            selected_evidence = evidence_by_person.get(person.pk) if participant_evidence else evidence
            if (not selected_evidence or (not participant_evidence and evidence_used)
                    or selected_evidence.account_id != person.account_id
                    or selected_evidence.wallet.account_id != person.account_id
                    or selected_evidence.wallet.user_id != assignment.provider_id
                    or selected_evidence.business_cycle != deficits[0].account_business_cycle
                    or not selected_evidence.business_cycle or selected_evidence.balance != 0
                    or not selected_evidence.is_active or selected_evidence.created_at < refund.created_at
                    or selected_evidence.created_at > run.created_at):
                raise ValidationError('退款本人零余额钱包事实异常')
            if participant_evidence:
                used_evidence.add(person.pk)
            else:
                evidence_used = True
    if evidence and not evidence_used:
        raise ValidationError('退款存在多余零余额钱包事实')
    if set(evidence_by_person) != used_evidence:
        raise ValidationError('退款存在多余陪玩零余额钱包事实')
    sources = list(payment.allocations.order_by('pk'))
    used_sources = {row.pk: 0 for row in sources}
    for prior_refund in refunds[:-1]:
        if prior_refund.payment_id != payment.pk:
            continue
        for entry in prior_refund.proposal.source_plan:
            if entry.get('allocation_id') not in used_sources or type(entry.get('amount_raw')) is not int:
                raise ValidationError('普通加购此前退款原付款来源异常')
            used_sources[entry['allocation_id']] += entry['amount_raw']
    planned = plan_refund([RefundablePart(row.pk, row.kind, row.amount, used_sources[row.pk])
                           for row in sources], refund.amount)
    if (proposal.source_plan != [{'allocation_id': row.pk, 'kind': row.kind, 'amount_raw': planned[row.pk]}
                                 for row in sources if planned[row.pk]]):
        raise ValidationError('普通加购退款原付款来源异常')
    allocations = list(OrderPointsFundingAllocation.objects.filter(service_order=order,
        created_at__lt=run.created_at).select_related('receipt').order_by('pk'))
    snapshot = run.allocation_snapshot
    later_witness = bool(witness_receipt_id is not None and OrderPointsFundingAllocation.objects.filter(service_order=order,
        receipt_id=witness_receipt_id, created_at__gt=run.created_at).exists())
    if (not isinstance(snapshot, list) or len(allocations) != len(snapshot)
            or (witness_receipt_id is not None and witness_receipt_id not in {x.receipt_id for x in allocations} and not later_witness)
            or OrderPointsFundingAllocation.objects.filter(service_order=order, created_at=run.created_at).exists()):
        raise ValidationError('普通加购退款的到账范围异常')
    state = PassReceiptUsageState.objects.filter(receipt_id=witness_receipt_id).first() if witness_receipt_id is not None else None
    if witness_receipt_id is not None and not state:
        raise ValidationError('普通加购退款到账用途缺失')
    from .ordinary_followup_receipt import _allocation_source
    payment_runs = list(OrderAddonFundingReleaseRun.objects.filter(payment__in=payments,
        created_at__gt=first.created_at, created_at__lt=refund.created_at).select_related('payment__intent'))
    from .ordinary_refund_addon_payment_release import KIND, verify as verify_payment
    for payment_run in payment_runs:
        if not isinstance(payment_run.ordinary_refund_snapshot, dict) or payment_run.ordinary_refund_snapshot.get('kind') != KIND:
            raise ValidationError('普通加购付款资金版本异常')
        verify_payment(payment_run)
    previous_runs.extend(item.pk for item in payment_runs)
    pre_payment_runs = list(OrderAddonFundingReleaseRun.objects.filter(payment__in=payments,
        created_at__lt=main_at).select_related('payment__intent'))
    for payment_run in pre_payment_runs:
        if not payment_run.ordinary_prestart_snapshot or not payment_run.ordinary_prestart_hash:
            raise ValidationError('主单退款前加购付款资金版本异常')
        from .addon_funding_release import verify_run
        verify_run(payment_run, _skip_usage=True)
    previous_runs.extend(item.pk for item in pre_payment_runs)
    handover_runs = list(OrderAddonFundingReleaseRun.objects.filter(handover__payment__in=payments,
        created_at__lt=refund.created_at).select_related('handover__payment__intent'))
    from .ordinary_refund_addon_payment_release import HANDOVER_KIND
    for handover_run in handover_runs:
        if handover_run.handover.accepted_at < main_at:
            if not handover_run.ordinary_prestart_snapshot or not handover_run.ordinary_prestart_hash:
                raise ValidationError('主单退款前加购接替资金版本异常')
            from .addon_funding_release import verify_run
            verify_run(handover_run, _skip_usage=True)
        else:
            if (not isinstance(handover_run.ordinary_refund_snapshot, dict)
                    or handover_run.ordinary_refund_snapshot.get('kind') != HANDOVER_KIND):
                raise ValidationError('加购接替资金版本异常')
            verify_payment(handover_run)
    previous_runs.extend(item.pk for item in handover_runs)
    normalized = []
    for allocation, item in zip(allocations, snapshot):
        _allocation_source(allocation, order, run.created_at)
        if allocation.receipt_id == witness_receipt_id:
            event = order_facts(allocation)
            if not state.events.filter(kind=event[0], allocation_key=event[1], request_key=event[2],
                                       amount=event[3], source_digest=event[4]).exists():
                raise ValidationError('普通加购退款补资用途缺失')
        prior = list(OrderAddonFundingRelease.objects.filter(allocation=allocation, run__created_at__lt=run.created_at)
                     .select_related('run', 'allocation').order_by('pk'))
        if (any(row.run_id not in {first.pk, *previous_runs} for row in prior)
                or not isinstance(item, list) or len(item) != 4
                or item[:2] != [allocation.pk, allocation.amount] or item[3] != []
                or item[2] != sum(row.amount for row in prior)):
            raise ValidationError('普通加购退款此前释放版本异常')
        for row in prior:
            if allocation.receipt_id != witness_receipt_id:
                continue
            facts = _addon_release_facts(row, witness_receipt_id=witness_receipt_id)
            if not state.events.filter(
                    kind=facts[0], allocation_key=facts[1], request_key=facts[2], amount=facts[3],
                    source_digest=facts[4]).exists():
                raise ValidationError('普通加购退款此前释放到账用途缺失')
        normalized.append([allocation.pk, allocation.amount, item[2]])
    before = sum(item[1] - item[2] for item in normalized)
    execution = first.negotiation
    if is_zero_first:
        from .ordinary_zero_followup_evidence import verified_funding_at
        reconstructed = verified_funding_at(execution, boundary=run.created_at,
                                             allocated_before=before, allow_post_addons=True,
                                             witness_receipt_id=witness_receipt_id, current_refund_id=refund.pk)
        required = reconstructed['combined']['required_units']
        facts = {'kind': 'ordinary_addon_after_main_refund_v1', 'refund_id': refund.pk,
                 'combined': reconstructed['combined'], 'addon_history': reconstructed['addon_history']}
        if evidence:
            facts['zero_wallet'] = [evidence.pk, evidence.wallet_id, evidence.account_id, evidence.business_cycle,
                                    evidence.balance, evidence.frozen_amount, evidence.is_active,
                                    evidence.created_at.isoformat()]
        if participant_evidence:
            facts['participant_zero_wallets'] = [[row.pk, row.participant_id, row.wallet_id, row.account_id,
                row.business_cycle, row.balance, row.frozen_amount, row.is_active, row.created_at.isoformat()]
                for row in participant_evidence]
        amount = max(before - required, 0)
        parts = plan_release(allocations=normalized, amount=amount)
        if (facts != run.ordinary_refund_snapshot or run.ordinary_refund_hash != _digest(run, facts)
                or run.allocated_before != before or run.required_amount != required or run.released_amount != amount
                or list(run.releases.order_by('-allocation_id').values_list('allocation_id', 'amount')) !=
                   [(part.allocation_id, part.amount) for part in parts]):
            raise ValidationError('零退款后加购退款资金释放凭证异常')
        return run
    funding = list(order.funding_allocations.all())
    paid = {kind: sum(x.amount for x in funding if x.kind == kind) for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
    original_return = plan_refund([RefundablePart(x.pk, x.kind, x.amount) for x in funding], execution.refund.amount)
    returned = {kind: sum(original_return[x.pk] for x in funding if x.kind == kind) for kind in paid}
    if positive_pretransfer:
        from types import SimpleNamespace
        saved = {item['account_id']: item for item in execution.refund.negotiated_allocation}
        if set(saved) != set(original_income):
            raise ValidationError('加购退款的原陪玩分配快照异常')
        assignments = [SimpleNamespace(pk=saved[account]['share_id'], provider_account_id=account,
                                       provider_income=income) for account, income in sorted(original_income.items())]
    else:
        assignments = list(order.providers.order_by('pk'))
    pending = {x.account_id: x.pending_deduction for x in shares}
    retained = {x.provider_account_id: x.provider_income - pending.get(x.provider_account_id, 0) for x in assignments}
    rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    main = funding_after_refund(principal_units=paid['PREPAID'], gift_units=paid['GIFT'],
        compensation_units=paid['COMPENSATION'], returned_prepaid_units=returned['PREPAID'],
        returned_gift_units=returned['GIFT'], returned_compensation_units=returned['COMPENSATION'],
        provider_income_units=order.provider_income, pending_deduction_units=sum(pending.values()),
        provider_external_units=execution.refund.recovered_amount, inviter_rate=rate, allocated_units=before)
    segment_shares = [(x.pk, x.provider_account_id, 0, 0, retained[x.provider_account_id]) for x in assignments]
    history = combined_at(order, main, run.created_at, segment_shares=segment_shares)
    combined = combine_refunded_history(main, history, main_retained_by_account=retained)
    rounding = execution.rounding_to_store_units + sum(row.rounding_surplus for row in refunds)
    if type(rounding) is not int or rounding < 0:
        raise ValidationError('普通主单退款取整异常')
    extra = max(rounding - combined.store_income_units, 0)
    if extra:
        required = combined.required_units + extra
        combined = replace(combined, store_income_units=combined.store_income_units + extra,
            required_units=required, release_units=max(before - required, 0), remaining_units=max(required - before, 0))
    facts = {'kind': 'ordinary_addon_after_main_refund_v1', 'refund_id': refund.pk,
             'combined': asdict(combined), 'addon_history': list(history['addon_history'])}
    if evidence:
        facts['zero_wallet'] = [evidence.pk, evidence.wallet_id, evidence.account_id, evidence.business_cycle,
                                evidence.balance, evidence.frozen_amount, evidence.is_active,
                                evidence.created_at.isoformat()]
    if participant_evidence:
        facts['participant_zero_wallets'] = [[row.pk, row.participant_id, row.wallet_id, row.account_id,
            row.business_cycle, row.balance, row.frozen_amount, row.is_active, row.created_at.isoformat()]
            for row in participant_evidence]
    amount = max(before - combined.required_units, 0)
    parts = plan_release(allocations=normalized, amount=amount)
    if (facts != run.ordinary_refund_snapshot or run.ordinary_refund_hash != _digest(run, facts)
            or run.allocated_before != before or run.required_amount != combined.required_units
            or run.released_amount != amount
            or list(run.releases.order_by('-allocation_id').values_list('allocation_id', 'amount')) !=
               [(part.allocation_id, part.amount) for part in parts]):
        raise ValidationError('普通加购退款资金释放凭证异常')
    return run
