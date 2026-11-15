"""Read ordinary negotiated-refund facts without taking cross-order locks.

This is only one part of receipt-local release verification.  In particular,
it does not prove the add-on history or the released funding allocation.
"""
from django.conf import settings
from rest_framework.exceptions import ValidationError


def _verify_direct_deposit(deficit, *, refund, account_id, user_id, amount, addon):
    """Verify the original debit and cycle; later repayments are separate events."""
    tx = deficit.source_store_transaction
    operator_id = refund.operator_id if addon else refund.operator_account_id
    legacy_id = refund.legacy_operator_id if addon else refund.operator_id
    key = f'addon:{refund.pk}:{deficit.profile_id}' if addon else f'{refund.pk}:{deficit.profile_id}'
    if (deficit.source_kind != 'DIRECT' or deficit.source_provider_transaction_id is not None
            or deficit.direct_key != key or deficit.account_id != account_id
            or deficit.profile.account_id != account_id or deficit.profile.user_id != user_id
            or deficit.amount != amount or not deficit.account_business_cycle
            or (deficit.addon_refund_id != refund.pk if addon else deficit.refund_id != refund.pk)
            or (deficit.refund_id is not None if addon else deficit.addon_refund_id is not None)
            or not tx or tx.order_id != refund.order_id or tx.tx_type != 'DEPOSIT_INCOME'
            or tx.status != 'SUCCESS' or tx.amount != -amount
            or tx.deposit_owner_id != account_id or tx.deposit_business_cycle != deficit.account_business_cycle
            or not tx.wallet.user or tx.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
            or tx.balance_after != tx.balance_before + tx.amount
            or tx.operator_account_id != operator_id or tx.operator_id != legacy_id
            or tx.reversal_of_id or tx.reversals.exists()):
        raise ValidationError('退款押金扣减的账号周期或原流水异常')


