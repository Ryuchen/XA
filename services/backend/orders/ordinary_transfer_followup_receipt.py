"""Read-only receipt-local proof of the second ordinary transfer refund."""
from dataclasses import asdict, replace

from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as RestValidationError


def verify(run, witness_receipt_id):
    from .models import OrdinaryTransferFollowupRound
    round_record = OrdinaryTransferFollowupRound.objects.filter(execution_id=run.negotiation_id).first()
    if round_record and round_record.sequence > 1:
        from .ordinary_transfer_multiround_receipt import verify as verify_multiple
        return verify_multiple(run, witness_receipt_id)
    from console.deposit_clearance import _verified_refund_execution
    from engagement.funding_plan import RefundablePart, plan_refund
    from engagement.funding_wallet import validate_order_funding_sources
    from engagement.models import OrderFundingAllocation
    from users.models import PassReceiptUsageState
    from users.receipt_usage_ledger import _locked_receipt
    from users.receipt_usage_sources import _addon_release_facts, _require_current_unlocked_reads, order_facts
    from wallet.models import Transaction
    from .addon_funding_history import combined_at
    from .models import (NegotiationExecution, OrderAddonFundingRelease, OrderAddonPayment,
                         OrderOriginalShareTransfer, OrderPointsFundingAllocation, OrdinaryTransferFollowupRound)
    from .ordinary_followup_receipt import _allocation_source
    from .ordinary_refund_receipt_evidence import _verify_direct_deposit
    from .ordinary_refunded_addon_release import _digest
    from .ordinary_transfer_followup import _hash
    from .points_funding_allocation import plan_release
    from .points_negotiation_preview import funding_after_refund
    from .refunded_addon_funding import combine_refunded_history
    from .ordinary_transfer_event_scope import segment_lines_at
    from .settlement import SettlementError

    _locked_receipt(witness_receipt_id)
    _require_current_unlocked_reads()
    execution = run.negotiation
    refund, order = execution.refund, execution.refund.order
    row = OrdinaryTransferFollowupRound.objects.filter(execution=execution, order=order).select_related('previous__refund').first()
    zero_pretransfer = bool(row and row.previous.zero_order_id == order.pk and not row.previous.refund_id)
    from .models import OrdinaryPostRefundTransferRevision
    positive_pretransfer = bool(row and row.previous.refund_id and OrdinaryPostRefundTransferRevision.objects.filter(
        order=order, snapshot__positive_pretransfer_execution_id=row.previous_id).exists())
    if (not row or row.sequence != 1 or row.facts_hash != _hash(row.proposal_id, row.facts)
            or row.facts.get('order_id') != order.pk or row.facts.get('previous_execution_id') != row.previous_id
            or row.facts.get('prior_execution_ids') != [row.previous_id]
            or (not zero_pretransfer and (not row.previous.refund_id or order.refund_records.filter(created_at__lte=refund.created_at).count() != 2
                                          or row.previous.refund.created_at >= refund.created_at))
            or (zero_pretransfer and order.refund_records.filter(created_at__lte=refund.created_at).count() != 1)
            or not execution.termination_processed_at or execution.termination_processed_at >= run.created_at
            or (not zero_pretransfer and not _verified_refund_execution(row.previous.refund))
            or not _verified_refund_execution(refund)):
        raise ValidationError('第二次转单退款协商来源异常')
    from .ordinary_transfer_event_scope import transfers_at
    if zero_pretransfer or positive_pretransfer:
        from .ordinary_zero_pretransfer import verify as verify_pretransfer_zero
        first_transfer = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).order_by('created_at', 'pk').first()
        if positive_pretransfer:
            from .ordinary_positive_pretransfer import verify as verify_positive_pretransfer
            verified_first = verify_positive_pretransfer(order, before=first_transfer.created_at) if first_transfer else None
        else:
            verified_first = verify_pretransfer_zero(order, before=first_transfer.created_at) if first_transfer else None
        if not verified_first or verified_first.pk != row.previous_id:
            raise ValidationError('首次零退款转单前凭证异常')
    else:
        transfers_at(order, row.previous.created_at)
    try:
        first_lines = [] if zero_pretransfer or positive_pretransfer else segment_lines_at(order, row.previous.created_at)
        lines = segment_lines_at(order, row.created_at)
    except SettlementError as exc:
        raise ValidationError('追加退款逐段收益异常') from exc
    if [asdict(line) for line in lines] != row.facts.get('segment_lines'):
        raise ValidationError('追加退款逐段收益与确认版本不一致')
    from collections import defaultdict
    from .addon_main_deduction import deductions_at
    from .addon_main_segment_deduction import verified_deductions
    from .models import OrderTransferNegotiationEvidence
    from .negotiation_transfer_evidence import _digest as transfer_digest
    first_evidence = OrderTransferNegotiationEvidence.objects.filter(execution=row.previous, order=order).first()
    if not (zero_pretransfer or positive_pretransfer) and (not first_evidence or first_evidence.snapshot_hash != transfer_digest(first_evidence.snapshot)
            or first_evidence.snapshot.get('provider_terms') != row.previous.proposal.provider_terms
            or first_evidence.snapshot.get('lines') != [asdict(line) for line in first_lines]):
        raise ValidationError('首次转单协商分段来源异常')
    def selected_income(segments_by_account, boundary, source_lines):
        deductions = verified_deductions(order, before=boundary)
        by_id = {line.segment_id: line for line in source_lines}
        output = {}
        for account, segment_ids in segments_by_account.items():
            if not segment_ids or len(segment_ids) != len(set(segment_ids)):
                raise ValidationError('退款受影响履约段异常')
            amount = 0
            for segment_id in segment_ids:
                line = by_id.get(segment_id)
                if not line or line.account_id != int(account):
                    raise ValidationError('退款履约段归属异常')
                amount += line.income - deductions.get(segment_id, 0)
            output[int(account)] = amount
        return output
    first_segments = defaultdict(list)
    selected = set(first_evidence.snapshot.get('selected_segment_ids', [])) if not (zero_pretransfer or positive_pretransfer) else set()
    for line in first_lines:
        if line.segment_id in selected:
            first_segments[line.account_id].append(line.segment_id)
    if (selected != {segment for segments in first_segments.values() for segment in segments}
            or (not (zero_pretransfer or positive_pretransfer) and sorted(selected) != row.previous.proposal.affected_segment_ids)
            or sorted(segment for segments in row.facts['segments'].values() for segment in segments)
                != execution.proposal.affected_segment_ids):
        raise ValidationError('退款确认的履约段范围异常')
    if zero_pretransfer or positive_pretransfer:
        from .models import OrderParticipationSegment
        closed = set(OrderParticipationSegment.objects.filter(attribution__order=order,
            provider_account_id__in=row.previous.proposal.affected_accounts, released_at=row.previous.accepted_at,
            started_at__isnull=False).values_list('pk', flat=True))
        if not closed or closed.intersection(execution.proposal.affected_segment_ids):
            raise ValidationError('追加退款不能重开首次协商已终止的履约段')
    first_income = selected_income(first_segments, row.previous.created_at, first_lines)
    second_income = selected_income(row.facts['segments'], row.created_at, lines)
    if ((not (zero_pretransfer or positive_pretransfer) and any(first_income.get(term['account_id']) != term['original_income_units']
            for term in row.previous.proposal.provider_terms)
            )
            or any(second_income.get(term['account_id']) != term['original_income_units']
                   for term in execution.proposal.provider_terms)):
        raise ValidationError('退款协商收益与实际履约段不一致')
    try:
        actual_addon_deductions = deductions_at(order, before=row.created_at)
    except RestValidationError as exc:
        raise ValidationError('追加退款前加购主单扣减异常') from exc
    if {str(key): value for key, value in actual_addon_deductions.items()} != row.facts.get('addon_main_deductions'):
        raise ValidationError('追加退款前加购主单扣减与确认版本不一致')
    outstanding = defaultdict(int)
    for line in lines:
        outstanding[line.account_id] += line.income
    for account, amount in actual_addon_deductions.items():
        outstanding[account] -= amount
    for prior_share in row.previous.shares.all():
        outstanding[prior_share.account_id] -= prior_share.pending_deduction
    if ({str(account): value for account, value in sorted(outstanding.items())} != row.facts.get('outstanding_income')
            or any(value < 0 for value in outstanding.values())):
        raise ValidationError('追加退款前各人剩余收益异常')
    sources = list(OrderFundingAllocation.objects.filter(order=order).select_related('lot').order_by('pk'))
    try:
        validate_order_funding_sources(order, sources)
    except RestValidationError as exc:
        raise ValidationError('追加退款原付款来源异常') from exc
    kinds = ('PREPAID', 'GIFT', 'COMPENSATION')
    paid = {kind: sum(item.amount for item in sources if item.kind == kind) for kind in kinds}
    before = {item['id']: item for item in row.facts['funding_sources']}
    if set(before) != {item.pk for item in sources} or sum(paid.values()) != order.amount:
        raise ValidationError('追加退款原付款快照异常')
    first_refund_amount = 0 if zero_pretransfer else row.previous.refund.amount
    first_delta = plan_refund([RefundablePart(item.pk, item.kind, item.amount, 0) for item in sources],
                              first_refund_amount)
    payments = list(Transaction.objects.filter(order=order, tx_type='PAY', status='SUCCESS').select_related('wallet'))
    if (any(before[item.pk]['lot_id'] != item.lot_id or before[item.pk]['kind'] != item.kind
            or before[item.pk]['amount'] != item.amount or before[item.pk]['refunded_amount'] != first_delta[item.pk]
            for item in sources) or len(payments) != 1 or payments[0].amount != -order.amount
            or payments[0].wallet.account_id != order.customer_account_id or payments[0].wallet.user_id != order.customer_id
            or payments[0].balance_after != payments[0].balance_before + payments[0].amount
            or payments[0].reversal_of_id or payments[0].reversals.exists()):
        raise ValidationError('追加退款原付款或首次退款来源异常')
    delta = plan_refund([RefundablePart(item.pk, item.kind, item.amount, before[item.pk]['refunded_amount'])
                         for item in sources], refund.amount)
    returned = {kind: sum(before[item.pk]['refunded_amount'] + delta[item.pk]
                          for item in sources if item.kind == kind) for kind in kinds}
    current_returned = plan_refund([RefundablePart(item.pk, item.kind, item.amount) for item in sources],
        sum(order.refund_records.values_list('amount', flat=True)))
    if (sum(returned.values()) != first_refund_amount + refund.amount
            or any(item.refunded_amount != current_returned[item.pk] for item in sources)):
        raise ValidationError('追加退款原路资金不一致')
    receipts = [row.previous, execution]
    from .ordinary_retained_segment_shares import verify_execution_plan
    for receipt in receipts:
        if receipt.proposal.ordinary_segment_pending_plan is not None:
            try:
                verify_execution_plan(receipt)
            except ValueError as exc:
                raise ValidationError('协商逐段承担凭证异常') from exc
    external = 0
    for receipt in receipts:
        shares = list(receipt.shares.select_related('assignment', 'wallet_transaction__wallet',
            'deposit_deficit__profile', 'deposit_deficit__source_store_transaction__wallet__user'))
        terms = {item['account_id']: item for item in receipt.proposal.provider_terms}
        if not shares or set(terms) != {item.account_id for item in shares}:
            raise ValidationError('退款确认人与承担人不一致')
        for share in shares:
            term, tx = terms[share.account_id], share.wallet_transaction
            if (share.assignment.order_id != order.pk or share.assignment.provider_account_id != share.account_id
                    or share.burden != term['burden_units'] or share.original_income != term['original_income_units']
                    or share.retained_income != term['retained_units']
                    or share.burden != share.pending_deduction + share.wallet_deduction + share.deposit_deduction
                    or share.original_income != share.pending_deduction + share.retained_income
                    or bool(tx) != bool(share.wallet_deduction) or bool(share.deposit_deficit_id) != bool(share.deposit_deduction)):
                raise ValidationError('退款本人承担异常')
            if tx and (tx.order_id != order.pk or tx.wallet.account_id != share.account_id
                    or tx.amount != -share.wallet_deduction or tx.tx_type != 'REFUND' or tx.status != 'SUCCESS'
                    or tx.balance_after != tx.balance_before + tx.amount or tx.reversal_of_id or tx.reversals.exists()):
                raise ValidationError('退款本人钱包流水异常')
            if share.deposit_deduction:
                _verify_direct_deposit(share.deposit_deficit, refund=receipt.refund, account_id=share.account_id,
                    user_id=share.assignment.provider_id, amount=share.deposit_deduction, addon=False)
            external += share.wallet_deduction + share.deposit_deduction
        if sum(item.burden for item in shares) != (receipt.refund.amount if receipt.refund_id else 0) + receipt.rounding_to_store_units:
            raise ValidationError('退款承担合计异常')
    allocations = list(OrderPointsFundingAllocation.objects.filter(service_order=order,
        created_at__lt=run.created_at).select_related('receipt').order_by('pk'))
    if [item.pk for item in allocations] != [item[0] for item in run.allocation_snapshot]:
        raise ValidationError('追加退款到账分配异常')
    state = PassReceiptUsageState.objects.filter(receipt_id=witness_receipt_id).first()
    if not state:
        raise ValidationError('到账用途账本缺失')
    for allocation, entry in zip(allocations, run.allocation_snapshot):
        _allocation_source(allocation, order, run.created_at)
        prior = list(OrderAddonFundingRelease.objects.filter(allocation=allocation,
            run__created_at__lt=run.created_at).select_related('run', 'allocation__receipt').order_by('pk'))
        if entry != [allocation.pk, allocation.amount, sum(item.amount for item in prior), []]:
            raise ValidationError('追加退款此前释放金额异常')
        if allocation.receipt_id == witness_receipt_id:
            events = [order_facts(allocation)] + [_addon_release_facts(item, witness_receipt_id=witness_receipt_id)
                                                   for item in prior]
            for kind, key, request, amount, digest in events:
                if not state.events.filter(kind=kind, allocation_key=key, request_key=request,
                                           amount=amount, source_digest=digest).exists():
                    raise ValidationError('追加退款此前到账用途异常')
    if run.allocated_before != sum(item[1] - item[2] for item in run.allocation_snapshot):
        raise ValidationError('追加退款到账分配总额异常')
    addon_deductions = {int(key): value for key, value in row.facts['addon_main_deductions'].items()}
    retained = {int(key): value for key, value in row.facts['outstanding_income'].items()}
    for share in execution.shares.all():
        retained[share.account_id] -= share.pending_deduction
    if any(value < 0 for value in retained.values()):
        raise ValidationError('追加退款后保留收益异常')
    main = funding_after_refund(principal_units=paid['PREPAID'], gift_units=paid['GIFT'],
        compensation_units=paid['COMPENSATION'], returned_prepaid_units=returned['PREPAID'],
        returned_gift_units=returned['GIFT'], returned_compensation_units=returned['COMPENSATION'],
        provider_income_units=sum(retained.values()) + sum(addon_deductions.values()), pending_deduction_units=0,
        provider_external_units=external,
        inviter_rate=order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0,
        allocated_units=run.allocated_before)
    history = {'addon_history': []}
    addons = list(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__lt=run.created_at).select_related('intent', 'buyer_transaction'))
    if addons:
        from .models import OrderAddonFundingReleaseRun, OrdinaryPostRefundTransferRevision
        from .addon_funding_release import verify_run as verify_addon_run
        from .ordinary_post_refund_transfer import verify_locked as verify_revision
        from .ordinary_transfer_addon_history import verify_handovers
        for addon in addons:
            verify_handovers(order, addon, before=run.created_at, witness_receipt_id=witness_receipt_id)
            payment_run = OrderAddonFundingReleaseRun.objects.filter(payment=addon).first()
            if (OrderPointsFundingAllocation.objects.filter(service_order=order,
                    created_at__lte=addon.buyer_transaction.created_at).exists() and not payment_run):
                raise ValidationError('追加退款前加购付款资金版本缺失')
            if payment_run:
                if payment_run.created_at >= run.created_at:
                    raise ValidationError('加购付款与追加退款资金时序异常')
                verify_addon_run(payment_run, _skip_usage=True, witness_receipt_id=witness_receipt_id)
            for addon_refund in addon.refund_records.filter(created_at__lt=run.created_at):
                addon_run = OrderAddonFundingReleaseRun.objects.filter(refund=addon_refund).first()
                revision = OrdinaryPostRefundTransferRevision.objects.filter(order=order,
                    source_kind='ADDON_REFUND', source_id=addon_refund.pk).first()
                if revision:
                    verify_revision(revision)
                    if addon_run and not (zero_pretransfer and revision.snapshot.get('pre_main_transfer_v1')):
                        raise ValidationError('加购退款存在重复资金版本')
                    if not addon_run:
                        addon_run = OrderAddonFundingReleaseRun.objects.filter(ordinary_transfer_revision=revision).first()
                if (OrderPointsFundingAllocation.objects.filter(service_order=order,
                        created_at__lte=addon_refund.created_at).exists() and not addon_run):
                    raise ValidationError('追加退款前加购退款资金版本缺失')
                if addon_run:
                    if addon_run.created_at >= run.created_at:
                        raise ValidationError('加购退款与追加退款资金时序异常')
                    verify_addon_run(addon_run, _skip_usage=True, witness_receipt_id=witness_receipt_id)
        lines = row.facts['segment_lines']
        history = combined_at(order, main, run.created_at, segment_shares=[
            (line['segment_id'], line['account_id'], line['basis'], line['fee'], line['income']) for line in lines])
        limits = {account: value + addon_deductions.get(account, 0) for account, value in retained.items()}
        result = combine_refunded_history(main, history, main_retained_by_account=limits)
    else:
        result = main
    from .ordinary_transfer_addon_rounding import facts as rounding_facts
    addon_rounding = rounding_facts(order, before=run.created_at)
    rounding = sum(item.rounding_to_store_units for item in receipts) + sum(item[1] for item in addon_rounding)
    extra = max(rounding - result.store_income_units, 0)
    if extra:
        required = result.required_units + extra
        result = replace(result, store_income_units=result.store_income_units + extra, required_units=required,
            release_units=max(run.allocated_before - required, 0), remaining_units=max(required - run.allocated_before, 0))
    facts = {'ordinary_transfer_followup_version': 1, 'round_id': row.pk, 'execution_id': execution.pk,
             'refund_id': refund.pk, 'funding': asdict(result), 'addon_history': history.get('addon_history', [])}
    parts = plan_release(allocations=[[item[0], item[1], item[2]] for item in run.allocation_snapshot],
                         amount=result.release_units)
    if (run.ordinary_refund_snapshot != facts or run.ordinary_refund_hash != _digest(run, facts)
            or run.required_amount != result.required_units or run.released_amount != result.release_units
            or [(item.allocation_id, item.amount) for item in run.releases.order_by('-allocation_id')]
               != [(item.allocation_id, item.amount) for item in parts]):
        raise ValidationError('追加退款资金版本或释放异常')
    return run
