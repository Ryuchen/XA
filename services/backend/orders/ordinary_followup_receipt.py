"""Receipt-local proof of one ordinary, untransferred follow-up release.

The caller holds only the witness receipt lock. Do not acquire order, wallet,
other receipt, or platform row locks from this reader.
"""
from dataclasses import asdict

from django.core.exceptions import ValidationError


def _allocation_source(allocation, order, boundary):
    """Read immutable platform and tenant facts; never lock another receipt."""
    import json
    from uuid import UUID
    from stores.context import current_store
    from stores.models import PlatformPassRepaymentOperation, PlatformPointsFundingOperation
    from stores.platform_pass_repayment import RepaymentError, _claimed_receipt, _sha
    from users.models import PassRefundRepaymentAction
    from .models import OrderPointsFundingAction
    store = current_store()
    try:
        key = allocation.request_key
        if not key.startswith('points:') or len(key) != 39:
            raise ValueError('invalid request key')
        operation_id = UUID(hex=key[7:])
    except (TypeError, AttributeError, ValueError) as exc:
        raise ValidationError('订单补资请求编号异常') from exc
    operation = PlatformPointsFundingOperation.objects.using('default').select_related('claim').filter(
        pk=operation_id, claim__store__slug=store.slug if store else '').first()
    action = OrderPointsFundingAction.objects.filter(request_key=key).first()
    receipt = allocation.receipt
    expected = {'receipt_id': allocation.receipt_id, 'allocation_id': allocation.pk,
                'order_id': order.pk, 'amount': allocation.amount}
    if (not store or not operation or not operation.completed_at or operation.completed_at >= boundary
            or operation.rejected_at or operation.voided_at or not action
            or action.created_at >= boundary or operation.tenant_action_id != action.pk
            or action.allocation_id != allocation.pk or action.actor != str(operation.actor_id)
            or allocation.allocated_by != str(operation.actor_id) or action.result != expected
            or operation.claim.tenant_receipt_id != receipt.pk
            or operation.payload_digest != _sha(json.dumps(
                [str(operation.claim_id), order.pk, allocation.amount], separators=(',', ':')))
            or not receipt.verified_at or not receipt.verified_by or receipt.verified_at >= boundary
            or receipt.usage_version is None):
        raise ValidationError('订单补资到账或平台操作凭证异常')
    try:
        _claimed_receipt(operation.claim, store.slug, receipt)
    except RepaymentError as exc:
        raise ValidationError('订单补资到账登记异常') from exc
    verification = PlatformPassRepaymentOperation.objects.using('default').filter(
        claim=operation.claim, kind='VERIFY', completed_at__lt=boundary,
        tenant_action_id__isnull=False, rejected_at__isnull=True, voided_at__isnull=True)
    matched = False
    for item in verification:
        check = PassRefundRepaymentAction.objects.filter(pk=item.tenant_action_id).first()
        if (check and check.kind == 'VERIFY' and check.receipt_id == receipt.pk and not check.allocation_id
                and check.request_key == f'platform:{item.pk.hex}' and check.actor == str(item.actor_id)
                and item.payload_digest == _sha(json.dumps(
                    [str(item.claim_id), 'VERIFY', None, None], separators=(',', ':')))
                and check.result == {'receipt_id': receipt.pk, 'verified_by': receipt.verified_by,
                                     'verified_at': receipt.verified_at.isoformat()}):
            matched = True
            break
    if not matched:
        raise ValidationError('订单补资到账核实凭证异常')