def _verified_prior_addon_refund(order, boundary):
    """Verify the limited prior add-on refund without taking financial locks."""
    from console.models import AdminAuditLog
    from engagement.funding_plan import RefundablePart, plan_refund
    from .models import OrderAddonRefund
    refunds = list(OrderAddonRefund.objects.filter(order=order, created_at__lt=boundary)
                   .select_related('payment__intent', 'proposal__case').order_by('pk'))
    if OrderAddonRefund.objects.filter(order=order, created_at=boundary).exists():
        raise ValidationError('普通主单退款前加购退款时序待核对')
    if refunds and (len(refunds) > 1 or order.addon_intents.count() >= 2
                    or any(item.payment.handovers.filter(status='ACCEPTED', accepted_at__lt=item.created_at).exists()
                           for item in refunds)):
        from .ordinary_multi_prior_addon_facts import verify
        verify(order, boundary)
        return
    if not refunds:
        return
    refund = refunds[0]
    payment, proposal = refund.payment, refund.proposal
    case = proposal.case
    if (payment.intent.order_id != order.pk or proposal.payment_id != payment.pk or case.order_id != order.pk
            or not 0 < refund.amount <= payment.intent.amount or proposal.amount != refund.amount
            or proposal.status != 'ACCEPTED' or not proposal.case_request_id or not proposal.accepted_at
            or proposal.created_at < proposal.case_opened_at or proposal.accepted_at > refund.created_at):
        raise ValidationError('普通主单退款前加购协商异常')
    people, decisions = list(proposal.participants.all()), list(proposal.decisions.all())
    if (len(people) != 1 or people[0].account_id != payment.intent.provider_id or len(decisions) != 2
            or any(not item.decided_at or item.decided_at < proposal.created_at
                   or item.decided_at > proposal.accepted_at for item in decisions)
            or proposal.accepted_at != max(item.decided_at for item in decisions)):
        raise ValidationError('普通主单退款前加购双方确认异常')
    sources = list(payment.allocations.order_by('pk'))
    planned = plan_refund([RefundablePart(row.pk, row.kind, row.amount, 0) for row in sources], refund.amount)
    expected = [{'allocation_id': row.pk, 'kind': row.kind, 'amount_raw': planned[row.pk]}
                for row in sources if planned[row.pk]]
    if proposal.source_plan != expected:
        raise ValidationError('普通主单退款前加购退款来源异常')
    from .ordinary_addon_prior_refund import verify_cumulative_sources
    verify_cumulative_sources(payment, sources)
    if not isinstance(case.history, list):
        raise ValidationError('普通主单退款前加购售后记录异常')
    entries = [item for item in case.history if isinstance(item, dict)
               and item.get('created_at') == proposal.case_opened_at.isoformat()
               and item.get('client_request_id') == proposal.case_request_id]
    if (len([item for item in entries if item.get('status') == 'OPEN']) != 1
            or len([item for item in entries if item.get('status') == 'RESOLVED']) != 1
            or any(item.get('affected_provider_account_ids') != [payment.intent.provider_id] for item in entries)):
        raise ValidationError('普通主单退款前加购售后记录异常')
    path = f'/api/admin/orders/{order.pk}/addons/payments/{payment.pk}/refund-proposals/'
    proposal_audits = list(AdminAuditLog.objects.filter(resource='addon-refund-proposal', object_id=str(proposal.pk),
        method='POST', path=path, status_code=200))
    refund_audits = list(AdminAuditLog.objects.filter(resource='addon-refund', object_id=str(refund.pk),
        method='POST', path=f'{path}{proposal.pk}/execute/', status_code=200))
    if (len(proposal_audits) != 1 or proposal_audits[0].operator_account_id != proposal.created_by_id
            or proposal_audits[0].request_body != {'order_id': order.pk, 'payment_id': payment.pk,
                'amount': refund.amount, 'explanation': proposal.explanation}
            or len(refund_audits) != 1 or refund_audits[0].operator_account_id != refund.operator_id
            or refund_audits[0].request_body != {'order_id': order.pk, 'payment_id': payment.pk,
                'proposal_id': proposal.pk, 'amount': refund.amount}):
        raise ValidationError('普通主单退款前加购操作审计异常')
    if refund.deposit_deduction:
        deficits = list(refund.deposit_deficits.select_related(
            'profile', 'source_store_transaction__wallet__user'))
        person = people[0]
        assignment = order.providers.filter(provider_account_id=person.account_id).first()
        if (len(deficits) != 1 or not assignment or not assignment.provider_id
                or person.deposit_deduction != refund.deposit_deduction):
            raise ValidationError('普通主单退款前加购押金承担异常')
        _verify_direct_deposit(deficits[0], refund=refund, account_id=person.account_id,
                               user_id=assignment.provider_id, amount=refund.deposit_deduction, addon=True)
    elif refund.deposit_deficits.exists():
        raise ValidationError('普通主单退款前存在多余的加购押金凭证')


