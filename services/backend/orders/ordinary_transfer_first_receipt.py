"""Receipt-local proof for one ordinary transfer and first main refund."""
from dataclasses import asdict, replace

from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as RestValidationError


def verify(run, witness_receipt_id):
    from console.deposit_clearance import _verified_refund_execution
    from engagement.funding_wallet import validate_order_funding_sources
    from engagement.models import OrderFundingAllocation, OrderPointAllocation
    from users.models import PassReceiptUsageState
    from users.receipt_usage_ledger import _locked_receipt
    from users.receipt_usage_sources import _require_current_unlocked_reads, order_facts
    from wallet.models import Transaction
    from .models import OrderAddonPayment, OrderOriginalShareTransfer, OrderPointsFundingAllocation, OrderTransferNegotiationEvidence
    from .ordinary_followup_receipt import _allocation_source
    from .ordinary_refund_receipt_evidence import _verify_direct_deposit
    from .ordinary_refunded_addon_release import _digest
    from .negotiation_transfer_evidence import _digest as evidence_digest
    from .points_funding_allocation import plan_release
    from .points_negotiation_preview import funding_after_refund
    from .ordinary_transfer_event_scope import segment_lines_at
    from .settlement import SettlementError

    _locked_receipt(witness_receipt_id)
    _require_current_unlocked_reads()
    execution = run.negotiation
    refund, order = execution.refund, execution.refund.order
    evidence = OrderTransferNegotiationEvidence.objects.filter(execution=execution, order=order).first()
    from .ordinary_transfer_event_scope import transfers_at
    transfers = transfers_at(order, execution.created_at)
    def pretransfer_addon_run(addon_run):
        return bool(transfers and isinstance(addon_run.ordinary_prestart_snapshot, dict)
                    and addon_run.ordinary_prestart_snapshot.get('kind') == 'ORDINARY_PRESTART_ADDON_V1'
                    and addon_run.created_at < transfers[0].created_at)
    addons = list(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__lt=run.created_at).select_related('intent', 'buyer_transaction'))
    if (not evidence or evidence.snapshot_hash != evidence_digest(evidence.snapshot)
            or {item.get('id') for item in evidence.snapshot.get('transfers', [])} != {item.pk for item in transfers}
            or evidence.snapshot.get('provider_terms') != execution.proposal.provider_terms
            or OrderPointAllocation.objects.filter(order=order).exists()
            or order.refund_records.filter(created_at__lt=run.created_at).count() != 1
            or order.refund_records.filter(created_at=run.created_at).exists()
            or not execution.termination_processed_at or execution.termination_processed_at >= run.created_at
            or not _verified_refund_execution(refund)):
        raise ValidationError('首次普通转单退款历史范围或凭证异常')
    from .ordinary_retained_segment_shares import verify_execution_plan
    try:
        if evidence.snapshot.get('segment_pending_plan') != execution.proposal.ordinary_segment_pending_plan:
            raise ValueError('首轮协商计划与执行凭证不一致')
        verify_execution_plan(execution)
    except ValueError as exc:
        raise ValidationError('首轮协商逐段承担凭证异常') from exc
    if evidence.snapshot.get('ordinary_paid_settlement_snapshot') != execution.proposal.ordinary_paid_settlement_snapshot:
        raise ValidationError('首轮协商已付快照不一致')
    from .ordinary_first_paid_settlement import verify_receipt_snapshot
    try:
        verify_receipt_snapshot(order, execution)
    except RestValidationError as exc:
        raise ValidationError('首轮协商已付流水待核对') from exc
    try:
        segment_lines = segment_lines_at(order, execution.created_at)
    except SettlementError as exc:
        raise ValidationError('转单逐段收益事实异常') from exc
    if [asdict(line) for line in segment_lines] != evidence.snapshot.get('lines'):
        raise ValidationError('转单逐段收益与原协商不一致')
    sources = list(OrderFundingAllocation.objects.filter(order=order).select_related('lot'))
    try:
        validate_order_funding_sources(order, sources)
    except RestValidationError as exc:
        raise ValidationError('原付款资金来源异常') from exc
    kinds = ('PREPAID', 'GIFT', 'COMPENSATION')
    paid = {kind: sum(row.amount for row in sources if row.kind == kind) for kind in kinds}
    returned = evidence.snapshot.get('return_sources')
    from engagement.funding_plan import RefundablePart, plan_refund
    original_return = plan_refund([RefundablePart(row.pk, row.kind, row.amount, 0)
                                   for row in sorted(sources, key=lambda row: row.pk)], refund.amount)
    payments = list(Transaction.objects.filter(order=order, tx_type='PAY', status='SUCCESS').select_related('wallet'))
    if (sum(paid.values()) != order.amount or not isinstance(returned, dict) or set(returned) != set(kinds)
            or any(type(returned[kind]) is not int or returned[kind] < 0 or returned[kind] > paid[kind] for kind in kinds)
            or any(returned[kind] != sum(original_return[row.pk] for row in sources if row.kind == kind)
                   for kind in kinds)
            or any(returned[kind] > sum(row.refunded_amount for row in sources if row.kind == kind) for kind in kinds)
            or sum(returned.values()) != refund.amount or len(payments) != 1
            or payments[0].amount != -order.amount or payments[0].wallet.account_id != order.customer_account_id
            or payments[0].wallet.user_id != order.customer_id or payments[0].reversal_of_id or payments[0].reversals.exists()
            or payments[0].balance_after != payments[0].balance_before + payments[0].amount):
        raise ValidationError('转单退款原付款或退回来源异常')
    shares = list(execution.shares.select_related('assignment', 'wallet_transaction__wallet',
        'deposit_deficit__profile', 'deposit_deficit__source_store_transaction__wallet__user'))
    terms = {row.get('account_id'): row for row in execution.proposal.provider_terms}
    if not shares or set(terms) != {row.account_id for row in shares}:
        raise ValidationError('转单退款确认人与承担人不一致')
    for share in shares:
        tx = share.wallet_transaction
        term = terms[share.account_id]
        if (share.assignment.order_id != order.pk or share.assignment.provider_account_id != share.account_id
                or share.burden != term.get('burden_units') or share.original_income != term.get('original_income_units')
                or share.retained_income != term.get('retained_units')
                or share.burden != share.pending_deduction + share.wallet_deduction + share.deposit_deduction
                or share.original_income != share.pending_deduction + share.retained_income
                or bool(tx) != bool(share.wallet_deduction) or bool(share.deposit_deficit_id) != bool(share.deposit_deduction)):
            raise ValidationError('转单退款本人承担异常')
        if tx and (tx.order_id != order.pk or tx.wallet.account_id != share.account_id
                or tx.amount != -share.wallet_deduction or tx.tx_type != 'REFUND' or tx.status != 'SUCCESS'
                or tx.balance_after != tx.balance_before + tx.amount or tx.reversal_of_id or tx.reversals.exists()):
            raise ValidationError('转单退款本人钱包流水异常')
        if share.deposit_deduction:
            _verify_direct_deposit(share.deposit_deficit, refund=refund, account_id=share.account_id,
                                   user_id=share.assignment.provider_id, amount=share.deposit_deduction, addon=False)
    if sum(row.burden for row in shares) != refund.amount + execution.rounding_to_store_units:
        raise ValidationError('转单退款承担合计异常')
    allocations = list(OrderPointsFundingAllocation.objects.filter(service_order=order,
        created_at__lt=run.created_at).select_related('receipt').order_by('pk'))
    if [row.pk for row in allocations] != [row[0] for row in run.allocation_snapshot]:
        raise ValidationError('首次转单退款的到账分配异常')
    state = PassReceiptUsageState.objects.filter(receipt_id=witness_receipt_id).first()
    if not state:
        raise ValidationError('到账用途账本缺失')
    for allocation, item in zip(allocations, run.allocation_snapshot):
        _allocation_source(allocation, order, run.created_at)
        from .models import OrderAddonFundingRelease
        prior = list(OrderAddonFundingRelease.objects.filter(allocation=allocation,
            run__created_at__lt=run.created_at).select_related('run__refund', 'allocation__receipt').order_by('pk'))
        if (item != [allocation.pk, allocation.amount, sum(row.amount for row in prior), []]
                or any(not ((isinstance(row.run.ordinary_prestart_snapshot, dict)
                    and row.run.ordinary_prestart_snapshot.get('kind') in
                        ('ORDINARY_TRANSFER_PRE_MAIN_ADDON_V1', 'ORDINARY_TRANSFER_PRE_MAIN_PAYMENT_V1',
                         'ORDINARY_TRANSFER_PRE_MAIN_HANDOVER_V1'))
                    or pretransfer_addon_run(row.run)
                    or (row.run.ordinary_transfer_revision_id and
                        row.run.ordinary_transfer_revision.snapshot.get('pre_main_transfer_v1') is True))
                    for row in prior)):
            raise ValidationError('首次转单退款之前的释放版本尚未接通')
        if allocation.receipt_id == witness_receipt_id:
            kind, key, request, amount, digest = order_facts(allocation)
            if not state.events.filter(kind=kind, allocation_key=key, request_key=request,
                                       amount=amount, source_digest=digest).exists():
                raise ValidationError('到账用途缺少原分配')
            from users.receipt_usage_sources import _addon_release_facts
            for release in prior:
                kind, key, request, amount, digest = _addon_release_facts(release, witness_receipt_id=witness_receipt_id)
                if not state.events.filter(kind=kind, allocation_key=key, request_key=request,
                                           amount=amount, source_digest=digest).exists():
                    raise ValidationError('到账用途缺少此前加购退款释放')
    if run.allocated_before != sum(item[1] - item[2] for item in run.allocation_snapshot):
        raise ValidationError('首次转单退款的到账分配总额异常')
    result = funding_after_refund(principal_units=paid['PREPAID'], gift_units=paid['GIFT'],
        compensation_units=paid['COMPENSATION'], returned_prepaid_units=returned['PREPAID'],
        returned_gift_units=returned['GIFT'], returned_compensation_units=returned['COMPENSATION'],
        provider_income_units=sum(line.income for line in segment_lines),
        pending_deduction_units=sum(row.pending_deduction for row in shares),
        provider_external_units=sum(row.wallet_deduction + row.deposit_deduction for row in shares),
        inviter_rate=order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0,
        allocated_units=run.allocated_before)
    history = None
    if addons:
        from collections import defaultdict
        from .addon_funding_history import combined_at
        from .ordinary_transfer_pre_main_addon import verify_handover_at
        from .refunded_addon_funding import combine_refunded_history
        from .models import OrderAddonFundingReleaseRun
        from .addon_funding_release import verify_run as verify_addon_run
        verified_unallocated_prior = False
        for addon in addons:
            payment_run = OrderAddonFundingReleaseRun.objects.filter(payment=addon).first()
            funded_at_payment = OrderPointsFundingAllocation.objects.filter(service_order=order,
                created_at__lte=addon.buyer_transaction.created_at).exists()
            if funded_at_payment and not payment_run:
                raise ValidationError('转单加购付款资金版本缺失')
            if payment_run:
                if (payment_run.created_at >= run.created_at or not isinstance(payment_run.ordinary_prestart_snapshot, dict)
                        or (payment_run.ordinary_prestart_snapshot.get('kind') != 'ORDINARY_TRANSFER_PRE_MAIN_PAYMENT_V1'
                            and not pretransfer_addon_run(payment_run))):
                    raise ValidationError('转单加购付款资金版本异常')
                try:
                    verify_addon_run(payment_run, _skip_usage=True, witness_receipt_id=witness_receipt_id)
                except RestValidationError as exc:
                    raise ValidationError('转单加购付款资金版本异常') from exc
            for addon_refund in addon.refund_records.filter(created_at__lt=run.created_at):
                addon_run = OrderAddonFundingReleaseRun.objects.filter(refund=addon_refund).first()
                if not addon_run:
                    if (addon_refund.created_at >= transfers[0].created_at
                            or OrderPointsFundingAllocation.objects.filter(service_order=order,
                                created_at__lte=addon_refund.created_at).exists()):
                        raise ValidationError('首次转单退款前加购退款资金版本缺失')
                    if not verified_unallocated_prior:
                        from .ordinary_multi_prior_addon_facts import verify as verify_prior
                        verify_prior(order, execution.created_at)
                        verified_unallocated_prior = True
                    continue
                if (addon_run.created_at >= run.created_at
                        or not isinstance(addon_run.ordinary_prestart_snapshot, dict)
                        or (addon_run.ordinary_prestart_snapshot.get('kind') != 'ORDINARY_TRANSFER_PRE_MAIN_ADDON_V1'
                            and not pretransfer_addon_run(addon_run))):
                    raise ValidationError('首次转单退款前加购退款资金版本缺失')
                try:
                    verify_addon_run(addon_run, _skip_usage=True, witness_receipt_id=witness_receipt_id)
                except RestValidationError as exc:
                    raise ValidationError('首次转单退款前加购退款资金版本异常') from exc
            handovers = list(addon.handovers.filter(status='ACCEPTED', accepted_at__lt=run.created_at).order_by('version'))
            if addon.handovers.filter(status='PENDING', created_at__lt=run.created_at).exists():
                raise ValidationError('首次转单退款的加购接替历史尚未接通')
            for handover in handovers:
                verify_handover_at(addon, handover, run.created_at)
                handover_run = OrderAddonFundingReleaseRun.objects.filter(handover=handover).first()
                if (OrderPointsFundingAllocation.objects.filter(service_order=order,
                        created_at__lte=handover.accepted_at).exists() and not handover_run):
                    raise ValidationError('首次转单退款前加购接替资金版本缺失')
                if handover_run:
                    if (handover_run.created_at >= run.created_at or not isinstance(handover_run.ordinary_prestart_snapshot, dict)
                            or (handover_run.ordinary_prestart_snapshot.get('kind') != 'ORDINARY_TRANSFER_PRE_MAIN_HANDOVER_V1'
                                and not pretransfer_addon_run(handover_run))):
                        raise ValidationError('加购接替资金版本异常')
                    verify_addon_run(handover_run, _skip_usage=True, witness_receipt_id=witness_receipt_id)
        retained = defaultdict(int)
        for line in segment_lines:
            retained[line.account_id] += line.income
        for share in shares:
            retained[share.account_id] -= share.pending_deduction
        if any(value < 0 for value in retained.values()):
            raise ValidationError('首次转单退款保留收益异常')
        history = combined_at(order, result, run.created_at, segment_shares=[
            (line.segment_id, line.account_id, line.basis, line.fee, line.income)
            for line in segment_lines])
        result = combine_refunded_history(result, history, main_retained_by_account=retained)
    addon_rounding = sum(row.rounding_surplus for addon in addons
                         for row in addon.refund_records.filter(created_at__lt=run.created_at))
    extra = max(execution.rounding_to_store_units + addon_rounding - result.store_income_units, 0)
    if extra:
        required = result.required_units + extra
        result = replace(result, store_income_units=result.store_income_units + extra, required_units=required,
                         release_units=max(run.allocated_before - required, 0),
                         remaining_units=max(required - run.allocated_before, 0))
    facts = {'ordinary_transfer_refund_version': 1, 'execution_id': execution.pk,
             'refund_id': refund.pk, 'funding': asdict(result)}
    if history is not None:
        facts['addon_history'] = list(history['addon_history'])
    normalized = [[item[0], item[1], item[2]] for item in run.allocation_snapshot]
    parts = plan_release(allocations=normalized, amount=result.release_units)
    if (run.ordinary_refund_snapshot != facts or run.ordinary_refund_hash != _digest(run, facts)
            or run.required_amount != result.required_units or run.released_amount != result.release_units
            or [(row.allocation_id, row.amount) for row in run.releases.order_by('-allocation_id')]
               != [(row.allocation_id, row.amount) for row in parts]):
        raise ValidationError('首次转单退款的释放资金版本异常')
    return run