def facts(row, *, witness_receipt_id=None):
    from console.deposit_clearance import _verified_refund_execution
    from engagement.funding_plan import RefundablePart, plan_refund
    from users.receipt_usage_ledger import _locked_receipt
    from users.receipt_usage_sources import _digest, _require_current_unlocked_reads, order_facts
    from .models import (NegotiationFollowupLink, OrderAddonFundingRelease,
                         OrderAddonFundingReleaseRun, OrderPointsFundingAllocation,
                         OrderPointsFundingRelease, OrderPointsTransferFundingRelease,
                         PointsPostRefundTransferRelease, OrderSegmentFinancialSnapshot)
    from .ordinary_refund_receipt_evidence import verified_funding, verified_shares, _verify_direct_deposit
    from .ordinary_refunded_addon_release import _digest as release_digest
    from .ordinary_refunded_addon_settlement import _digest as evidence_digest
    from .negotiation_execution import _allocation_hash
    from .negotiation_followup_accounting import verify_followup_termination
    from .points_funding_allocation import plan_release
    from .points_negotiation_preview import RefundedFunding

    run, allocation = row.run, row.allocation
    witness_id = witness_receipt_id or allocation.receipt_id
    _locked_receipt(witness_id)
    _require_current_unlocked_reads()
    link = NegotiationFollowupLink.objects.select_related(
        'order', 'previous__refund', 'execution__refund').filter(execution_id=run.negotiation_id).first()
    if link and link.execution.refund_id and not link.previous.refund_id:
        from .ordinary_zero_followup_evidence import verified_release
        return verified_release(row, witness_receipt_id=witness_id)
    if not link or not link.execution.refund_id or not link.previous.refund_id:
        raise ValidationError('追加退款到账来源缺少协商记录')
    order, first, second = link.order, link.previous, link.execution
    projection = link.projected_ledger
    if (allocation.service_order_id != order.pk or allocation.redemption_id is not None
            or row.amount <= 0 or not isinstance(projection, dict)
            or projection.get('ordinary_addon_followup_version') != 1
            or link.ledger_hash != _allocation_hash(projection)
            or order.is_gift_snapshot or order.source_order_id
            or first.refund.created_at >= second.refund.created_at
            or second.refund.created_at >= run.created_at or not second.termination_processed_at
            or second.termination_processed_at >= run.created_at):
        raise ValidationError('追加退款到账来源范围或顺序异常')
    verify_followup_termination(link)
    first_rows = verified_shares(first)
    checked = _verified_refund_execution(second.refund)
    shares = list(second.shares.select_related('assignment', 'wallet_transaction__wallet',
        'deposit_deficit__profile', 'deposit_deficit__source_store_transaction__wallet__user'))
    if not checked or checked.pk != second.pk or len(first_rows) != 1 or len(shares) != 1:
        raise ValidationError('两次退款确认或承担凭证异常')
    share = shares[0]
    estimate = projection.get('estimate')
    if (not isinstance(estimate, dict) or estimate.get('prior_paid') != 0
            or estimate.get('prior_settled_account_id') != first_rows[0].account_id
            or estimate.get('insufficient') != 0
            or share.assignment.order_id != order.pk or share.assignment.provider_account_id != share.account_id
            or share.account_id == first_rows[0].account_id
            or share.account_id != estimate.get('account_id')
            or share.original_income != share.pending_deduction + share.retained_income
            or share.burden != share.pending_deduction + share.wallet_deduction + share.deposit_deduction
            or share.burden != second.refund.amount or second.rounding_to_store_units
            or share.pending_deduction != estimate.get('pending_deduction')
            or share.retained_income != estimate.get('retained_main_income')
            or share.wallet_deduction != estimate.get('wallet_deduction')
            or share.deposit_deduction != estimate.get('deposit_deduction')
            or second.refund.recovered_amount != share.wallet_deduction + share.deposit_deduction):
        raise ValidationError('追加退款逐人承担异常')
    expected_terms = [{'account_id': share.account_id, 'original_income_units': share.original_income,
        'burden_units': share.burden, 'retained_units': share.retained_income,
        'additional_units': share.wallet_deduction + share.deposit_deduction}]
    if second.proposal.provider_terms != expected_terms or second.proposal.affected_accounts != [share.account_id]:
        raise ValidationError('追加退款与确认的承担方案不一致')
    tx = share.wallet_transaction
    if bool(tx) != bool(share.wallet_deduction) or (tx and (
            tx.order_id != order.pk or tx.wallet.account_id != share.account_id
            or tx.wallet.user_id != share.assignment.provider_id or tx.tx_type != 'REFUND'
            or tx.status != 'SUCCESS' or tx.amount != -share.wallet_deduction
            or tx.balance_after != tx.balance_before + tx.amount or tx.reversal_of_id or tx.reversals.exists()
            or tx.operator_account_id != second.operator_id or tx.operator_id != second.refund.operator_id)):
        raise ValidationError('追加退款陪玩钱包流水异常')
    if bool(share.deposit_deficit_id) != bool(share.deposit_deduction):
        raise ValidationError('追加退款押金来源异常')
    if share.deposit_deficit_id:
        _verify_direct_deposit(share.deposit_deficit, refund=second.refund, account_id=share.account_id,
                               user_id=share.assignment.provider_id, amount=share.deposit_deduction, addon=False)

    prior_run = OrderAddonFundingReleaseRun.objects.select_related('negotiation__refund__order').filter(
        negotiation=first).first()
    if prior_run and prior_run.created_at >= second.refund.created_at:
        raise ValidationError('首次退款资金版本缺失')
    if prior_run:
        first_boundary = prior_run.created_at
    else:
        # The refund writer creates no run when there were no allocations.
        # Its order lock excludes a concurrent allocation until commit.
        from datetime import timedelta
        from types import SimpleNamespace
        if not first.termination_processed_at:
            raise ValidationError('首次退款完成时间缺失')
        first_boundary = first.termination_processed_at + timedelta(microseconds=1)
        if first_boundary >= second.refund.created_at:
            raise ValidationError('首次退款与追加退款时序异常')
    from .models import OrderAddonPayment
    payments = list(OrderAddonPayment.objects.filter(intent__order=order)
                    .select_related('intent', 'buyer_transaction').order_by('pk'))
    if any(payment.buyer_transaction.created_at >= second.refund.created_at
           or payment.refund_records.filter(created_at__gte=second.refund.created_at).exists()
           for payment in payments):
        raise ValidationError('后续加购付款、退款或换人来源尚未接通')
    from .ordinary_refunded_team_handover import verify as verify_team_handover
    handover_run_ids = set()
    for payment in payments:
        history = list(payment.handovers.select_related('payment__intent__order',
            'source_segment__attribution', 'target_segment__attribution'))
        handovers = [row for row in history if row.status == 'ACCEPTED']
        if any(row.status == 'PENDING' for row in history):
            raise ValidationError('多次加购接替来源尚未接通')
        if len(history) != len(handovers):
            from .ordinary_pending_handover_refund import verify_retry
            if not verify_retry(payment):
                raise ValidationError('退款导致接替失效的历史异常')
        for handover in handovers:
            if not handover.accepted_at or handover.accepted_at >= second.refund.created_at:
                raise ValidationError('加购接替与退款时序异常')
            pre_main_handover = handover.accepted_at < first.refund.created_at
            if pre_main_handover:
                from .ordinary_team_pre_main_handover import verify as verify_pre_main_handover
                verify_pre_main_handover(handover)
            else:
                verify_team_handover(handover)
            handover_run = OrderAddonFundingReleaseRun.objects.filter(handover=handover).first()
            funded = OrderPointsFundingAllocation.objects.filter(service_order=order,
                created_at__lte=handover.accepted_at).exists()
            if funded and not handover_run:
                raise ValidationError('加购接替资金版本缺失')
            if handover_run:
                if handover_run.created_at >= second.refund.created_at:
                    raise ValidationError('加购接替资金版本异常')
                if pre_main_handover:
                    if not handover_run.ordinary_prestart_snapshot or not handover_run.ordinary_prestart_hash:
                        raise ValidationError('主单退款前加购接替资金版本异常')
                    from .addon_funding_release import verify_run
                    verify_run(handover_run, _skip_usage=True)
                else:
                    from .ordinary_refund_addon_payment_release import HANDOVER_KIND, verify as verify_handover_release
                    if (not isinstance(handover_run.ordinary_refund_snapshot, dict)
                            or handover_run.ordinary_refund_snapshot.get('kind') != HANDOVER_KIND):
                        raise ValidationError('加购接替资金版本异常')
                    verify_handover_release(handover_run)
                handover_run_ids.add(handover_run.pk)
    if prior_run:
        first_facts = verified_funding(prior_run)
    else:
        first_facts = verified_funding(SimpleNamespace(
            negotiation_id=first.pk, negotiation=first, created_at=first_boundary,
            allocated_before=0), verify_snapshot=False)
    payment_runs = list(OrderAddonFundingReleaseRun.objects.filter(payment__in=payments,
        created_at__gt=first_boundary, created_at__lt=second.refund.created_at).select_related('payment__intent'))
    from .ordinary_refund_addon_payment_release import KIND as payment_kind, verify as verify_payment
    for payment_run in payment_runs:
        if (not isinstance(payment_run.ordinary_refund_snapshot, dict)
                or payment_run.ordinary_refund_snapshot.get('kind') != payment_kind):
            raise ValidationError('退款后加购付款资金版本未接通')
        verify_payment(payment_run)
    payment_run_ids = {item.pk for item in payment_runs}
    refund_runs = list(OrderAddonFundingReleaseRun.objects.filter(refund__payment__in=payments,
        refund__created_at__gt=first.refund.created_at, created_at__lt=second.refund.created_at)
        .select_related('refund__payment__intent'))
    if sum(payment.refund_records.filter(created_at__gt=first.refund.created_at).count()
           for payment in payments) != len(refund_runs):
        raise ValidationError('加购退款资金版本缺失')
    from .ordinary_after_main_addon_receipt import verified_release as verify_addon_refund
    for refund_run in refund_runs:
        verify_addon_refund(refund_run, witness_id)
    refund_run_ids = {item.pk for item in refund_runs}
    settlement = order.points_settlement
    evidence = settlement.ordinary_refund_addon_evidence
    data = evidence.facts
    if (evidence.execution_id != first.pk or evidence.facts_hash != evidence_digest(data)
            or projection.get('prior_facts_hash') != evidence.facts_hash
            or settlement.persons.filter(settled_at__lte=second.refund.created_at).exists()
            or order.providers.filter(settled_at__lte=second.refund.created_at).exists()
            or not isinstance(data, dict)):
        raise ValidationError('追加退款前的结算来源异常')
    prior_allocations = list(OrderPointsFundingAllocation.objects.filter(service_order=order,
        created_at__lt=second.refund.created_at).select_related('receipt').order_by('pk'))
    first_allocations = [item for item in prior_allocations if item.created_at < first_boundary]
    first_snapshot = prior_run.allocation_snapshot if prior_run else []
    if (any(item.created_at == first_boundary for item in prior_allocations)
            or [item.pk for item in first_allocations] != [item[0] for item in first_snapshot]
            or len(run.allocation_snapshot) != len(prior_allocations)):
        raise ValidationError('首次退款前后到账分配时序异常')
    from users.models import PassReceiptUsageState
    state = PassReceiptUsageState.objects.filter(receipt_id=witness_id).first()
    if not state:
        raise ValidationError('到账用途账本缺失')
    def has_event(event):
        kind, key, request, amount, digest = event
        return state.events.filter(kind=kind, allocation_key=key, request_key=request,
                                   amount=amount, source_digest=digest).exists()
    first_normalized = []
    pre_main_runs = list(OrderAddonFundingReleaseRun.objects.filter(refund__order=order,
        refund__created_at__lt=first.refund.created_at).select_related('refund'))
    pre_main_runs += list(OrderAddonFundingReleaseRun.objects.filter(handover__payment__intent__order=order,
        handover__accepted_at__lt=first.refund.created_at).select_related('handover'))
    pre_main_runs += list(OrderAddonFundingReleaseRun.objects.filter(payment__intent__order=order,
        payment__buyer_transaction__created_at__lt=first.refund.created_at,
        created_at__lt=first.refund.created_at).select_related('payment'))
    if any(not item.ordinary_prestart_snapshot or not item.ordinary_prestart_hash
           or item.created_at >= first_boundary for item in pre_main_runs):
        raise ValidationError('首次主单退款前加购资金版本异常')
    pre_main_ids = {item.pk for item in pre_main_runs}
    for item, original in zip(first_allocations, first_snapshot):
        previous = list(OrderAddonFundingRelease.objects.filter(allocation=item,
            run__created_at__lt=first_boundary).select_related('run', 'allocation').order_by('pk'))
        if (not isinstance(original, list) or len(original) != 4 or original[:2] != [item.pk, item.amount]
                or original[3] != [] or any(row.run_id not in pre_main_ids for row in previous)
                or original[2] != sum(row.amount for row in previous)):
            raise ValidationError('首次退款的到账分配或前序释放异常')
        if item.receipt_id == witness_id:
            from users.receipt_usage_sources import _addon_release_facts
            for row in previous:
                if not has_event(_addon_release_facts(row, witness_receipt_id=witness_id)):
                    raise ValidationError('首次主单退款前到账释放用途缺失')
        first_normalized.append([item.pk, item.amount, original[2]])
    first_before = sum(item[1] - item[2] for item in first_normalized)
    first_amount = max(first_before - prior_run.required_amount, 0) if prior_run else 0
    first_parts = plan_release(allocations=first_normalized, amount=first_amount)
    if prior_run and (prior_run.allocated_before != first_before or prior_run.released_amount != first_amount
            or [(item.allocation_id, item.amount) for item in prior_run.releases.order_by('-allocation_id')]
               != [(item.allocation_id, item.amount) for item in first_parts]):
        raise ValidationError('首次退款实际释放不一致')
    normalized = []
    for allocation_item, snap in zip(prior_allocations, run.allocation_snapshot):
        _allocation_source(allocation_item, order, second.refund.created_at)
        if (OrderPointsFundingRelease.objects.filter(allocation=allocation_item, created_at__lte=run.created_at).exists()
                or OrderPointsTransferFundingRelease.objects.filter(
                    allocation=allocation_item, revision__created_at__lte=run.created_at).exists()
                or PointsPostRefundTransferRelease.objects.filter(
                    allocation=allocation_item, revision__created_at__lte=run.created_at).exists()
                or OrderAddonFundingRelease.objects.filter(allocation=allocation_item,
                    run__created_at=run.created_at).exclude(run=run).exists()):
            raise ValidationError('追加退款前其他补资释放尚未接通')
        earlier = list(OrderAddonFundingRelease.objects.filter(allocation=allocation_item,
            run__created_at__lt=run.created_at).exclude(run=run).select_related('run', 'allocation').order_by('pk'))
        if (not isinstance(snap, list) or len(snap) != 4 or snap[0] != allocation_item.pk
                or snap[1] != allocation_item.amount or snap[3] != []
                or any((not prior_run or item.run_id != prior_run.pk) and item.run_id not in payment_run_ids
                       and item.run_id not in refund_run_ids and item.run_id not in handover_run_ids
                       and item.run_id not in pre_main_ids
                       for item in earlier)
                or snap[2] != sum(item.amount for item in earlier)):
            raise ValidationError('追加退款此前到账释放异常')
        if allocation_item.receipt_id == witness_id:
            if not has_event(order_facts(allocation_item)):
                raise ValidationError('订单补资到账用途缺失')
            for item in earlier:
                from users.receipt_usage_sources import _addon_release_facts
                prior_event = _addon_release_facts(item, witness_receipt_id=witness_id)
                if not has_event(prior_event):
                    raise ValidationError('此前到账释放用途缺失')
        normalized.append([allocation_item.pk, allocation_item.amount, snap[2]])
    before = sum(amount - used for _, amount, used in normalized)
    from datetime import timedelta
    from types import SimpleNamespace
    second_boundary = second.refund.created_at - timedelta(microseconds=1)
    if second_boundary <= first_boundary:
        raise ValidationError('两次退款资金事件时序异常')
    second_facts = verified_funding(SimpleNamespace(
        negotiation_id=first.pk, negotiation=first, created_at=second_boundary,
        allocated_before=before), verify_snapshot=False)
    original = RefundedFunding(**second_facts['combined'])
    if (data.get('combined') != asdict(original)
            or data.get('addon_history') != second_facts['addon_history']
            or data.get('funding_uses') != [[pk, amount - used] for pk, amount, used in normalized if amount > used]
            or estimate.get('allocated_funding') != before):
        raise ValidationError('首次退款后的补资预留异常')
    people = data.get('people')
    if not isinstance(people, list) or len(people) != 2:
        raise ValidationError('双陪原收益范围异常')
    by_account = {item.get('account_id'): item for item in people if isinstance(item, dict)}
    assignments = list(order.providers.all())
    if (len(assignments) != 2 or len(by_account) != 2
            or set(by_account) != {item.provider_account_id for item in assignments}):
        raise ValidationError('双陪账号归属异常')
    from .addon_main_deduction import deductions_at
    before_first = deductions_at(order, before=first.created_at)
    before_second = deductions_at(order, before=second.refund.created_at)
    for assignment in assignments:
        item = by_account[assignment.provider_account_id]
        first_share = first_rows[0] if first_rows[0].account_id == assignment.provider_account_id else None
        earlier = before_first.get(assignment.provider_account_id, 0)
        total = before_second.get(assignment.provider_account_id, 0)
        amount = ((first_share.retained_income if first_share else assignment.provider_income - earlier)
                  - (total - earlier))
        snapshot = OrderSegmentFinancialSnapshot.objects.select_related('segment').filter(
            segment_id=item.get('segment_id'), segment__attribution__order=order,
            segment__provider_account_id=assignment.provider_account_id).first()
        if (item.get('assignment_id') != assignment.pk or item.get('user_id') != assignment.provider_id
                or not snapshot or not snapshot.segment.started_at
                or snapshot.incoming_basis != assignment.settlement_base
                or snapshot.income_at_entry != assignment.provider_income
                or snapshot.commission_type != assignment.commission_type
                or snapshot.commission_rate != assignment.commission_rate
                or snapshot.commission_fixed != assignment.commission_fixed
                or snapshot.game_commission_snapshot != assignment.game_commission_snapshot
                or item.get('main_income') != amount or item.get('affected') != bool(first_share)):
            raise ValidationError('双陪首次退款收益不一致')
    # Replay each paid add-on and verified handover at the second refund boundary.
    from .addon_handover import current_responsibilities
    addons = []
    for payment in payments:
        _, responsibilities = current_responsibilities(payment, before=second.refund.created_at)
        for part in responsibilities:
            person = by_account.get(part['provider_id'])
            if not person or person['segment_id'] != part['segment_id']:
                raise ValidationError('加购收益履约归属异常')
            addons.append({'payment_id': payment.pk, 'segment_id': part['segment_id'],
                           'account_id': part['provider_id'], 'basis': part['basis'],
                           'income': part['income']})
    if data.get('addons') != addons:
        raise ValidationError('加购收益与原结算预留不一致')
    if share.original_income != by_account[share.account_id]['main_income']:
        raise ValidationError('追加退款原收益不一致')
    sources = list(order.funding_allocations.order_by('pk'))
    parts = [RefundablePart(item.pk, item.kind, item.amount) for item in sources]
    first_return = plan_refund(parts, first.refund.amount)
    returned = plan_refund(parts, first.refund.amount + second.refund.amount)
    for kind, key in (('PREPAID', 'returned_principal'), ('GIFT', 'returned_gift'),
                      ('COMPENSATION', 'returned_compensation')):
        if estimate.get(key) != sum(returned[item.pk] - first_return[item.pk] for item in sources if item.kind == kind):
            raise ValidationError('追加退款原付款来源不一致')
    provider = original.provider_income_units - share.pending_deduction
    inviter = (order.amount - first.refund.amount - second.refund.amount) * (
        order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0) // 100
    margin = (original.net_principal_units - estimate['returned_principal']
              + original.planned_provider_external_units + second.refund.recovered_amount - provider - inviter)
    rounding = first.rounding_to_store_units + sum(order.addon_refunds.filter(
        created_at__lt=second.refund.created_at).values_list('rounding_surplus', flat=True))
    required = max(0, rounding - margin)
    if (estimate.get('provider_income') != provider or estimate.get('inviter_income') != inviter
            or estimate.get('required_funding') != required or estimate.get('store_income') != margin + required
            or estimate.get('funding_to_release') != max(before - required, 0)
            or estimate.get('funding_still_needed') != max(required - before, 0)
            or run.required_amount != required or run.allocated_before != before
            or run.released_amount != max(before - required, 0)
            or run.ordinary_refund_hash != release_digest(run, run.ordinary_refund_snapshot)):
        raise ValidationError('追加退款补资计算异常')
    expected_snapshot = {'followup_link_id': link.pk, 'execution_id': second.pk,
        'refund_id': second.refund_id, 'prior_facts_hash': projection['prior_facts_hash'],
        'required_units': required, 'allocated_before': before, 'release_units': run.released_amount}
    expected_parts = plan_release(allocations=normalized, amount=run.released_amount)
    if (run.ordinary_refund_snapshot != expected_snapshot
            or [(item.allocation_id, item.amount) for item in run.releases.order_by('-allocation_id')]
               != [(item.allocation_id, item.amount) for item in expected_parts]
            or not run.releases.filter(pk=row.pk, allocation=allocation, amount=row.amount).exists()):
        raise ValidationError('追加退款实际释放与资金快照不一致')
    return ('RELEASE', f'order:{allocation.pk}', f'ordinary-followup-release:{row.pk}', row.amount,
            _digest(['ordinary-followup-release', row.pk, row.amount, allocation.pk, allocation.receipt_id,
                     run.pk, run.negotiation_id, run.ordinary_refund_hash, run.allocation_snapshot]))