def verified_shares(execution):
    from console.deposit_clearance import _verified_refund_execution
    from .addon_main_deduction import deductions_at
    from .negotiation_termination import verify_termination_evidence

    refund = execution.refund
    from .models import OrderOriginalShareTransfer
    first_transfer = OrderOriginalShareTransfer.objects.filter(
        previous_segment__attribution__order=refund.order).order_by('created_at', 'pk').first()
    if first_transfer and execution.termination_processed_at and execution.termination_processed_at < first_transfer.created_at:
        from .ordinary_positive_pretransfer import verify as verify_positive_pretransfer
        verified = verify_positive_pretransfer(refund.order, before=first_transfer.created_at)
        if verified.pk != execution.pk:
            raise ValidationError('退款前转单的原协商身份异常')
        return list(verified.shares.select_related('assignment', 'wallet_transaction__wallet',
            'deposit_deficit__profile', 'deposit_deficit__source_store_transaction__wallet__user').order_by('account_id'))
    checked = _verified_refund_execution(refund)
    if not checked or checked.pk != execution.pk:
        raise ValidationError('普通退款的协商与客户流水凭证待核对')
    verify_termination_evidence(checked)
    order, proposal = refund.order, checked.proposal
    if refund.benefit_effects.get('ordinary_transfer_negotiation_version') or refund.benefit_effects.get(
            'ordinary_transfer_followup_version'):
        raise ValidationError('转单退款的逐人凭证暂不能按普通主单核对')
    terms = proposal.provider_terms
    if not isinstance(terms, list) or any(not isinstance(item, dict) for item in terms):
        raise ValidationError('普通退款陪玩承担方案异常')
    by_account = {item.get('account_id'): item for item in terms}
    if len(by_account) != len(terms) or None in by_account:
        raise ValidationError('普通退款陪玩承担方案重复')
    prior = deductions_at(order, before=checked.created_at)
    shares = list(checked.shares.select_related(
        'assignment', 'wallet_transaction__wallet', 'deposit_deficit__profile',
        'deposit_deficit__source_store_transaction__wallet__user').order_by('account_id'))
    if [row.account_id for row in shares] != sorted(by_account):
        raise ValidationError('普通退款陪玩承担记录不完整')
    for row in shares:
        assignment, term = row.assignment, by_account[row.account_id]
        if (assignment.order_id != order.pk or assignment.provider_account_id != row.account_id
                or row.original_income != assignment.provider_income - prior.get(row.account_id, 0)
                or row.original_income != term.get('original_income_units')
                or row.burden != term.get('burden_units') or row.retained_income != term.get('retained_units')
                or row.original_income != row.pending_deduction + row.retained_income
                or row.burden != row.pending_deduction + row.wallet_deduction + row.deposit_deduction):
            raise ValidationError('普通退款陪玩承担记录异常')
        tx = row.wallet_transaction
        if bool(tx) != bool(row.wallet_deduction):
            raise ValidationError('普通退款陪玩钱包流水缺失')
        if tx and (tx.order_id != order.pk or tx.wallet.account_id != row.account_id
                or tx.wallet.user_id != assignment.provider_id or tx.tx_type != 'REFUND' or tx.status != 'SUCCESS'
                or tx.amount != -row.wallet_deduction or tx.balance_after != tx.balance_before + tx.amount
                or tx.operator_account_id != checked.operator_id or tx.operator_id != refund.operator_id
                or tx.reversal_of_id or tx.reversals.exists()):
            raise ValidationError('普通退款陪玩钱包流水异常')
        deficit = row.deposit_deficit
        if bool(deficit) != bool(row.deposit_deduction):
            raise ValidationError('普通退款陪玩押金流水缺失')
        if deficit:
            _verify_direct_deposit(deficit, refund=refund, account_id=row.account_id,
                                   user_id=assignment.provider_id, amount=row.deposit_deduction, addon=False)
    if (sum(row.burden for row in shares) - refund.amount != checked.rounding_to_store_units
            or refund.recovered_amount != sum(row.wallet_deduction + row.deposit_deduction for row in shares)):
        raise ValidationError('普通退款承担合计异常')
    return shares


