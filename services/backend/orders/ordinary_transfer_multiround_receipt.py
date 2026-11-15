"""Receipt-local replay of later refunds against a real first agreement."""
from collections import defaultdict
from contextvars import ContextVar
from dataclasses import asdict, replace

from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as RestValidationError


_verification_state = ContextVar('ordinary_transfer_multiround_verification', default=None)


def verify(run, witness_receipt_id):
    state = _verification_state.get()
    token = None
    if state is None:
        state = {}
        token = _verification_state.set(state)
    key = (run.pk, witness_receipt_id)
    try:
        if state.get(key) is True:
            return run
        if key in state:
            raise ValidationError('多轮退款到账版本循环引用')
        state[key] = False
        result = _verify(run, witness_receipt_id)
        state[key] = True
        return result
    finally:
        if token is not None:
            _verification_state.reset(token)


def _verify(run, witness_receipt_id):
    from console.deposit_clearance import _verified_refund_execution
    from engagement.funding_plan import RefundablePart, plan_refund
    from engagement.funding_wallet import validate_order_funding_sources
    from users.models import PassReceiptUsageState
    from users.receipt_usage_ledger import _locked_receipt
    from users.receipt_usage_sources import _addon_release_facts, _require_current_unlocked_reads, order_facts
    from wallet.models import Transaction
    from .models import (NegotiationExecution, OrderAddonFundingRelease, OrderAddonFundingReleaseRun,
        OrderAddonPayment, OrderOriginalShareTransfer, OrderPointsFundingAllocation, OrdinaryTransferFollowupRound)
    from .ordinary_transfer_followup import _hash
    from .ordinary_transfer_event_scope import segment_lines_at
    from .ordinary_followup_receipt import _allocation_source
    from .ordinary_refund_receipt_evidence import _verify_direct_deposit
    from .ordinary_refunded_addon_release import _digest
    from .points_funding_allocation import plan_release
    from .points_negotiation_preview import funding_after_refund

    _locked_receipt(witness_receipt_id)
    _require_current_unlocked_reads()
    execution = run.negotiation
    if not execution or not execution.refund_id:
        raise ValidationError('追加退款资金版本缺少实际退款')
    refund, order = execution.refund, execution.refund.order
    row = OrdinaryTransferFollowupRound.objects.select_related('previous', 'proposal').filter(
        execution=execution, order=order).first()
    if not row or row.sequence < 2 or not execution.termination_processed_at or execution.termination_processed_at >= run.created_at:
        raise ValidationError('多轮退款资金事件时序异常')
    rounds = list(OrdinaryTransferFollowupRound.objects.filter(order=order, execution__isnull=False,
        sequence__lte=row.sequence).select_related('execution__refund', 'proposal').order_by('sequence'))
    if len(rounds) != row.sequence or [item.sequence for item in rounds] != list(range(1, row.sequence + 1)):
        raise ValidationError('多轮协商顺序不完整')
    first = rounds[0].previous
    from .models import OrderTransferNegotiationEvidence
    first_evidence = OrderTransferNegotiationEvidence.objects.filter(execution=first, order=order).first()
    from .ordinary_zero_pretransfer import verify as verify_pretransfer_zero
    transfer = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).order_by('created_at', 'pk').first()
    zero_pretransfer = not first_evidence and not first.refund_id
    positive_pretransfer = not first_evidence and bool(first.refund_id)
    if zero_pretransfer:
        verified_first = verify_pretransfer_zero(order, before=transfer.created_at) if transfer else None
        if not first.zero_order_id or first.refund_id or not verified_first or verified_first.pk != first.pk:
            raise ValidationError('首次零退款和后续转单来源异常')
    elif positive_pretransfer:
        from .ordinary_positive_pretransfer import verify as verify_positive_pretransfer
        verified_first = verify_positive_pretransfer(order, before=transfer.created_at) if transfer else None
        if not verified_first or verified_first.pk != first.pk:
            raise ValidationError('首次正退款与后续转单来源异常')
    else:
        from .negotiation_transfer_evidence import _digest as evidence_digest
        if (not transfer or first_evidence.snapshot_hash != evidence_digest(first_evidence.snapshot)
                or first_evidence.snapshot.get('provider_terms') != first.proposal.provider_terms
                or first_evidence.snapshot.get('lines') != [asdict(item) for item in segment_lines_at(order, first.created_at)]):
            raise ValidationError('转单后首次协商的逐段凭证异常')
        if first.refund_id:
            from .negotiation_termination import verify_termination_evidence
            if not _verified_refund_execution(first.refund):
                raise ValidationError('转单后首次退款凭证异常')
            try:
                verify_termination_evidence(first)
            except RestValidationError as exc:
                raise ValidationError('转单后首次退款结案凭证异常') from exc
        else:
            from .ordinary_transfer_zero_receipt import verify as verify_transfer_zero
            verify_transfer_zero(first)
    prior, previous = [first], first
    for current in rounds:
        if (current.previous_id != previous.pk or current.facts_hash != _hash(current.proposal_id, current.facts)
                or current.facts.get('prior_execution_ids') != [item.pk for item in prior]
                or current.facts.get('previous_execution_id') != previous.pk
                or not current.created_at or not previous.termination_processed_at
                or current.created_at < previous.termination_processed_at
                or not current.execution.termination_processed_at
                or current.execution.termination_processed_at < current.execution.accepted_at
                or current.execution.termination_processed_at >= run.created_at):
            raise ValidationError('多轮协商事实或先后顺序异常')
        if current.execution.refund_id:
            if not _verified_refund_execution(current.execution.refund):
                raise ValidationError('多轮实际退款凭证异常')
            from .negotiation_termination import verify_termination_evidence
            try:
                verify_termination_evidence(current.execution)
            except RestValidationError as exc:
                raise ValidationError('多轮退款结案审计异常') from exc
        else:
            from .ordinary_transfer_followup_zero import verify_termination
            try:
                verify_termination(current.execution, lock=False)
            except RestValidationError as exc:
                raise ValidationError('多轮零退款结案凭证异常') from exc
        if current.pk != row.pk:
            prior.append(current.execution)
        previous = current.execution
    receipts = [*prior, execution]
    lines = segment_lines_at(order, row.created_at)
    if [asdict(item) for item in lines] != row.facts.get('segment_lines'):
        raise ValidationError('多轮协商履约段与确认快照不一致')
    from .addon_main_deduction import deductions_at
    from .addon_main_segment_deduction import verified_deductions
    from .models import OrderParticipationSegment
    if zero_pretransfer or positive_pretransfer:
        closed_segments = set(OrderParticipationSegment.objects.filter(attribution__order=order,
            provider_account_id__in=first.proposal.affected_accounts, released_at=first.accepted_at,
            started_at__isnull=False).values_list('pk', flat=True))
    else:
        closed_segments = set(first_evidence.snapshot.get('selected_segment_ids', []))
        if sorted(closed_segments) != sorted(first.proposal.affected_segment_ids or []):
            raise ValidationError('首次协商受影响履约段异常')
        first_lines = {item.segment_id: item for item in segment_lines_at(order, first.created_at)}
        if any(pk not in first_lines or first_lines[pk].account_id not in first.proposal.affected_accounts
               for pk in closed_segments):
            raise ValidationError('首次协商履约段归属异常')
        first_deductions = verified_deductions(order, before=first.created_at)
        for term in first.proposal.provider_terms:
            owned = [first_lines[pk] for pk in closed_segments if pk in first_lines
                     and first_lines[pk].account_id == term['account_id']]
            if (not owned or sum(item.income - first_deductions.get(item.segment_id, 0) for item in owned)
                    != term['original_income_units']):
                raise ValidationError('首次协商的本人实际收益异常')
    if not closed_segments:
        raise ValidationError('首次零退款已终止履约段缺失')
    for previous_round in rounds[:-1]:
        historical = segment_lines_at(order, previous_round.created_at)
        if [asdict(item) for item in historical] != previous_round.facts.get('segment_lines'):
            raise ValidationError('历史协商的实际履约段异常')
        by_segment = {item.segment_id: item for item in historical}
        historical_deductions = verified_deductions(order, before=previous_round.created_at)
        selected_before = previous_round.facts.get('segments')
        if (not isinstance(selected_before, dict)
                or sorted(pk for ids in selected_before.values() for pk in ids)
                   != previous_round.proposal.affected_segment_ids):
            raise ValidationError('历史协商的受影响段异常')
        previous_ids = {pk for ids in selected_before.values() for pk in ids}
        if previous_ids & closed_segments:
            raise ValidationError('历史协商重新选择了已终止履约段')
        closed_segments.update(previous_ids)
        for term in previous_round.proposal.provider_terms:
            ids = selected_before.get(str(term['account_id']), [])
            if (not ids or any(pk not in by_segment or by_segment[pk].account_id != term['account_id'] for pk in ids)
                    or sum(by_segment[pk].income - historical_deductions.get(pk, 0) for pk in ids)
                       != term['original_income_units']):
                raise ValidationError('历史协商的本人收益异常')
    deductions = deductions_at(order, before=row.created_at)
    if {str(key): value for key, value in deductions.items()} != row.facts.get('addon_main_deductions'):
        raise ValidationError('多轮协商前加购扣减异常')
    segments = {item.segment_id: item for item in lines}
    segment_deductions = verified_deductions(order, before=row.created_at)
    selected = row.facts.get('segments')
    if not isinstance(selected, dict) or sorted(pk for ids in selected.values() for pk in ids) != execution.proposal.affected_segment_ids:
        raise ValidationError('多轮退款实际履约段范围异常')
    if {pk for ids in selected.values() for pk in ids} & closed_segments:
        raise ValidationError('多轮退款不能重新选择已终止履约段')
    for term in execution.proposal.provider_terms:
        account = term['account_id']
        ids = selected.get(str(account), [])
        if (not ids or any(pk not in segments or segments[pk].account_id != account for pk in ids)
                or sum(segments[pk].income - segment_deductions.get(pk, 0) for pk in ids) != term['original_income_units']):
            raise ValidationError('多轮退款本次受影响收益异常')
    outstanding = defaultdict(int)
    for item in lines:
        outstanding[item.account_id] += item.income
    for account, amount in deductions.items():
        outstanding[account] -= amount
    for receipt in prior:
        for share in receipt.shares.all():
            outstanding[share.account_id] -= share.pending_deduction
    if ({str(key): value for key, value in sorted(outstanding.items())} != row.facts.get('outstanding_income')
            or any(value < 0 for value in outstanding.values())):
        raise ValidationError('多轮退款各人剩余收益异常')
    sources = list(order.funding_allocations.select_related('lot').order_by('pk'))
    try:
        validate_order_funding_sources(order, sources)
    except RestValidationError as exc:
        raise ValidationError('多轮退款付款来源异常') from exc
    kinds = ('PREPAID', 'GIFT', 'COMPENSATION')
    before = {item['id']: item for item in row.facts['funding_sources']}
    prior_amount = sum(item.refund.amount for item in prior if item.refund_id)
    planned_before = plan_refund([RefundablePart(item.pk, item.kind, item.amount) for item in sources], prior_amount)
    all_actual_refunds = list(order.refund_records.order_by('created_at', 'pk'))
    planned_current = plan_refund([RefundablePart(item.pk, item.kind, item.amount) for item in sources],
                                  sum(item.amount for item in all_actual_refunds))
    if (set(before) != {item.pk for item in sources} or row.facts.get('prior_refund_units') != prior_amount
            or any(before[item.pk]['lot_id'] != item.lot_id or before[item.pk]['kind'] != item.kind
                   or before[item.pk]['amount'] != item.amount or before[item.pk]['refunded_amount'] != planned_before[item.pk]
                   or item.refunded_amount != planned_current[item.pk] for item in sources)):
        raise ValidationError('多轮退款历史原付款构成异常')
    delta = plan_refund([RefundablePart(item.pk, item.kind, item.amount, planned_before[item.pk]) for item in sources], refund.amount)
    paid = {kind: sum(item.amount for item in sources if item.kind == kind) for kind in kinds}
    returned = {kind: sum(planned_before[item.pk] + delta[item.pk] for item in sources if item.kind == kind) for kind in kinds}
    payments = list(Transaction.objects.filter(order=order, tx_type='PAY', status='SUCCESS').select_related('wallet'))
    if (sum(paid.values()) != order.amount or len(payments) != 1 or payments[0].amount != -order.amount
            or payments[0].wallet.account_id != order.customer_account_id or payments[0].wallet.user_id != order.customer_id
            or payments[0].balance_after != payments[0].balance_before + payments[0].amount
            or payments[0].reversal_of_id or payments[0].reversals.exists()):
        raise ValidationError('多轮退款原付款流水异常')
    external = 0
    for receipt in receipts:
        if receipt.proposal.ordinary_segment_pending_plan is not None:
            from .ordinary_retained_segment_shares import verify_execution_plan
            try:
                verify_execution_plan(receipt)
            except ValueError as exc:
                raise ValidationError('多轮协商逐段承担凭证异常') from exc
        shares = list(receipt.shares.select_related('assignment', 'wallet_transaction__wallet',
            'deposit_deficit__profile', 'deposit_deficit__source_store_transaction__wallet__user'))
        terms = {item['account_id']: item for item in receipt.proposal.provider_terms}
        if not shares or set(terms) != {item.account_id for item in shares}:
            raise ValidationError('多轮协商确认人异常')
        for share in shares:
            term, tx = terms[share.account_id], share.wallet_transaction
            if (share.assignment.order_id != order.pk or share.assignment.provider_account_id != share.account_id
                    or share.original_income != term['original_income_units'] or share.retained_income != term['retained_units']
                    or share.burden != term['burden_units'] or share.burden != share.pending_deduction + share.wallet_deduction + share.deposit_deduction
                    or share.original_income != share.pending_deduction + share.retained_income
                    or bool(tx) != bool(share.wallet_deduction) or bool(share.deposit_deficit_id) != bool(share.deposit_deduction)):
                raise ValidationError('多轮退款本人承担异常')
            if tx and (tx.order_id != order.pk or tx.wallet.account_id != share.account_id
                    or tx.wallet.user_id != share.assignment.provider_id or tx.operator_account_id != receipt.operator_id
                    or (receipt.refund_id and tx.operator_id != receipt.refund.operator_id)
                    or tx.amount != -share.wallet_deduction
                    or tx.tx_type != 'REFUND' or tx.status != 'SUCCESS' or tx.balance_after != tx.balance_before + tx.amount
                    or tx.reversal_of_id or tx.reversals.exists()):
                raise ValidationError('多轮退款本人实际钱包流水异常')
            if share.deposit_deduction:
                _verify_direct_deposit(share.deposit_deficit, refund=receipt.refund, account_id=share.account_id,
                    user_id=share.assignment.provider_id, amount=share.deposit_deduction, addon=False)
            external += share.wallet_deduction + share.deposit_deduction
        if sum(item.burden for item in shares) != (receipt.refund.amount if receipt.refund_id else 0) + receipt.rounding_to_store_units:
            raise ValidationError('多轮退款承担合计异常')
    allocations = list(OrderPointsFundingAllocation.objects.filter(service_order=order,
        created_at__lt=run.created_at).select_related('receipt').order_by('pk'))
    if [item.pk for item in allocations] != [item[0] for item in run.allocation_snapshot]:
        raise ValidationError('多轮退款本单到账分配异常')
    if (witness_receipt_id not in {item.receipt_id for item in allocations}
            and not OrderPointsFundingAllocation.objects.filter(service_order=order, receipt_id=witness_receipt_id,
                                                                 created_at__gt=run.created_at).exists()):
        raise ValidationError('多轮退款到账见证不属于该订单')
    state = PassReceiptUsageState.objects.filter(receipt_id=witness_receipt_id).first()
    if not state:
        raise ValidationError('多轮退款到账用途账本缺失')
    for allocation, entry in zip(allocations, run.allocation_snapshot):
        _allocation_source(allocation, order, run.created_at)
        releases = list(OrderAddonFundingRelease.objects.filter(allocation=allocation,
            run__created_at__lt=run.created_at).select_related('run', 'allocation__receipt').order_by('pk'))
        if entry != [allocation.pk, allocation.amount, sum(item.amount for item in releases), []]:
            raise ValidationError('多轮退款此前释放金额异常')
        if allocation.receipt_id == witness_receipt_id:
            events = [order_facts(allocation)] + [_addon_release_facts(item, witness_receipt_id=witness_receipt_id)
                                                   for item in releases]
            if any(not state.events.filter(kind=kind, allocation_key=key, request_key=request, amount=amount,
                                           source_digest=digest).exists() for kind, key, request, amount, digest in events):
                raise ValidationError('多轮退款此前到账用途异常')
    if run.allocated_before != sum(item[1] - item[2] for item in run.allocation_snapshot):
        raise ValidationError('多轮退款到账分配总额异常')
    retained = {int(key): value for key, value in row.facts['outstanding_income'].items()}
    for share in execution.shares.all():
        retained[share.account_id] -= share.pending_deduction
    main = funding_after_refund(principal_units=paid['PREPAID'], gift_units=paid['GIFT'],
        compensation_units=paid['COMPENSATION'], returned_prepaid_units=returned['PREPAID'],
        returned_gift_units=returned['GIFT'], returned_compensation_units=returned['COMPENSATION'],
        provider_income_units=sum(retained.values()) + sum(deductions.values()), pending_deduction_units=0,
        provider_external_units=external, inviter_rate=order.inviter_commission_rate_snapshot
            if order.inviter_id or order.inviter_account_id else 0, allocated_units=run.allocated_before)
    from .addon_funding_history import combined_at
    from .refunded_addon_funding import combine_refunded_history
    addons = list(OrderAddonPayment.objects.filter(intent__order=order, buyer_transaction__created_at__lt=run.created_at))
    history = {'addon_history': []}
    if addons:
        from .addon_payment import verify_receipt
        from .addon_refund_share_allocation import verify as verify_shares
        from .addon_funding_release import verify_run as verify_addon_run
        from .models import OrdinaryPostRefundTransferRevision
        from .ordinary_post_refund_transfer import verify_locked as verify_revision
        from .ordinary_transfer_addon_history import verify_handovers
        for addon in addons:
            verify_receipt(addon)
            verify_handovers(order, addon, before=run.created_at, witness_receipt_id=witness_receipt_id)
            payment_run = OrderAddonFundingReleaseRun.objects.filter(payment=addon).first()
            if (OrderPointsFundingAllocation.objects.filter(service_order=order,
                    created_at__lte=addon.buyer_transaction.created_at).exists() and not payment_run):
                raise ValidationError('多轮前加购付款资金版本缺失')
            if payment_run:
                if payment_run.created_at >= run.created_at:
                    raise ValidationError('多轮前加购付款时序异常')
                verify_addon_run(payment_run, _skip_usage=True, witness_receipt_id=witness_receipt_id)
            for addon_refund in addon.refund_records.filter(created_at__lt=run.created_at):
                verify_shares(addon_refund)
                addon_run = OrderAddonFundingReleaseRun.objects.filter(refund=addon_refund).first()
                revision = OrdinaryPostRefundTransferRevision.objects.filter(order=order,
                    source_kind='ADDON_REFUND', source_id=addon_refund.pk).first()
                if revision:
                    if revision.created_at >= run.created_at:
                        raise ValidationError('多轮前加购退款版本时序异常')
                    verify_revision(revision)
                    if addon_run and not (zero_pretransfer and revision.snapshot.get('pre_main_transfer_v1')):
                        raise ValidationError('多轮前加购退款存在重复资金版本')
                    if not addon_run:
                        addon_run = OrderAddonFundingReleaseRun.objects.filter(ordinary_transfer_revision=revision).first()
                if OrderPointsFundingAllocation.objects.filter(service_order=order,
                        created_at__lte=addon_refund.created_at).exists() and not addon_run:
                    raise ValidationError('多轮前加购退款资金版本缺失')
                if addon_run:
                    if addon_run.created_at >= run.created_at:
                        raise ValidationError('多轮前加购退款时序异常')
                    verify_addon_run(addon_run, _skip_usage=True, witness_receipt_id=witness_receipt_id)
        history = combined_at(order, main, run.created_at, segment_shares=[
            (line.segment_id, line.account_id, line.basis, line.fee, line.income) for line in lines])
        result = combine_refunded_history(main, history, main_retained_by_account={
            account: value + deductions.get(account, 0) for account, value in retained.items()})
    else:
        result = main
    from .ordinary_transfer_addon_rounding import facts as rounding_facts
    rounding = sum(item.rounding_to_store_units for item in receipts) + sum(item[1] for item in rounding_facts(order, before=run.created_at))
    if rounding > result.store_income_units:
        extra = rounding - result.store_income_units
        required = result.required_units + extra
        result = replace(result, store_income_units=result.store_income_units + extra, required_units=required,
            release_units=max(run.allocated_before - required, 0), remaining_units=max(required - run.allocated_before, 0))
    facts = {'ordinary_transfer_followup_version': 1, 'round_id': row.pk, 'execution_id': execution.pk,
             'refund_id': refund.pk, 'funding': asdict(result), 'addon_history': history.get('addon_history', [])}
    parts = plan_release(allocations=[[item[0], item[1], item[2]] for item in run.allocation_snapshot], amount=result.release_units)
    if (run.ordinary_refund_snapshot != facts or run.ordinary_refund_hash != _digest(run, facts)
            or run.required_amount != result.required_units or run.released_amount != result.release_units
            or [(item.allocation_id, item.amount) for item in run.releases.order_by('-allocation_id')]
                != [(item.allocation_id, item.amount) for item in parts]):
        raise ValidationError('多轮退款实际资金版本与释放异常')
    return run
