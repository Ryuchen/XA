"""Receipt-local evidence for an add-on refund after a transferred follow-up."""
from dataclasses import asdict, replace

from django.core.exceptions import ValidationError


def verify(run, witness_receipt_id):
    from engagement.funding_plan import RefundablePart, plan_refund
    from engagement.funding_wallet import validate_order_funding_sources
    from users.models import PassReceiptUsageState
    from users.receipt_usage_ledger import _locked_receipt
    from users.receipt_usage_sources import _addon_release_facts, _require_current_unlocked_reads, order_facts
    from .addon_funding_history import combined_at
    from .addon_main_deduction import deductions_at
    from .addon_payment import verify_receipt
    from .addon_refund_share_allocation import verify as verify_shares
    from .models import (NegotiationExecution, OrderAddonFundingRelease, OrderAddonFundingReleaseRun, OrderAddonPayment,
                         OrderPointsFundingAllocation, OrdinaryTransferFollowupRound)
    from .ordinary_followup_receipt import _allocation_source
    from .ordinary_refunded_addon_release import _digest
    from .ordinary_transfer_followup_receipt import verify as verify_followup
    from .points_funding_allocation import plan_release
    from .points_negotiation_preview import funding_after_refund
    from .refunded_addon_funding import combine_refunded_history

    if witness_receipt_id is not None:
        _locked_receipt(witness_receipt_id)
        _require_current_unlocked_reads()
    else:
        from stores.db import transaction
        if not transaction.get_connection().in_atomic_block:
            raise ValidationError('无到账分配须在订单资金事务中核验')
    refund = run.refund
    order, proposal, payment = refund.order, refund.proposal, refund.payment
    row = OrdinaryTransferFollowupRound.objects.filter(pk=run.ordinary_refund_snapshot.get('round_id'),
        order=order).select_related('execution__refund', 'previous__refund').first()
    if (not row or not row.execution.termination_processed_at
            or row.execution.termination_processed_at >= refund.created_at or refund.created_at >= run.created_at):
        raise ValidationError('追加协商后加购退款时序异常')
    ids = row.facts.get('prior_execution_ids', [])
    prior_events = list(NegotiationExecution.objects.filter(pk__in=ids).order_by('pk'))
    if not ids or len(prior_events) != len(set(ids)) or [item.pk for item in prior_events] != ids:
        raise ValidationError('追加协商历史轮次异常')
    from .ordinary_transfer_followup import _hash
    rounds = list(OrdinaryTransferFollowupRound.objects.filter(order=order, sequence__lte=row.sequence)
                  .select_related('execution').order_by('sequence'))
    if (len(rounds) != row.sequence or [item.sequence for item in rounds] != list(range(1, row.sequence + 1))
            or [item.execution_id for item in rounds] != ids[1:] + [row.execution_id]
            or any(item.previous_id != prior for item, prior in zip(rounds, ids))
            or any(item.facts_hash != _hash(item.proposal_id, item.facts) for item in rounds)):
        raise ValidationError('追加协商逐轮关联或快照异常')
    previous = OrderAddonFundingReleaseRun.objects.filter(negotiation=row.execution,
        created_at__lt=refund.created_at).first()
    if previous and row.execution.refund_id:
        if witness_receipt_id is None:
            raise ValidationError('无到账分配与已有追加资金版本不一致')
        verify_followup(previous, witness_receipt_id)
    elif witness_receipt_id is None:
        from .ordinary_followup_addon_release import facts_locked
        facts_locked(run)
    else:
        from collections import defaultdict
        from .ordinary_transfer_followup_zero import verify_termination
        from .ordinary_transfer_event_scope import segment_lines_at
        from .models import OrderOriginalShareTransfer, OrderTransferNegotiationEvidence
        from console.deposit_clearance import _verified_refund_execution
        from .negotiation_termination import verify_termination_evidence
        def verify_positive(execution):
            if not execution.refund_id or not _verified_refund_execution(execution.refund):
                raise ValidationError('追加协商实际退款凭证缺失')
            verify_termination_evidence(execution)
            if execution.proposal.ordinary_segment_pending_plan is not None:
                from .ordinary_retained_segment_shares import verify_execution_plan
                try:
                    verify_execution_plan(execution)
                except ValueError as exc:
                    raise ValidationError('协商逐段承担凭证异常') from exc
            terms = {item['account_id']: item for item in execution.proposal.provider_terms}
            shares = list(execution.shares.select_related('assignment', 'wallet_transaction__wallet',
                'deposit_deficit__profile', 'deposit_deficit__source_store_transaction__wallet__user'))
            if not shares or set(terms) != {item.account_id for item in shares}:
                raise ValidationError('追加协商受影响人异常')
            for share in shares:
                term, tx = terms[share.account_id], share.wallet_transaction
                if (share.assignment.order_id != order.pk or share.assignment.provider_account_id != share.account_id
                        or share.burden != term['burden_units'] or share.original_income != term['original_income_units']
                        or share.retained_income != term['retained_units']
                        or share.burden != share.pending_deduction + share.wallet_deduction + share.deposit_deduction
                        or share.original_income != share.pending_deduction + share.retained_income
                        or bool(tx) != bool(share.wallet_deduction)
                        or bool(share.deposit_deficit_id) != bool(share.deposit_deduction)):
                    raise ValidationError('追加协商本人资金承担异常')
                if tx and (tx.order_id != order.pk or tx.wallet.account_id != share.account_id
                        or tx.amount != -share.wallet_deduction or tx.tx_type != 'REFUND' or tx.status != 'SUCCESS'
                        or tx.balance_after != tx.balance_before + tx.amount or tx.reversal_of_id or tx.reversals.exists()):
                    raise ValidationError('追加协商本人钱包流水异常')
                if share.deposit_deduction:
                    from .ordinary_refund_receipt_evidence import _verify_direct_deposit
                    _verify_direct_deposit(share.deposit_deficit, refund=execution.refund,
                        account_id=share.account_id, user_id=share.assignment.provider_id,
                        amount=share.deposit_deduction, addon=False)
            if sum(share.burden for share in shares) != execution.refund.amount + execution.rounding_to_store_units:
                raise ValidationError('追加协商承担合计异常')
        if row.execution.refund_id:
            if OrderPointsFundingAllocation.objects.filter(service_order=order,
                    created_at__lte=row.execution.termination_processed_at).exists():
                raise ValidationError('追加协商时已有到账分配但资金版本缺失')
            verify_positive(row.execution)
        else:
            verify_termination(row.execution, lock=False)
        for index, event in enumerate(prior_events):
            if event.refund_id:
                prior_run = OrderAddonFundingReleaseRun.objects.filter(negotiation=event,
                    created_at__lt=row.execution.created_at).first()
                if prior_run:
                    from .addon_funding_release import verify_run as verify_main
                    verify_main(prior_run, _skip_usage=True, witness_receipt_id=witness_receipt_id)
                    continue
                if OrderPointsFundingAllocation.objects.filter(service_order=order,
                        created_at__lte=event.termination_processed_at).exists():
                    raise ValidationError('协商已有到账分配但资金版本缺失')
                verify_positive(event)
                if index:
                    continue
                first_evidence = OrderTransferNegotiationEvidence.objects.filter(order=order,
                    execution=event).first()
                if first_evidence:
                    from .negotiation_transfer_evidence import _digest as transfer_digest
                    first_lines = segment_lines_at(order, event.created_at)
                    if (first_evidence.snapshot_hash != transfer_digest(first_evidence.snapshot)
                            or first_evidence.snapshot.get('lines') != [asdict(line) for line in first_lines]
                            or first_evidence.snapshot.get('provider_terms') != event.proposal.provider_terms):
                        raise ValidationError('首协商时的履约段凭证异常')
                else:
                    from .ordinary_positive_pretransfer import verify as verify_positive_before_transfer
                    first_transfer = OrderOriginalShareTransfer.objects.filter(
                        previous_segment__attribution__order=order).order_by('created_at', 'pk').first()
                    if (not first_transfer or verify_positive_before_transfer(order,
                            before=first_transfer.created_at).pk != event.pk):
                        raise ValidationError('首协商前转单资金事实异常')
            elif index:
                verify_termination(event, lock=False)
            elif OrderTransferNegotiationEvidence.objects.filter(order=order, execution=event).exists():
                from .ordinary_transfer_zero_receipt import verify as verify_zero_first
                verify_zero_first(event)
            else:
                from .ordinary_zero_pretransfer import verify as verify_zero_before_transfer
                transfer = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order)
                transfer = transfer.order_by('created_at', 'pk').first()
                if not transfer or verify_zero_before_transfer(order, before=transfer.created_at).pk != event.pk:
                    raise ValidationError('零追加前主单协商来源异常')
        lines = segment_lines_at(order, row.created_at)
        if [asdict(line) for line in lines] != row.facts.get('segment_lines'):
            raise ValidationError('零追加时的履约段收益不一致')
        prior_deductions = deductions_at(order, before=row.created_at)
        if {str(key): value for key, value in prior_deductions.items()} != row.facts.get('addon_main_deductions'):
            raise ValidationError('零追加前加购收益扣减异常')
        from .addon_main_segment_deduction import verified_deductions
        by_segment = {line.segment_id: line for line in lines}
        segment_deductions = verified_deductions(order, before=row.created_at)
        selected = row.facts.get('segments', {})
        if sorted(segment for ids in selected.values() for segment in ids) != (row.execution.proposal.affected_segment_ids or []):
            raise ValidationError('追加协商确认的履约段范围异常')
        for term in row.execution.proposal.provider_terms:
            ids = selected.get(str(term['account_id']), [])
            if (not ids or len(ids) != len(set(ids))
                    or any(segment not in by_segment or by_segment[segment].account_id != term['account_id']
                           for segment in ids)
                    or sum(by_segment[segment].income - segment_deductions.get(segment, 0) for segment in ids)
                       != term['original_income_units']):
                raise ValidationError('追加协商确认的本人收益异常')
        outstanding = defaultdict(int)
        for line in lines:
            outstanding[line.account_id] += line.income
        for account, amount in prior_deductions.items():
            outstanding[account] -= amount
        for event in prior_events:
            for share in event.shares.all():
                outstanding[share.account_id] -= share.pending_deduction
        if ({str(account): value for account, value in sorted(outstanding.items())}
                != row.facts.get('outstanding_income') or any(value < 0 for value in outstanding.values())):
            raise ValidationError('零追加前本人待结收益异常')
    if (proposal.payment_id != payment.pk or payment.intent.order_id != order.pk
            or proposal.status != 'ACCEPTED' or proposal.amount != refund.amount
            or not proposal.accepted_at or proposal.accepted_at > refund.created_at):
        raise ValidationError('加购退款方案来源异常')
    verify_receipt(payment)
    verify_shares(refund)
    from .models import PointsHandoverRefundInvalidation
    from .ordinary_pending_handover_refund import capture as capture_invalidation
    from .points_handover_refund_invalidation import digest as invalidation_digest
    invalidations = list(PointsHandoverRefundInvalidation.objects.filter(refund=refund).select_related(
        'handover__payment__intent__order', 'handover__source_segment', 'handover__target_segment', 'refund'))
    for invalidation in invalidations:
        if (invalidation.handover.payment.intent.order_id != order.pk
                or not isinstance(invalidation.snapshot, dict)
                or invalidation.snapshot.get('kind') != 'ORDINARY_PENDING_REFUND_V1'
                or invalidation.created_at < refund.created_at or invalidation.created_at > run.created_at
                or invalidation.evidence_hash != invalidation_digest(invalidation.snapshot)
                or capture_invalidation(invalidation.handover, refund) != invalidation.snapshot):
            raise ValidationError('加购退款导致接替失效的真实凭证异常')
    people = list(proposal.participants.order_by('account_id'))
    decisions = list(proposal.decisions.values_list('account_id', 'role', 'accepted', 'decided_at'))
    expected = {(payment.intent.buyer_id, 'BOSS', True)} | {(p.account_id, 'PROVIDER', True) for p in people}
    if (not people or len({person.account_id for person in people}) != len(people)
            or len(decisions) != len(expected)
            or {(a, role, accepted) for a, role, accepted, _ in decisions} != expected
            or any(not at or at < proposal.created_at for _, _, _, at in decisions)
            or max(at for _, _, _, at in decisions) != proposal.accepted_at
            or sum(p.burden for p in people) != refund.amount + refund.rounding_surplus
            or sum(p.addon_pending_deduction + p.main_pending_deduction + p.wallet_deduction
                   + p.deposit_deduction for p in people) != refund.amount + refund.rounding_surplus):
        raise ValidationError('加购退款各方确认及承担异常')
    from .addon_handover import current_responsibilities
    _, responsibility = current_responsibilities(payment, before=refund.created_at, exclude_refund_id=refund.pk)
    weights = {person.account_id: sum(part['basis'] for part in responsibility
                                      if part['provider_id'] == person.account_id) for person in people}
    total = sum(weights.values())
    if not total or any(not amount for amount in weights.values()):
        raise ValidationError('加购退款本人份额缺失')
    from .addon_refund import _allocate_refund_basis, _allocate_segment_burden
    from .models import OrderParticipationSegment
    allocated = _allocate_refund_basis(refund.amount, list(weights.items()))
    rounding = {}
    for old in payment.refund_records.filter(created_at__lt=refund.created_at):
        for participant in old.proposal.participants.all():
            for entry in participant.share_plan:
                key = (participant.account_id, entry['segment_id'])
                rounding[key] = rounding.get(key, 0) + entry['burden'] - entry['refund_basis']
    payments = list(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__lt=refund.created_at).order_by('pk'))
    payments.sort(key=lambda item: (item.pk != payment.pk, item.pk))
    after_main = {}
    for person in people:
        burden = ((refund.amount * weights[person.account_id] + total * 10 - 1) // (total * 10)) * 10
        grouped = {}
        for part in responsibility:
            if part['provider_id'] == person.account_id and part['basis']:
                item = grouped.setdefault(part['segment_id'], {'basis': 0, 'income': 0})
                item['basis'] += part['basis']
                item['income'] += part['income']
        portions = _allocate_refund_basis(allocated[person.account_id],
            [(segment_id, item['basis']) for segment_id, item in sorted(grouped.items())])
        entries = []
        for segment_id, item in sorted(grouped.items()):
            capacity = max(0, min(item['basis'], item['basis'] - rounding.get((person.account_id, segment_id), 0)))
            entries.append({'segment_id': segment_id, 'basis': item['basis'], 'income': item['income'],
                            'refund_basis': portions[segment_id], 'burden_capacity': capacity})
        latest = list(OrderParticipationSegment.objects.filter(pk__in=portions)
                      .order_by('-started_at', '-accepted_at', '-pk').values_list('pk', flat=True))
        excess = _allocate_segment_burden(entries, burden, latest)
        if (person.basis != weights[person.account_id] or person.burden != burden
                or person.excess_burden != excess or person.share_plan != entries):
            raise ValidationError('加购退款本人承担与实际份额不一致')
        remaining, expected_deductions = burden, []
        for addon in payments:
            _, parts = current_responsibilities(addon, before=refund.created_at, exclude_refund_id=refund.pk)
            grouped = {}
            for part in parts:
                if part['provider_id'] == person.account_id:
                    grouped[part['segment_id']] = grouped.get(part['segment_id'], 0) + part['income']
            for segment_id, amount in sorted(grouped.items()):
                take = min(remaining, amount)
                if take:
                    expected_deductions.append((addon.pk, segment_id, take))
                    remaining -= take
        actual = list(person.earning_deductions.order_by('pk').values_list('payment_id', 'segment_id', 'amount'))
        if actual != expected_deductions:
            raise ValidationError('加购退款逐付款收益承担异常')
        from .ordinary_after_main_segment_income import deduction_at
        expected_main = (deduction_at(order, prior_events[0], list(prior_events[0].shares.all()), person.account_id,
                                     refund.created_at, remaining, followup=row) if remaining else [])
        actual_main = list(person.main_segment_deductions.order_by('pk').values_list('segment_id', 'amount'))
        main_amount = sum(part[1] for part in expected_main)
        if actual_main != expected_main or person.main_pending_deduction != main_amount:
            raise ValidationError('加购退款逐段主单收益承担异常')
        after_main[person.pk] = remaining - main_amount
    tx = refund.buyer_transaction
    if (not tx or tx.wallet.account_id != order.customer_account_id or tx.amount != refund.amount
            or tx.tx_type != 'REFUND' or tx.status != 'SUCCESS'
            or tx.balance_after != tx.balance_before + tx.amount or tx.reversal_of_id or tx.reversals.exists()):
        raise ValidationError('加购退款老板流水异常')
    for person in people:
        if (sum(person.earning_deductions.values_list('amount', flat=True)) != person.addon_pending_deduction
                or sum(person.main_segment_deductions.values_list('amount', flat=True)) != person.main_pending_deduction):
            raise ValidationError('加购退款逐段扣减缺失')
        tx = person.provider_transaction
        if bool(tx) != bool(person.wallet_deduction):
            raise ValidationError('加购退款钱包流水缺失')
        assignment = order.providers.get(provider_account_id=person.account_id)
        if tx and (tx.wallet.account_id != person.account_id or tx.wallet.user_id != assignment.provider_id
                or tx.order_id is not None or tx.amount != -person.wallet_deduction or tx.balance_before < 0
                or tx.tx_type != 'REFUND' or tx.status != 'SUCCESS'
                or tx.balance_after != tx.balance_before + tx.amount or tx.reversal_of_id or tx.reversals.exists()
                or tx.operator_account_id != refund.operator_id or tx.operator_id != refund.legacy_operator_id
                or person.wallet_deduction != min(after_main[person.pk], tx.balance_before)):
            raise ValidationError('加购退款本人钱包流水异常')
        if after_main[person.pk] - person.wallet_deduction != person.deposit_deduction:
            raise ValidationError('加购退款钱包和押金承担顺序异常')
        deficits = list(refund.deposit_deficits.filter(account_id=person.account_id)
                        .select_related('profile', 'source_store_transaction__wallet__user'))
        if len(deficits) != (1 if person.deposit_deduction else 0):
            raise ValidationError('加购退款本人押金凭证缺失')
        if deficits:
            from .ordinary_refund_receipt_evidence import _verify_direct_deposit
            _verify_direct_deposit(deficits[0], refund=refund, account_id=person.account_id,
                                   user_id=assignment.provider_id, amount=person.deposit_deduction, addon=True)
            if not person.wallet_deduction:
                witness = refund.participant_zero_wallet_evidence.filter(participant=person).select_related('wallet').first()
                if not witness:
                    witness = getattr(refund, 'zero_wallet_evidence', None)
                if (not witness or witness.account_id != person.account_id or witness.wallet.account_id != person.account_id
                        or witness.wallet.user_id != assignment.provider_id or witness.business_cycle != deficits[0].account_business_cycle
                        or not witness.business_cycle or witness.balance != 0 or not witness.is_active
                        or witness.created_at < refund.created_at or witness.created_at > run.created_at):
                    raise ValidationError('加购退款本人零钱包凭证异常')

    allocations = list(OrderPointsFundingAllocation.objects.filter(service_order=order,
        created_at__lt=run.created_at).select_related('receipt').order_by('pk'))
    if [item.pk for item in allocations] != [item[0] for item in run.allocation_snapshot]:
        raise ValidationError('加购退款到账分配不一致')
    if witness_receipt_id is None and allocations:
        raise ValidationError('无到账核验不允许已有分配')
    state = PassReceiptUsageState.objects.filter(receipt_id=witness_receipt_id).first() if witness_receipt_id else None
    if witness_receipt_id is not None and not state:
        raise ValidationError('到账用途账本缺失')
    from .ordinary_transfer_addon_history import verify_handovers
    from .addon_funding_release import verify_run as verify_addon_run
    from .models import OrdinaryPostRefundTransferRevision
    verified_unallocated_prior = False
    for addon in OrderAddonPayment.objects.filter(intent__order=order,
            buyer_transaction__created_at__lt=run.created_at).select_related('intent', 'buyer_transaction'):
        verify_handovers(order, addon, before=run.created_at, witness_receipt_id=witness_receipt_id)
        for old_refund in addon.refund_records.filter(created_at__lt=refund.created_at):
            old_run = OrderAddonFundingReleaseRun.objects.filter(refund=old_refund).first()
            revision = OrdinaryPostRefundTransferRevision.objects.filter(order=order,
                source_kind='ADDON_REFUND', source_id=old_refund.pk).first()
            if revision:
                from .ordinary_post_refund_transfer import verify_locked as verify_revision
                if revision.created_at >= refund.created_at:
                    raise ValidationError('前序加购退款转单版本时序异常')
                verify_revision(revision)
                if old_run and not (revision.snapshot.get('pre_main_transfer_v1')
                                    and revision.snapshot.get('zero_pretransfer_execution_id')):
                    raise ValidationError('前序加购退款存在重复资金版本')
                if not old_run:
                    old_run = OrderAddonFundingReleaseRun.objects.filter(ordinary_transfer_revision=revision).first()
                    if not old_run and not OrderPointsFundingAllocation.objects.filter(service_order=order,
                            created_at__lte=revision.created_at).exists():
                        continue
            if not old_run:
                if (old_refund.created_at >= prior_events[0].created_at
                        or OrderPointsFundingAllocation.objects.filter(service_order=order,
                            created_at__lte=old_refund.created_at).exists()):
                    raise ValidationError('前序加购退款资金版本缺失')
                if not verified_unallocated_prior:
                    from .ordinary_multi_prior_addon_facts import verify as verify_pre_main_refunds
                    verify_pre_main_refunds(order, prior_events[0].created_at)
                    verified_unallocated_prior = True
                continue
            if old_run.created_at >= refund.created_at:
                raise ValidationError('前序加购退款资金版本缺失')
            verify_addon_run(old_run, _skip_usage=True, witness_receipt_id=witness_receipt_id)
    for allocation, entry in zip(allocations, run.allocation_snapshot):
        _allocation_source(allocation, order, run.created_at)
        prior = list(OrderAddonFundingRelease.objects.filter(allocation=allocation,
            run__created_at__lt=run.created_at).select_related('run', 'allocation__receipt').order_by('pk'))
        from .models import OrderPointsFundingRelease, OrderPointsTransferFundingRelease, PointsPostRefundTransferRelease
        transfer = list(OrderPointsTransferFundingRelease.objects.filter(allocation=allocation,
            revision__created_at__lt=run.created_at).order_by('pk').values_list('pk', 'amount'))
        main = list(OrderPointsFundingRelease.objects.filter(allocation=allocation,
            created_at__lt=run.created_at).order_by('pk').values_list('pk', 'amount'))
        post = list(PointsPostRefundTransferRelease.objects.filter(allocation=allocation,
            revision__created_at__lt=run.created_at).order_by('pk').values_list('pk', 'amount'))
        if entry != [allocation.pk, allocation.amount, sum(item.amount for item in prior),
                     [list(item) for item in transfer], [list(item) for item in main],
                     [list(item) for item in post]]:
            raise ValidationError('加购退款此前到账释放不一致')
        if allocation.receipt_id == witness_receipt_id:
            events = [order_facts(allocation)] + [_addon_release_facts(item, witness_receipt_id=witness_receipt_id)
                                                   for item in prior]
            for kind, key, request, amount, digest in events:
                if not state.events.filter(kind=kind, allocation_key=key, request_key=request,
                                           amount=amount, source_digest=digest).exists():
                    raise ValidationError('加购退款此前到账用途异常')

    sources = list(order.funding_allocations.select_related('lot').order_by('pk'))
    validate_order_funding_sources(order, sources)
    before = {item['id']: item for item in row.facts['funding_sources']}
    if set(before) != {item.pk for item in sources}:
        raise ValidationError('追加协商原付款来源缺失')
    delta = plan_refund([RefundablePart(item.pk, item.kind, item.amount, before[item.pk]['refunded_amount'])
                         for item in sources], row.execution.refund.amount if row.execution.refund_id else 0)
    kinds = ('PREPAID', 'GIFT', 'COMPENSATION')
    paid = {kind: sum(item.amount for item in sources if item.kind == kind) for kind in kinds}
    returned = {kind: sum(before[item.pk]['refunded_amount'] + delta[item.pk]
                          for item in sources if item.kind == kind) for kind in kinds}
    from .models import OrderOriginalShareTransfer
    transferred_after_followup = OrderOriginalShareTransfer.objects.filter(
        previous_segment__attribution__order=order, created_at__gt=row.execution.termination_processed_at,
        created_at__lt=run.created_at).exists()
    if transferred_after_followup:
        from .ordinary_followup_transferred_addon_plan import plan_at
        result, history = plan_at(order, row, boundary=run.created_at, allocated=run.allocated_before)
    else:
        base = {int(key): value for key, value in row.facts['addon_main_deductions'].items()}
        retained = {int(key): value for key, value in row.facts['outstanding_income'].items()}
        for share in row.execution.shares.all():
            retained[share.account_id] -= share.pending_deduction
        current = deductions_at(order, before=run.created_at)
        if any(current.get(key, 0) < value for key, value in base.items()):
            raise ValidationError('加购主单扣减记录异常')
        for account, amount in current.items():
            if account not in retained:
                raise ValidationError('加购主单扣减本人不存在')
            retained[account] -= amount - base.get(account, 0)
        if any(value < 0 for value in retained.values()):
            raise ValidationError('加购退款后本人收益不足')
        prior = [*prior_events, row.execution]
        external = sum(p.wallet_deduction + p.deposit_deduction for event in prior for p in event.shares.all())
        main = funding_after_refund(principal_units=paid['PREPAID'], gift_units=paid['GIFT'],
            compensation_units=paid['COMPENSATION'], returned_prepaid_units=returned['PREPAID'],
            returned_gift_units=returned['GIFT'], returned_compensation_units=returned['COMPENSATION'],
            provider_income_units=sum(retained.values()) + sum(current.values()), pending_deduction_units=0,
            provider_external_units=external,
            inviter_rate=order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0,
            allocated_units=run.allocated_before)
        lines = row.facts['segment_lines']
        history = combined_at(order, main, run.created_at, segment_shares=[
            (line['segment_id'], line['account_id'], line['basis'], line['fee'], line['income']) for line in lines])
        limits = {account: value + current.get(account, 0) for account, value in retained.items()}
        result = combine_refunded_history(main, history, main_retained_by_account=limits) if history.get('addon_history') else main
        from .ordinary_transfer_addon_rounding import facts as rounding_facts
        rounding = sum(event.rounding_to_store_units for event in prior) + sum(
            item[1] for item in rounding_facts(order, before=run.created_at))
        extra = max(rounding - result.store_income_units, 0)
        if extra:
            required = result.required_units + extra
            result = replace(result, store_income_units=result.store_income_units + extra, required_units=required,
                release_units=max(run.allocated_before - required, 0), remaining_units=max(required - run.allocated_before, 0))
    facts = {'kind': 'ordinary_followup_addon_refund_v1', 'round_id': row.pk, 'refund_id': refund.pk,
             'funding': asdict(result), 'addon_history': history.get('addon_history', [])}
    if (run.ordinary_refund_snapshot != facts or run.ordinary_refund_hash != _digest(run, facts)
            or run.required_amount != result.required_units or run.released_amount != result.release_units):
        raise ValidationError('加购退款资金版本异常')
    normalized = [[item[0], item[1], item[2] + sum(entry[1] for group in item[3:] for entry in group)]
                  for item in run.allocation_snapshot]
    parts = plan_release(allocations=normalized, amount=result.release_units)
    if [(p.allocation_id, p.amount) for p in run.releases.order_by('-allocation_id')] != [
            (p.allocation_id, p.amount) for p in parts]:
        raise ValidationError('加购退款到账释放不一致')
    return run