def verified_funding(run, *, verify_snapshot=True):
    """Rebuild the original payment and add-on obligation without row locks.

    The caller must still prove the receipt's allocations and RELEASE events;
    this function alone must never grant access to a funding release.
    """
    from dataclasses import asdict, replace
    from engagement.funding_plan import RefundablePart, plan_refund
    from engagement.funding_wallet import validate_order_funding_sources
    from engagement.models import OrderFundingAllocation, OrderPointAllocation
    from .addon_funding_history import combined_at
    from .addon_payment import verify_order_debit_links
    from .models import OrderOriginalShareTransfer, OrderPlatformSubsidy, OrderPrestartHandoff
    from .points_negotiation_preview import funding_after_refund
    from .refunded_addon_funding import combine_refunded_history
    from .settlement import compute_provider_income

    if not run.negotiation_id or not run.created_at:
        raise ValidationError('普通退款补资来源不完整')
    execution = run.negotiation
    shares = verified_shares(execution)
    refund, order = execution.refund, execution.refund.order
    from .models import OrderAddonHandover
    from .ordinary_team_pre_main_handover import verify as verify_pre_main_handover
    for handover in OrderAddonHandover.objects.filter(payment__intent__order=order, status='ACCEPTED',
            accepted_at__lt=execution.created_at).select_related('payment__intent__order',
                'source_segment__attribution', 'target_segment__attribution'):
        verify_pre_main_handover(handover)
    _verified_prior_addon_refund(order, execution.created_at)
    refunds = list(order.refund_records.order_by('created_at', 'pk'))
    first_transfer = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).order_by('created_at', 'pk').first()
    positive_pretransfer = bool(first_transfer and execution.termination_processed_at < first_transfer.created_at)
    if (refund.created_at >= run.created_at or execution.termination_processed_at >= run.created_at
            or not refunds or refunds[0].pk != refund.pk or (not positive_pretransfer and len(refunds) > 2)
            or len({item.created_at for item in refunds}) != len(refunds)
            or any(item.created_at <= run.created_at for item in refunds[1:])
            or OrderPointAllocation.objects.filter(order=order).exists()
            or OrderPlatformSubsidy.objects.filter(order=order).exists()
            or OrderPrestartHandoff.objects.filter(order=order).exists()
            or (first_transfer and not positive_pretransfer)):
        raise ValidationError('普通退款补资来源超出当前核验范围')
    verify_order_debit_links(order, table_exists=True, lock=False)
    payments = list(order.transactions.filter(tx_type='PAY', status='SUCCESS', amount__lt=0).select_related('wallet'))
    if (len(payments) != 1 or payments[0].amount != -order.amount
            or payments[0].wallet.account_id != order.customer_account_id
            or payments[0].wallet.user_id != order.customer_id
            or payments[0].balance_after != payments[0].balance_before + payments[0].amount
            or payments[0].reversal_of_id or payments[0].reversals.exists()):
        raise ValidationError('普通退款原付款流水异常')
    sources = list(OrderFundingAllocation.objects.filter(order=order).select_related('lot').order_by('pk'))
    if not sources or sum(row.amount for row in sources) != order.amount:
        raise ValidationError('普通退款原付款来源异常')
    validate_order_funding_sources(order, sources)
    refundable = [RefundablePart(row.pk, row.kind, row.amount) for row in sources]
    expected = plan_refund(refundable, refund.amount)
    # Later refunds change the stored cumulative source balances, not the
    # first release's historical principal/gift/compensation mix. Require a
    # real, linked follow-up before using its cumulative amount for that
    # present-day consistency check.
    if positive_pretransfer and len(refunds) > 1:
        from console.deposit_clearance import _verified_refund_execution
        from .models import OrdinaryTransferFollowupRound
        from .ordinary_transfer_followup import _hash
        rounds = list(OrdinaryTransferFollowupRound.objects.filter(order=order, execution__refund__isnull=False)
                      .select_related('execution__refund').order_by('sequence'))
        positive_rounds = {item.execution.refund_id: item for item in rounds}
        if set(positive_rounds) != {item.pk for item in refunds[1:]}:
            raise ValidationError('后续转单退款逐轮来源缺失')
        for item in refunds[1:]:
            event = _verified_refund_execution(item)
            round_record = positive_rounds[item.pk]
            if (not event or event.pk != round_record.execution_id
                    or round_record.facts_hash != _hash(round_record.proposal_id, round_record.facts)):
                raise ValidationError('后续转单退款逐轮来源异常')
    elif len(refunds) == 2:
        from console.deposit_clearance import _verified_refund_execution
        from .models import NegotiationFollowupLink
        later = _verified_refund_execution(refunds[1])
        link = NegotiationFollowupLink.objects.filter(order=order, previous=execution,
            execution=later).first() if later else None
        if (not link or link.prior_refund_units != refund.amount
                or link.original_amount_units != order.amount
                or link.previous_terms_hash != execution.terms_hash):
            raise ValidationError('后续退款与首次普通退款的关联异常')
    cumulative = plan_refund(refundable, sum(item.amount for item in refunds))
    if any(row.refunded_amount != cumulative[row.pk] for row in sources):
        raise ValidationError('普通退款原付款来源退回异常')
    if positive_pretransfer:
        from types import SimpleNamespace
        from .models import OrderSegmentFinancialSnapshot
        initial = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order,
            segment__accepted_at__lt=execution.accepted_at).select_related('segment'))
        allocations = {item['account_id']: item for item in refund.negotiated_allocation}
        assignments = [SimpleNamespace(pk=allocations[item.segment.provider_account_id]['share_id'],
            provider_account_id=item.segment.provider_account_id, provider_income=item.income_at_entry,
            settlement_base=item.incoming_basis, commission_type=item.commission_type,
            commission_rate=item.commission_rate, commission_fixed=item.commission_fixed) for item in initial]
    else:
        assignments = list(order.providers.order_by('pk'))
    if (not assignments or any(not row.provider_account_id for row in assignments)
            or len({row.provider_account_id for row in assignments}) != len(assignments)
            or sum(row.settlement_base for row in assignments) != order.amount
            or (not positive_pretransfer and sum(row.provider_income for row in assignments) != order.provider_income)
            or any(row.commission_type not in ('PERCENT', 'FIXED') or row.provider_income !=
                   compute_provider_income(row.settlement_base, row.commission_type,
                                           row.commission_rate, row.commission_fixed) for row in assignments)):
        raise ValidationError('普通退款原陪玩分账异常')
    pending = {row.account_id: row.pending_deduction for row in shares}
    retained = {row.provider_account_id: row.provider_income - pending.get(row.provider_account_id, 0)
                for row in assignments}
    if min(retained.values()) < 0:
        raise ValidationError('普通退款陪玩保留收益异常')
    totals = {kind: sum(row.amount for row in sources if row.kind == kind)
              for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
    returned = {kind: sum(expected[row.pk] for row in sources if row.kind == kind) for kind in totals}
    rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    if order.inviter_commission != order.amount * rate // 100:
        raise ValidationError('普通退款推荐佣金快照异常')
    main = funding_after_refund(principal_units=totals['PREPAID'], gift_units=totals['GIFT'],
        compensation_units=totals['COMPENSATION'], returned_prepaid_units=returned['PREPAID'],
        returned_gift_units=returned['GIFT'], returned_compensation_units=returned['COMPENSATION'],
        provider_income_units=sum(row.provider_income for row in assignments), pending_deduction_units=sum(pending.values()),
        provider_external_units=refund.recovered_amount, inviter_rate=rate, allocated_units=run.allocated_before)
    segment_shares = [(row.pk, row.provider_account_id, 0, 0, retained[row.provider_account_id])
                      for row in assignments]
    if order.addon_intents.filter(payment__buyer_transaction__created_at__lte=run.created_at).exists():
        history = combined_at(order, main, run.created_at, segment_shares=segment_shares)
        combined = combine_refunded_history(main, history, main_retained_by_account=retained)
    else:
        history, combined = {'addon_history': []}, main
    rounding = execution.rounding_to_store_units + sum(order.addon_refunds.filter(
        created_at__lt=run.created_at).values_list('rounding_surplus', flat=True))
    if type(rounding) is not int or rounding < 0:
        raise ValidationError('普通退款取整金额异常')
    additional = max(rounding - combined.store_income_units, 0)
    if additional:
        required = combined.required_units + additional
        combined = replace(combined, store_income_units=combined.store_income_units + additional,
            required_units=required, release_units=max(run.allocated_before - required, 0),
            remaining_units=max(required - run.allocated_before, 0))
    facts = {'execution_id': execution.pk, 'refund_id': refund.pk,
             'combined': asdict(combined), 'addon_history': list(history['addon_history'])}
    from .ordinary_refunded_addon_release import _digest
    if verify_snapshot and (facts != run.ordinary_refund_snapshot or run.ordinary_refund_hash != _digest(run, facts)
            or run.required_amount != combined.required_units):
        raise ValidationError('普通退款补资历史快照异常')
    return facts


def verified_release(run, witness_receipt_id):
    """Rebuild allocations and prior releases under one witness receipt lock.

    This restricted proof does not post the current RELEASE event.  Writers
    must append it atomically before it can be accepted by the usage ledger.
    """
    from uuid import UUID
    from stores.context import current_store
    from stores.models import PlatformPointsFundingOperation
    from stores.platform_pass_repayment import _sha
    from users.models import PassReceiptUsageState
    from users.receipt_usage_sources import _addon_release_facts, order_facts
    from .models import (OrderAddonFundingRelease, OrderPointsFundingAction,
                         OrderPointsFundingAllocation, OrderPointsFundingRelease,
                         OrderPointsTransferFundingRelease, PointsPostRefundTransferRelease)
    from .points_funding_allocation import plan_release
    import json

    verified_funding(run)
    order_id = run.negotiation.refund.order_id
    allocations = list(OrderPointsFundingAllocation.objects.filter(service_order_id=order_id,
        created_at__lte=run.created_at).select_related('receipt').order_by('pk'))
    snapshot = run.allocation_snapshot
    if (not current_store() or not allocations or not isinstance(snapshot, list)
            or witness_receipt_id not in {row.receipt_id for row in allocations}
            or any(row.created_at == run.created_at or row.redemption_id is not None for row in allocations)
            or len(snapshot) != len(allocations)):
        raise ValidationError('普通退款共享到账分配范围待核对')
    state = PassReceiptUsageState.objects.filter(receipt_id=witness_receipt_id).first()
    if not state:
        raise ValidationError('普通退款到账用途账本缺失')
    def has_event(facts):
        kind, allocation_key, request_key, amount, digest = facts
        return state.events.filter(kind=kind, allocation_key=allocation_key, request_key=request_key,
                                   amount=amount, source_digest=digest).exists()
    normalized = []
    for allocation, item in zip(allocations, snapshot):
        if (not isinstance(item, list) or len(item) != 4 or item[0] != allocation.pk
                or item[1] != allocation.amount or type(item[2]) is not int or item[2] < 0 or item[3] != []
                or (allocation.receipt_id == witness_receipt_id and not has_event(order_facts(allocation)))):
            raise ValidationError('普通退款到账分配用途异常')
        try:
            key = allocation.request_key
            if not key.startswith('points:') or len(key) != 39:
                raise ValueError('invalid allocation key')
            operation_id = UUID(hex=key[7:])
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValidationError('普通退款补资操作编号异常') from exc
        operation = PlatformPointsFundingOperation.objects.using('default').select_related('claim').filter(
            pk=operation_id, claim__store__slug=current_store().slug).first()
        action = OrderPointsFundingAction.objects.filter(request_key=key).first()
        expected = {'receipt_id': allocation.receipt_id, 'allocation_id': allocation.pk,
                    'order_id': order_id, 'amount': allocation.amount}
        if (not operation or not operation.completed_at or operation.rejected_at or operation.voided_at
                or not action or operation.tenant_action_id != action.pk or action.allocation_id != allocation.pk
                or operation.claim.tenant_receipt_id != allocation.receipt_id
                or action.actor != str(operation.actor_id) or allocation.allocated_by != str(operation.actor_id)
                or action.result != expected or operation.payload_digest != _sha(json.dumps(
                    [str(operation.claim_id), order_id, allocation.amount], separators=(',', ':')))):
            raise ValidationError('普通退款补资平台凭证异常')
        # This reader can run while only one shared receipt is locked.  The
        # platform helper's ledger check would acquire other receipt locks.
        from .ordinary_followup_receipt import _allocation_source
        _allocation_source(allocation, run.negotiation.refund.order, run.created_at)
        if (OrderPointsFundingRelease.objects.filter(allocation=allocation, created_at__lte=run.created_at).exists()
                or OrderPointsTransferFundingRelease.objects.filter(
                    allocation=allocation, revision__created_at__lte=run.created_at).exists()
                or PointsPostRefundTransferRelease.objects.filter(
                    allocation=allocation, revision__created_at__lte=run.created_at).exists()):
            raise ValidationError('普通退款前其他补资释放尚未接通')
        earlier = list(OrderAddonFundingRelease.objects.filter(allocation=allocation,
            run__created_at__lte=run.created_at).exclude(run=run).select_related('run', 'allocation').order_by('pk'))
        if (any(row.run.created_at == run.created_at or not row.run.ordinary_prestart_snapshot for row in earlier)
                or sum(row.amount for row in earlier) != item[2]):
            raise ValidationError('普通退款前加购释放历史异常')
        for row in earlier:
            source = _addon_release_facts(row, witness_receipt_id=allocation.receipt_id)
            if allocation.receipt_id == witness_receipt_id and not has_event(source):
                raise ValidationError('普通退款前加购释放的到账用途异常')
        normalized.append([allocation.pk, allocation.amount, item[2]])
    before = sum(amount - previous for _, amount, previous in normalized)
    amount = max(before - run.required_amount, 0)
    parts = plan_release(allocations=normalized, amount=amount)
    if (before != run.allocated_before or amount != run.released_amount
            or [(row.allocation_id, row.amount) for row in run.releases.order_by('-allocation_id')]
            != [(row.allocation_id, row.amount) for row in parts]):
        raise ValidationError('普通退款到账释放金额异常')
    return run
