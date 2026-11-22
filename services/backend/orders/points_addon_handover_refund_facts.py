"""Receipt-local facts for refunds after an add-on handover.

This does not authorize a refund.  The caller must already hold the order,
money and receipt locks, or use a verified receipt-local current read.
"""
from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as RestValidationError


def _sum_segments(rows):
    """Keep every verified share while rebuilding segment-level totals."""
    result = {}
    for segment_id, account_id, basis, income in rows:
        value = result.setdefault((segment_id, account_id), {'basis': 0, 'income': 0})
        value['basis'] += basis
        value['income'] += income
    return result


def _same_seat_cross_handovers(refund, first, other_handover):
    """Find the first handovers at the refund boundary in payment posting order."""
    from .models import OrderAddonHandover, OrderAddonPayment, OrderOriginalShareTransfer
    from .points_transfer_refund_access import same_seat_addon_payments

    if (not other_handover or first.payment_id != refund.payment_id or first.payment_id == other_handover.payment_id
            or any(row.kind != 'STANDARD' or row.previous_id or not row.accepted_at
                   or not row.created_at <= row.accepted_at < refund.created_at for row in (first, other_handover))
            or (first.source_segment_id, first.target_segment_id) !=
               (other_handover.source_segment_id, other_handover.target_segment_id)):
        return ()
    order = refund.order
    if (order.refund_records.filter(created_at__lte=refund.created_at).exists()
            or order.prestart_handoffs.filter(created_at__lte=refund.created_at).exists()):
        return ()
    transfers = list(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
        created_at__lte=refund.created_at).select_related('previous_segment').order_by('pk')[:2])
    paid = list(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__lte=refund.created_at).select_related('intent', 'buyer_transaction'))
    paid_ids = {row.pk for row in paid}
    debit_ids = {first.payment_id, other_handover.payment_id}
    if (len(transfers) != 1 or transfers[0].previous_segment_id != first.source_segment_id
            or transfers[0].next_segment_id != first.target_segment_id
            or transfers[0].created_at >= min(first.created_at, other_handover.created_at)
            or not debit_ids <= paid_ids or not same_seat_addon_payments(order, paid, transfers[0])):
        return ()
    if paid_ids == debit_ids:
        return first, other_handover
    if refund.main_pending_deduction or refund.wallet_deduction or refund.deposit_deduction:
        return ()
    handovers = list(OrderAddonHandover.objects.filter(payment_id__in=paid_ids,
        status='ACCEPTED', accepted_at__lte=refund.created_at).select_related('payment__intent'))
    by_payment = {row.payment_id: row for row in handovers}
    if (by_payment.keys() != paid_ids or len(by_payment) != len(handovers)
            or by_payment[first.payment_id].pk != first.pk or by_payment[other_handover.payment_id].pk != other_handover.pk
            or any(row.kind != 'STANDARD' or row.previous_id
                   or (row.source_segment_id, row.target_segment_id) != (first.source_segment_id, first.target_segment_id)
                   or not transfers[0].created_at < row.created_at <= row.accepted_at < refund.created_at
                   for row in handovers)):
        return ()
    return first, *(by_payment[pk] for pk in sorted(paid_ids - {first.payment_id}))


def _same_seat_cross_income(refund, handover, segment_id, account_id, *, receipt_id):
    """Rebuild another payment's earnings immediately before the refund."""
    from .addon_funding_release import verify_run
    from .addon_handover import verify_handover
    from .models import (OrderAddonFundingReleaseRun, OrderAddonSettlement, OrderPointsFundingAllocation,
                         OrderSegmentAddonSettlement)

    payment = handover.payment
    run = OrderAddonFundingReleaseRun.objects.filter(handover=handover, created_at__lt=refund.created_at).first()
    if (segment_id != handover.target_segment_id
            or payment.handovers.filter(status='ACCEPTED', accepted_at=refund.created_at).exists()
            or payment.refund_earning_deductions.filter(refund__created_at=handover.accepted_at).exists()
            or (OrderPointsFundingAllocation.objects.filter(redemption__order=refund.order,
                    created_at__lte=handover.accepted_at).exists()
                and (not run or not run.points_handover_hash or not run.points_handover_snapshot))
            or OrderAddonSettlement.objects.filter(payment=payment, segment_id=segment_id,
                settled_at__lte=refund.created_at).exists()
            or OrderSegmentAddonSettlement.objects.filter(payment=payment, segment_id=segment_id,
                settled_at__lte=refund.created_at).exists()):
        raise ValidationError('跨加购原接替履约段或资金凭证异常')
    try:
        shares = verify_handover(payment, handover, as_of=refund.created_at, receipt_id=receipt_id)
        if run:
            verify_run(run, _skip_usage=True, witness_receipt_id=receipt_id)
    except RestValidationError as exc:
        raise ValidationError('跨加购原接替份额待核对') from exc
    matching = [row for row in shares if row.segment_id == segment_id
                and row.segment.provider_account_id == account_id]
    if len(matching) != 1:
        raise ValidationError('跨加购扣减不属于同一陪玩的原履约段')
    used = sum(payment.refund_earning_deductions.filter(segment_id=segment_id,
        refund__created_at__gt=handover.accepted_at,
        refund__created_at__lt=refund.created_at).values_list('amount', flat=True))
    return matching[0].income - used


def _verify_same_seat_debits(refund, handovers, person, state, deductions, *, receipt_id):
    """Replay the posting order, including an earlier payment with no debit row."""
    segment_id = handovers[0].target_segment_id
    message = '跨加购待结收益未按原付款顺序扣减'
    if any(row.segment_id != segment_id for row in deductions):
        raise ValidationError(message)
    remaining = person.burden
    expected = {}
    for handover in handovers:
        available = (state.get((segment_id, person.account_id), {}).get('income', -1)
                     if handover.payment_id == refund.payment_id else
                     _same_seat_cross_income(refund, handover, segment_id, person.account_id, receipt_id=receipt_id))
        if available < 0:
            raise ValidationError(message)
        amount = min(available, remaining)
        if amount:
            expected[handover.payment_id] = amount
            remaining -= amount
        if not remaining:
            break
    if remaining or expected != {row.payment_id: row.amount for row in deductions}:
        raise ValidationError(message)


def followup_cross_income(refund, deduction, *, receipt_id=None):
    """Prove another payment's net segment income immediately before a refund."""
    from .addon_payment import verify_receipt
    from .models import OrderAddonFundingReleaseRun
    from .points_transfer_followup_handover_scope import verify_event
    from .points_transfer_followup_addon_version import _verify_prior_run
    payment = deduction.payment
    if (payment.intent.order_id != refund.order_id or payment.pk == refund.payment_id
            or payment.buyer_transaction.created_at >= refund.created_at
            or payment.handovers.filter(status='ACCEPTED', accepted_at=refund.created_at).exists()
            or payment.refund_earning_deductions.filter(refund__created_at=refund.created_at)
                .exclude(refund=refund).exists()):
        raise ValidationError('跨加购资金事件时序异常')
    latest = payment.handovers.filter(status='ACCEPTED', accepted_at__lt=refund.created_at)\
        .order_by('-accepted_at', '-pk').first()
    run = OrderAddonFundingReleaseRun.objects.filter(handover=latest,
        points_followup_snapshot__kind='TRANSFER_FOLLOWUP_V1').first() if latest else None
    if not run or run.created_at < latest.accepted_at or run.created_at >= refund.created_at:
        raise ValidationError('跨加购接替资金版本缺失或时序异常')
    try:
        verify_receipt(payment)
        shares = verify_event(payment, latest, before=refund.created_at, receipt_id=receipt_id)
        _verify_prior_run(run, receipt_id=receipt_id)
    except RestValidationError as exc:
        raise ValidationError('跨加购接替及资金凭证异常') from exc
    matches = [row for row in shares if row.segment_id == deduction.segment_id
               and row.segment.provider_account_id == deduction.participant.account_id]
    if not matches:
        raise ValidationError('跨加购接替收益不属于该陪玩履约段')
    from .addon_refund_share_allocation import verify as verify_share_allocations
    verify_share_allocations(refund)
    from .addon_refund_cross_plan import verify_posted
    verify_posted(refund)
    used = sum(payment.refund_earning_deductions.filter(segment_id=deduction.segment_id,
        participant__account_id=deduction.participant.account_id,
        refund__created_at__gt=latest.accepted_at,
        refund__created_at__lt=refund.created_at).values_list('amount', flat=True))
    return sum(row.income for row in matches) - used


def first_refund_facts(refund, *, receipt_id=None, _memo=None):
    if _memo is None:
        _memo = {}
    if refund.pk in _memo:
        return _memo[refund.pk]
    from users.receipt_usage_sources import _require_current_unlocked_reads
    from .addon_handover import verify_handover
    from .models import OrderAddonHandover, OrderAddonRefund, OrderAddonFundingReleaseRun
    _require_current_unlocked_reads()
    payment, proposal, order = refund.payment, refund.proposal, refund.order
    handovers = list(OrderAddonHandover.objects.filter(payment=payment, status='ACCEPTED',
        accepted_at__lt=refund.created_at).order_by('accepted_at', 'pk'))
    team_run = (OrderAddonFundingReleaseRun.objects.filter(handover=handovers[-1],
        points_handover_snapshot__kind='SAME_TEAM_V1').first() if handovers else None)
    if not receipt_id and not team_run:
        from django.db.models import Q
        from .models import OrderPointsFundingAllocation
        witness = OrderPointsFundingAllocation.objects.filter(
            Q(redemption__order=order) | Q(service_order=order)).order_by('pk').first()
        if witness:
            # The order-money caller prelocks its actual receipts. Use an
            # existing receipt as witness, including during initial posting;
            # do not route an allocated order through the zero-receipt path.
            from users.receipt_usage_ledger import _locked_receipt
            _locked_receipt(witness.receipt_id)
            receipt_id = witness.receipt_id
        else:
            from .points_transfer_refund_evidence import _facts_without_receipt_locked
            _facts_without_receipt_locked(order)
    earlier = list(OrderAddonRefund.objects.filter(order=order, created_at__lt=refund.created_at)
                   .select_related('payment', 'proposal', 'order').order_by('created_at', 'pk'))
    if (not handovers or len({item.created_at for item in earlier}) != len(earlier)
            or OrderAddonRefund.objects.filter(order=order, created_at=refund.created_at).exclude(pk=refund.pk).exists()
            or payment.refund_earning_deductions.filter(refund__created_at__lt=refund.created_at)
                .exclude(refund__in=earlier).exists()
            or payment.handovers.filter(status='ACCEPTED', accepted_at=refund.created_at).exists()
            or proposal.payment_id != payment.pk or payment.intent.order_id != order.pk
            or proposal.status != 'ACCEPTED' or proposal.amount != refund.amount):
        raise ValidationError('接替后退款事件范围待核对')
    prior_own = [item for item in earlier if item.payment_id == payment.pk
                 and item.created_at < handovers[-1].accepted_at]
    try:
        if team_run:
            from .addon_funding_release import verify_run
            if team_run.refunded_main_snapshot is not None:
                from .points_team_postmain_handover_facts import verify_event_current
                verify_event_current(team_run, receipt_id=receipt_id)
                from .points_team_postmain_handover_facts import facts as team_postmain_facts
                team_postmain_facts(team_run, receipt_id=receipt_id)
            else:
                verify_run(team_run, _skip_usage=True, witness_receipt_id=receipt_id)
                from .points_team_addon_handover_facts import verify_locked
                verify_locked(order, payment, handovers[-1], before=refund.created_at)
            shares = list(handovers[-1].shares.select_related('segment').order_by('position'))
        else:
            followup = OrderAddonFundingReleaseRun.objects.filter(handover=handovers[-1],
                points_followup_snapshot__kind='TRANSFER_FOLLOWUP_V1').first()
            if followup:
                from .points_transfer_followup_handover_scope import verify_event
                from .points_transfer_followup_addon_version import _verify_prior_run
                if followup.created_at >= refund.created_at:
                    raise ValidationError('接替资金版本晚于退款')
                shares = verify_event(payment, handovers[-1], before=refund.created_at, receipt_id=receipt_id)
                _verify_prior_run(followup, receipt_id=receipt_id)
            else:
                shares = verify_handover(payment, handovers[-1], as_of=refund.created_at, receipt_id=receipt_id)
    except RestValidationError as exc:
        raise ValidationError('接替前原始份额凭证异常') from exc
    original = _sum_segments((row.segment_id, row.segment.provider_account_id, row.basis, row.income)
                             for row in shares)
    state = {key: {**value, 'rounding': 0} for key, value in original.items()}
    from .addon_refund_share_allocation import verify as verify_share_allocations
    verify_share_allocations(refund)
    prior_facts = []
    for prior in earlier:
        before_handover = prior.created_at < handovers[-1].accepted_at
        if prior.payment_id == payment.pk and not before_handover:
            prior_fact = first_refund_facts(prior, receipt_id=receipt_id, _memo=_memo)
            if team_run:
                from .points_addon_receipt_usage import digest
                prior_facts.append({'refund_id': prior.pk, 'facts_hash': digest(prior_fact)})
            else:
                prior_facts.append(prior_fact)
        else:
            from .points_addon_receipt_usage import facts as other_refund_facts
            from .points_team_other_addon_refund import applies as other_applies, facts as other_team_facts
            other_team = OrderAddonFundingReleaseRun.objects.filter(handover__payment=prior.payment,
                points_handover_snapshot__kind='SAME_TEAM_V1', handover__accepted_at__lt=prior.created_at).exists()
            try:
                prior_facts.append(first_refund_facts(prior, receipt_id=receipt_id, _memo=_memo) if other_team
                                   else other_team_facts(prior, receipt_id=receipt_id) if other_applies(prior)
                                   else other_refund_facts(prior, read_receipt_id=receipt_id, _event_history=False))
            except RestValidationError as exc:
                raise ValidationError('此前其他加购退款凭证异常') from exc
        # The accepted handover shares already incorporate every deduction
        # made before it. Verify those receipts, but never apply them twice.
        if before_handover:
            if prior.payment_id == payment.pk:
                for person in prior.proposal.participants.all():
                    for entry in person.share_plan:
                        key = (entry['segment_id'], person.account_id)
                        if key in state:
                            state[key]['rounding'] += entry['burden'] - entry['refund_basis']
            continue
        for person in prior.proposal.participants.all():
            if prior.payment_id == payment.pk:
                for entry in person.share_plan:
                    key = (entry['segment_id'], person.account_id)
                    if key not in state:
                        raise ValidationError('此前退款履约段异常')
                    state[key]['basis'] -= entry['refund_basis']
                    state[key]['rounding'] += entry['burden'] - entry['refund_basis']
            for deduction in person.earning_deductions.filter(payment=payment):
                key = (deduction.segment_id, person.account_id)
                if key not in state:
                    raise ValidationError('此前退款收益扣减异常')
                state[key]['income'] -= deduction.amount
        if any(min(row['basis'], row['income']) < 0 for row in state.values()):
            raise ValidationError('此前退款已超出履约段份额')
    participants = list(proposal.participants.select_related('provider_transaction__wallet').order_by('account_id'))
    if team_run:
        from .models import OrderPointsRefundSettlementPerson
        if OrderPointsRefundSettlementPerson.objects.filter(
                settlement__posting__points_refund__redemption__order=order,
                segment__provider_account_id__in=[person.account_id for person in participants],
                settled_at__lte=refund.created_at).exists():
            raise ValidationError('接替退款不能改变已结算陪玩的责任份额')
    decisions = list(proposal.decisions.order_by('account_id').values_list('account_id', 'role', 'accepted', 'decided_at'))
    expected = {(payment.intent.buyer_id, 'BOSS', True)} | {(person.account_id, 'PROVIDER', True) for person in participants}
    if (not participants or len(expected) != len(participants) + 1
            or set((account, role, accepted) for account, role, accepted, _ in decisions) != expected
            or any(not at for _, _, _, at in decisions) or not proposal.accepted_at
            or max(at for _, _, _, at in decisions) != proposal.accepted_at
            or proposal.accepted_at > refund.created_at):
        raise ValidationError('接替后退款确认凭证异常')
    # Rebuild the accepted split from the original handover shares, rather
    # than trusting persisted participant amounts or the current net shares.
    from .addon_refund import _allocate_refund_basis, _allocate_segment_burden
    selected = [person.account_id for person in participants]
    weights = {account: sum(row['basis'] for (_, owner), row in state.items() if owner == account)
               for account in selected}
    total = sum(weights.values())
    if not total or refund.amount > total or any(not basis for basis in weights.values()):
        raise ValidationError('接替后退款责任份额异常')
    refund_by_person = _allocate_refund_basis(refund.amount, sorted(weights.items()))
    for person in participants:
        rows = [(segment_id, row) for (segment_id, account), row in state.items()
                if account == person.account_id and row['basis']]
        allocations = _allocate_refund_basis(refund_by_person[person.account_id],
                                            [(segment_id, row['basis']) for segment_id, row in rows])
        burden = ((refund.amount * weights[person.account_id] + total * 10 - 1) // (total * 10)) * 10
        entries = [{'segment_id': segment_id, 'basis': row['basis'], 'income': row['income'],
                    'refund_basis': allocations[segment_id],
                    'burden_capacity': max(0, min(row['basis'], row['basis'] - row['rounding']))}
                   for segment_id, row in sorted(rows)]
        from .models import OrderParticipationSegment
        latest = list(OrderParticipationSegment.objects.filter(pk__in=[segment_id for segment_id, _ in rows])
                      .order_by('-started_at', '-accepted_at', '-pk').values_list('pk', flat=True))
        excess = _allocate_segment_burden(entries, burden, latest)
        if (person.basis != weights[person.account_id] or person.burden != burden
                or person.excess_burden != excess or person.share_plan != entries):
            raise ValidationError('接替后退款逐人分摊与确认方案不一致')
    totals = [0, 0, 0, 0]
    people = []
    for person in participants:
        entries = person.share_plan
        if not isinstance(entries, list) or not entries:
            raise ValidationError('退款受影响履约段缺失')
        segment_ids = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValidationError('退款履约份额异常')
            segment_id = entry.get('segment_id')
            row = state.get((segment_id, person.account_id))
            if (not row or segment_id in segment_ids or entry.get('basis') != row['basis']
                    or entry.get('income') != row['income'] or type(entry.get('refund_basis')) is not int
                    or type(entry.get('burden')) is not int or entry['refund_basis'] < 0
                    or entry['burden'] < 0):
                raise ValidationError('退款与接替原始份额不一致')
            segment_ids.add(segment_id)
        amounts = [person.addon_pending_deduction, person.main_pending_deduction,
                   person.wallet_deduction, person.deposit_deduction]
        if any(type(value) is not int or value < 0 for value in amounts) or sum(amounts) != person.burden:
            raise ValidationError('退款逐人承担不守恒')
        for index, value in enumerate(amounts):
            totals[index] += value
        tx = person.provider_transaction
        if person.wallet_deduction:
            if (not tx or tx.wallet.account_id != person.account_id or tx.tx_type != 'REFUND'
                    or tx.status != 'SUCCESS' or tx.amount != -person.wallet_deduction
                    or tx.balance_after != tx.balance_before + tx.amount or tx.reversal_of_id
                    or tx.reversals.exists() or tx.operator_account_id != refund.operator_id
                    or tx.operator_id != refund.legacy_operator_id):
                raise ValidationError('退款逐人钱包流水异常')
        elif tx:
            raise ValidationError('退款存在多余钱包流水')
        addon_deductions = list(person.earning_deductions.select_related('payment__intent',
            'segment__attribution').order_by('pk'))
        main_deductions = list(person.main_segment_deductions.select_related('segment__attribution').order_by('pk'))
        if (sum(row.amount for row in addon_deductions) != person.addon_pending_deduction
                or sum(row.amount for row in main_deductions) != person.main_pending_deduction
                or any(row.refund_id != refund.pk or row.payment.intent.order_id != order.pk
                       or row.segment.attribution.order_id != order.pk
                       or row.segment.provider_account_id != person.account_id for row in addon_deductions)
                or any(row.segment.attribution.order_id != order.pk
                       or row.segment.provider_account_id != person.account_id for row in main_deductions)):
            raise ValidationError('退款逐段待结收益扣减异常')
        if len({(row.payment_id, row.segment_id) for row in addon_deductions}) != len(addon_deductions):
            raise ValidationError('退款加购履约段扣减重复')
        for deduction in addon_deductions:
            if deduction.payment_id == payment.pk:
                available = state.get((deduction.segment_id, person.account_id), {}).get('income', -1)
            else:
                other = deduction.payment
                from .addon_payment import verify_receipt
                try:
                    verify_receipt(other)
                except RestValidationError as exc:
                    raise ValidationError('跨加购付款凭证异常') from exc
                other_team = OrderAddonFundingReleaseRun.objects.filter(handover__payment=other,
                    points_handover_snapshot__kind='SAME_TEAM_V1',
                    handover__accepted_at__lt=refund.created_at).order_by('-handover__accepted_at', '-pk').first()
                followup = OrderAddonFundingReleaseRun.objects.filter(handover__payment=other,
                    handover__status='ACCEPTED', handover__accepted_at__lt=refund.created_at,
                    points_followup_snapshot__kind='TRANSFER_FOLLOWUP_V1').select_related(
                        'handover__payment__intent').order_by('-handover__accepted_at', '-handover_id').first()
                voluntary = OrderAddonFundingReleaseRun.objects.filter(handover__payment=other,
                    handover__status='ACCEPTED', handover__accepted_at__lt=refund.created_at,
                    points_handover_snapshot__kind='POINTS_VOLUNTARY_HANDOVER_V1').select_related(
                        'handover__payment__intent').order_by('-handover__accepted_at', '-handover_id').first()
                latest = other.handovers.filter(status='ACCEPTED', accepted_at__lt=refund.created_at)\
                    .order_by('-accepted_at', '-pk').first()
                if voluntary and latest and voluntary.handover_id == latest.pk:
                    if (voluntary.created_at < latest.accepted_at or voluntary.created_at >= refund.created_at
                            or other.handovers.filter(status='ACCEPTED', accepted_at=refund.created_at).exists()):
                        raise ValidationError('跨加购自愿接替资金时序异常')
                    from .points_voluntary_handover_event import facts as voluntary_facts
                    from .addon_funding_release import verify_run
                    try:
                        voluntary_facts(voluntary, receipt_id=receipt_id)
                        verify_run(voluntary, _skip_usage=True, witness_receipt_id=receipt_id)
                    except RestValidationError as exc:
                        raise ValidationError('跨加购自愿接替来源异常') from exc
                    matching = list(latest.shares.filter(segment_id=deduction.segment_id,
                        segment__provider_account_id=person.account_id))
                    if not matching:
                        raise ValidationError('跨加购自愿接替收益不属于该陪玩履约段')
                    used = sum(other.refund_earning_deductions.filter(segment_id=deduction.segment_id,
                        participant__account_id=person.account_id, refund__created_at__gt=latest.accepted_at,
                        refund__created_at__lt=refund.created_at).values_list('amount', flat=True))
                    available = sum(share.income for share in matching) - used
                elif followup and (not other_team or
                                 followup.handover.accepted_at > other_team.handover.accepted_at):
                    # Read the latest actual responsibility and its committed funding
                    # event at this refund boundary; an earlier share cannot fund a
                    # deduction after a later handover.
                    latest = other.handovers.filter(status='ACCEPTED', accepted_at__lt=refund.created_at)\
                        .order_by('-accepted_at', '-pk').first()
                    if (not latest or latest.pk != followup.handover_id
                            or followup.created_at < latest.accepted_at
                            or followup.created_at >= refund.created_at
                            or other.handovers.filter(status='ACCEPTED', accepted_at=refund.created_at).exists()):
                        raise ValidationError('跨加购接替资金版本或时间异常')
                    from .points_transfer_followup_handover_scope import verify_event
                    from .points_transfer_followup_addon_version import _verify_prior_run
                    try:
                        matching = [row for row in verify_event(other, latest, before=refund.created_at,
                                    receipt_id=receipt_id) if row.segment_id == deduction.segment_id
                                    and row.segment.provider_account_id == person.account_id]
                        _verify_prior_run(followup, receipt_id=receipt_id)
                    except RestValidationError as exc:
                        raise ValidationError('跨加购接替及资金凭证异常') from exc
                    if not matching:
                        raise ValidationError('跨加购接替收益不属于该陪玩履约段')
                    used = sum(other.refund_earning_deductions.filter(segment_id=deduction.segment_id,
                        participant__account_id=person.account_id,
                        refund__created_at__gt=latest.accepted_at,
                        refund__created_at__lt=refund.created_at).values_list('amount', flat=True))
                    available = sum(row.income for row in matching) - used
                elif other_team:
                    if (not latest or latest.pk != other_team.handover_id
                            or other.handovers.filter(status='ACCEPTED', accepted_at=refund.created_at).exists()):
                        raise ValidationError('跨加购接替责任及时间待核对')
                    from .points_team_postmain_handover_facts import verify_event_current
                    verify_event_current(other_team, receipt_id=receipt_id)
                    matching = list(other_team.handover.shares.filter(segment_id=deduction.segment_id,
                        segment__provider_account_id=person.account_id))
                    if not matching:
                        raise ValidationError('跨加购接替收益不属于该陪玩履约段')
                    used = sum(other.refund_earning_deductions.filter(segment_id=deduction.segment_id,
                        participant__account_id=person.account_id,
                        refund__created_at__gt=other_team.handover.accepted_at,
                        refund__created_at__lt=refund.created_at).values_list('amount', flat=True))
                    available = sum(row.income for row in matching) - used
                elif (chain := _same_seat_cross_handovers(refund, handovers[-1], latest)):
                    if len(chain) > 2:
                        _verify_same_seat_debits(refund, chain, person, state, addon_deductions, receipt_id=receipt_id)
                        break
                    available = _same_seat_cross_income(refund, latest, deduction.segment_id,
                                                        person.account_id, receipt_id=receipt_id)
                    own_income = state.get((deduction.segment_id, person.account_id), {}).get('income')
                    own_used = sum(row.amount for row in addon_deductions if row.payment_id == payment.pk
                                   and row.segment_id == deduction.segment_id)
                    if (own_used != own_income or (any(amounts[1:]) and deduction.amount != available)):
                        raise ValidationError('跨加购待结收益未按原付款顺序扣减')
                else:
                    if (other.handovers.filter(status='ACCEPTED', accepted_at__lt=refund.created_at).exists()
                            or other.intent.segment_id != deduction.segment_id
                            or other.intent.provider_id != person.account_id
                            or other.buyer_transaction.created_at >= refund.created_at):
                        raise ValidationError('跨加购收益履约段尚未接入')
                    used = sum(other.refund_earning_deductions.filter(
                        refund__created_at__lt=refund.created_at).values_list('amount', flat=True))
                    available = other.provider_income - used
            if deduction.amount > available:
                raise ValidationError('退款占用的加购段收益不足')
        deficits = list(refund.deposit_deficits.filter(account_id=person.account_id)
                        .select_related('profile', 'source_store_transaction__wallet__user').order_by('pk'))
        if (sum(item.amount for item in deficits) != person.deposit_deduction
                or any(item.profile.account_id != person.account_id or item.addon_refund_id != refund.pk
                       or not item.source_store_transaction_id for item in deficits)):
            raise ValidationError('退款逐人押金凭证异常')
        from django.conf import settings
        for item in deficits:
            source = item.source_store_transaction
            if (item.source_kind != 'DIRECT' or item.direct_key != f'addon:{refund.pk}:{item.profile_id}'
                    or source.order_id != order.pk or source.tx_type != 'DEPOSIT_INCOME'
                    or source.status != 'SUCCESS' or source.amount != -item.amount
                    or not source.wallet.user_id or source.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
                    or source.balance_after != source.balance_before + source.amount
                    or source.operator_account_id != refund.operator_id or source.operator_id != refund.legacy_operator_id
                    or source.reversal_of_id or source.reversals.exists()):
                raise ValidationError('退款逐人押金真实流水异常')
        if team_run and order.refund_records.filter(created_at__lt=refund.created_at).count() == 1:
            from .points_team_refund_priority import verify as verify_priority
            verify_priority(refund, person, receipt_id=receipt_id)
        people.append([person.pk, person.account_id, person.basis, person.burden, entries, amounts,
                       tx.pk if tx else None, [[item.pk, item.amount, item.source_store_transaction_id] for item in deficits],
                       [[row.pk, row.payment_id, row.segment_id, row.amount] for row in addon_deductions],
                       [[row.pk, row.segment_id, row.amount] for row in main_deductions]])
    if ((len(participants) == 1 and refund.provider_transaction_id != participants[0].provider_transaction_id)
            or (len(participants) > 1 and refund.provider_transaction_id)):
        raise ValidationError('退款与陪玩钱包流水关联异常')
    if (totals != [refund.addon_pending_deduction, refund.main_pending_deduction,
                   refund.wallet_deduction, refund.deposit_deduction]
            or sum(refund.deposit_deficits.values_list('amount', flat=True)) != refund.deposit_deduction
            or sum(person.burden for person in participants) != refund.amount + refund.rounding_surplus
            or sum(entry['refund_basis'] for person in participants for entry in person.share_plan) != refund.amount):
        raise ValidationError('退款逐人金额汇总异常')
    result = {'handover_id': handovers[-1].pk, 'refund_id': refund.pk,
            'original_shares': [[row.pk, row.segment_id, row.segment.provider_account_id, row.basis,
                                 row.income, row.commission_snapshot] for row in shares],
            'decisions': [[account, role, accepted, at.isoformat()] for account, role, accepted, at in decisions],
            'participants': people}
    if proposal.cross_pending_plan is not None:
        result['cross_pending_plan'] = proposal.cross_pending_plan
    if prior_facts:
        result['prior_refunds'] = prior_facts
    _memo[refund.pk] = result
    return result


def post_followup_facts(refund, *, receipt_id=None):
    """Prove an actual same-team add-on refund after both main postings."""
    from .models import PointsNegotiationPosting, OrderAddonFundingReleaseRun
    from .points_negotiation_execution import verify_participants
    from .addon_main_segment_deduction import verified_deductions
    order, payment, proposal = refund.order, refund.payment, refund.proposal
    runs = list(OrderAddonFundingReleaseRun.objects.filter(handover__payment=payment,
        points_handover_snapshot__kind='SAME_TEAM_V1', handover__accepted_at__lt=refund.created_at)
        .select_related('handover').order_by('handover__accepted_at', 'pk'))
    from .points_team_addon_handover_facts import has_ordered_funding_chain
    mains = list(PointsNegotiationPosting.objects.filter(
        points_refund__redemption__order=order, created_at__lt=refund.created_at).order_by('created_at', 'pk'))
    if (not runs or not has_ordered_funding_chain(runs) or len(mains) != 2 or mains[0].created_at >= mains[1].created_at
            or mains[1].proposal.points_terms.prior_refund_id != mains[0].points_refund_id
            or order.refund_records.filter(created_at=refund.created_at).exists()):
        raise ValidationError('加购退款前的两次主单退款时序待核对')
    people = first_refund_facts(refund, receipt_id=receipt_id)
    try:
        for posting in mains:
            verify_participants(posting)
        owners = {}
        for posting in mains:
            for share in posting.shares.all():
                matches = [row for row in posting.participants if row.get('account_id') == share.account_id]
                if len(matches) != 1 or share.account_id in owners:
                    raise ValidationError('两次退款的陪玩收益归属异常')
                owners[share.account_id] = (posting, share, matches[0])
        used = verified_deductions(order, before=refund.created_at)
        prior = {posting.pk: verified_deductions(order, before=posting.created_at) for posting in mains}
        for person in proposal.participants.all():
            owner = owners.get(person.account_id)
            if not owner:
                raise ValidationError('加购退款责任人缺少主单退款份额')
            posting, share, entry = owner
            for row in person.main_segment_deductions.all():
                previous = used.get(row.segment_id, 0) - prior[posting.pk].get(row.segment_id, 0)
                if (row.segment_id != entry['segment_id'] or previous < 0
                        or row.amount + previous > share.retained_income):
                    raise ValidationError('两次退款后主单履约段收益不足')
    except RestValidationError as exc:
        raise ValidationError('两次主单退款后的个人收益凭证异常') from exc
    allocations = {row.pk: row for row in payment.allocations.all()}
    source_plan = proposal.source_plan
    if (not isinstance(source_plan, list) or not source_plan
            or any(not isinstance(row, dict) or type(row.get('allocation_id')) is not int
                   or type(row.get('amount_raw')) is not int or row['amount_raw'] <= 0
                   or row['allocation_id'] not in allocations
                   or allocations[row['allocation_id']].kind != row.get('kind') for row in source_plan)
            or len({row['allocation_id'] for row in source_plan}) != len(source_plan)
            or sum(row['amount_raw'] for row in source_plan) != refund.amount):
        raise ValidationError('两次主单退款后的加购原付款来源异常')
    tx = refund.buyer_transaction
    if (not tx or tx.wallet.account_id != order.customer_account_id or tx.tx_type != 'REFUND'
            or tx.status != 'SUCCESS' or tx.amount != refund.amount
            or tx.balance_after != tx.balance_before + tx.amount or tx.reversal_of_id or tx.reversals.exists()
            or tx.operator_account_id != refund.operator_id or tx.operator_id != refund.legacy_operator_id):
        raise ValidationError('两次主单退款后的加购老板流水异常')
    return {'refund_id': refund.pk, 'main_posting_ids': [row.pk for row in mains],
            'handover_refund': people, 'source_plan': source_plan,
            'buyer_transaction': [tx.pk, tx.wallet_id, tx.amount, tx.balance_before, tx.balance_after,
                                  tx.operator_id, tx.operator_account_id]}


def funding_facts(refund, *, receipt_id):
    """Rebuild a post-handover refund at its own event time."""
    from users.receipt_usage_sources import _require_current_unlocked_reads, _addon_release_facts
    from dataclasses import asdict
    from .models import (OrderOriginalShareTransfer, OrderPointsFundingAllocation,
                         OrderAddonFundingRelease)
    from .points_addon_receipt_usage import payment_facts
    from .points_transfer_refund_evidence import _facts_current_read, _facts_without_receipt_locked
    from .points_transfer_receipt_usage import historical_plan
    from .addon_funding_history import combined_at
    from .points_funding_plan import PointsFundingPlanError
    from rest_framework.exceptions import ValidationError as DRFValidationError
    _require_current_unlocked_reads()
    order, payment, proposal = refund.order, refund.payment, refund.proposal
    if receipt_id is None:
        witness = OrderPointsFundingAllocation.objects.filter(redemption__order=order).order_by('pk').first()
        if witness:
            # The order-money entry prelocks its receipts.  A receipt allocated
            # after the purchase is a read witness, not an earlier allocation.
            from users.receipt_usage_ledger import _locked_receipt
            _locked_receipt(witness.receipt_id)
            receipt_id = witness.receipt_id
    people = first_refund_facts(refund, receipt_id=receipt_id)
    from .models import OrderAddonFundingReleaseRun, OrderPointsTransferFollowupFundingRevision
    payment_run = OrderAddonFundingReleaseRun.objects.filter(payment=payment,
        points_followup_snapshot__kind='TRANSFER_FOLLOWUP_V1').first()
    if payment_run:
        snapshot = payment_run.points_followup_snapshot
        revision = OrderPointsTransferFollowupFundingRevision.objects.filter(
            pk=snapshot.get('followup_revision_id'), posting__points_refund__redemption__order=order).first()
        if (not revision or snapshot.get('addon_payment_id') != payment.pk
                or payment.buyer_transaction.created_at <= revision.posting.created_at):
            raise ValidationError('加购付款与第二次主单退款资金版本不一致')
        from .points_transfer_followup_addon_payment import facts as followup_payment_facts
        from .points_transfer_followup_addon_version import _verify_prior_run
        _verify_prior_run(payment_run, receipt_id=receipt_id)
        payment_snapshot = followup_payment_facts(payment, revision, receipt_id=receipt_id)
        if snapshot.get('addon_payment_facts') != payment_snapshot:
            raise ValidationError('加购付款资金事实不一致')
    else:
        payment_snapshot = payment_facts(payment, read_receipt_id=receipt_id)
    transfers = list(OrderOriginalShareTransfer.objects.filter(
        previous_segment__attribution__order=order, created_at__lt=refund.created_at).order_by('pk'))
    team = not transfers
    if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
                                                  created_at=refund.created_at).exists():
        raise ValidationError('接替后退款的主单时序尚未接入')
    sources = list(order.funding_allocations.order_by('pk'))
    paid = {kind: sum(row.amount for row in sources if row.kind == kind)
            for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
    if team:
        from .points_funding_plan import PointsFundingPlan
        rows = payment_snapshot.get('main_shares')
        if not isinstance(rows, list) or len(rows) != 2:
            raise ValidationError('同单接替退款的主单来源尚未接入')
        try:
            main = PointsFundingPlan(**payment_snapshot['main_plan'])
            lines = [(item[1], item[2], item[3], item[6], item[7]) for item in rows]
        except (KeyError, TypeError, IndexError) as exc:
            raise ValidationError('同单接替退款的主单份额待核对') from exc
    else:
        history = (_facts_current_read(order, receipt_id) if receipt_id
                   else _facts_without_receipt_locked(order))
        main, lines = historical_plan(history, order.points_redemption, paid, transfers[-1].pk)
    available_main = {segment_id: income for segment_id, _, _, _, income in lines}
    main_refunds = list(order.refund_records.filter(created_at__lte=refund.created_at).order_by('created_at', 'pk'))
    posting = None
    if main_refunds:
        from .models import PointsNegotiationPosting
        if (len(main_refunds) not in ((1,) if team else (1, 2))
                or any(item.created_at == refund.created_at for item in main_refunds)):
            raise ValidationError('加购退款与主单退款时序待核对')
        posting = PointsNegotiationPosting.objects.filter(points_refund__refund=main_refunds[0]).first()
        if not posting:
            raise ValidationError('主单退款确认凭证缺失')
        if team:
            if not isinstance(posting.participants, list) or len(posting.participants) != 2:
                raise ValidationError('同单主单退款履约凭证缺失')
            # The non-transfer posting records one immutable segment per account.
            # Its retained income already includes earlier add-on deductions.
            from .points_negotiation_execution import verify_participants
            try:
                verify_participants(posting)
            except DRFValidationError as exc:
                raise ValidationError('同单主单退款履约凭证异常') from exc
            participants = {row['segment_id']: row for row in posting.participants}
            if set(participants) != set(available_main):
                raise ValidationError('同单主单退款履约段不一致')
            for segment_id, row in participants.items():
                share = posting.shares.filter(account_id=row['account_id']).first()
                available_main[segment_id] = (share.retained_income if share else
                    row['income_at_entry'] - row.get('prior_addon_deduction', 0))
            if any(value < 0 for value in available_main.values()):
                raise ValidationError('同单主单退款剩余收益异常')
        else:
            postings = [posting]
            if len(main_refunds) == 2:
                followup = PointsNegotiationPosting.objects.filter(points_refund__refund=main_refunds[1]).first()
                terms = getattr(followup.proposal, 'points_terms', None) if followup else None
                if not followup or not terms or terms.prior_refund_id != posting.points_refund_id:
                    raise ValidationError('转单两次主单退款的资金前驱异常')
                postings.append(followup)
            for main_posting in postings:
                selected = set(main_posting.proposal.affected_segment_ids or [])
                if not isinstance(main_posting.participants, dict):
                    raise ValidationError('主单退款履约事实异常')
                segments = {row['id']: row for row in main_posting.participants['facts']['segments']}
                closed = (selected if selected else {item['id'] for item in segments.values()
                          if item.get('started_at') and item.get('account_id') in main_posting.proposal.affected_accounts})
                for segment_id in closed:
                    available_main[segment_id] = 0
                for person in main_posting.shares.all():
                    chosen = [line[0] for line in lines if line[1] == person.account_id
                              and segments.get(line[0], {}).get('started_at') and (not selected or line[0] in selected)]
                    if person.pending_deduction and len(chosen) != 1:
                        raise ValidationError('主单退款占用须明确本人履约段')
                    if person.pending_deduction and chosen[0] not in closed:
                        available_main[chosen[0]] -= person.pending_deduction
    from .addon_main_segment_deduction import verified_deductions
    try:
        used_main = verified_deductions(order, before=refund.created_at)
        if team and posting:
            # Posting retained income already accounts for deductions made before it.
            earlier_main = verified_deductions(order, before=posting.created_at)
            used_main = {key: value - earlier_main.get(key, 0) for key, value in used_main.items()}
            if any(value < 0 for value in used_main.values()):
                raise ValidationError('主单退款前后收益扣减时序异常')
    except DRFValidationError as exc:
        raise ValidationError('此前主单段收益占用异常') from exc
    for person in proposal.participants.all():
        for row in person.main_segment_deductions.all():
            if row.amount + used_main.get(row.segment_id, 0) > available_main.get(row.segment_id, 0):
                raise ValidationError('退款占用的主单段收益不足')
    if posting:
        from .models import OrderAddonFundingReleaseRun
        from .refunded_addon_revision import capture
        from django.utils.dateparse import parse_datetime
        run = OrderAddonFundingReleaseRun.objects.filter(refund=refund).first()
        transferred_followup = bool(not team and len(main_refunds) == 2)
        snapshot = (run.points_followup_snapshot if transferred_followup else run.refunded_main_snapshot) if run else None
        at = parse_datetime(snapshot.get('captured_at', '')) if isinstance(snapshot, dict) else None
        if not run or not at or at < refund.created_at or not isinstance(run.allocation_snapshot, list):
            raise ValidationError('主单退款后的加购资金版本缺失')
        allocation_map = {row.pk: row for row in OrderPointsFundingAllocation.objects.filter(
            pk__in=[item[0] for item in run.allocation_snapshot])}
        net = []
        for item in run.allocation_snapshot:
            if len(item) not in (5, 6) or item[0] not in allocation_map:
                raise ValidationError('主单退款后的到账分配快照异常')
            used = item[2] + sum(row[1] for group in item[3:6] for row in group)
            if used > item[1]:
                raise ValidationError('主单退款后的到账释放超额')
            net.append([item[0], item[1] - used, allocation_map[item[0]].receipt_id])
        try:
            if transferred_followup:
                from .models import OrderPointsTransferFollowupFundingRevision
                from .points_transfer_followup_addon_version import capture as capture_followup
                version = OrderPointsTransferFollowupFundingRevision.objects.filter(
                    pk=snapshot.get('followup_revision_id'), posting=postings[-1]).first()
                if not version:
                    raise ValidationError('转单后续退款资金版本缺失')
                rebuilt = capture_followup(order, version, net, at, receipt_id=receipt_id)
            elif receipt_id and not team:
                from .refunded_addon_revision import _capture_transferred
                from django.db.models import Q
                witness = OrderPointsFundingAllocation.objects.filter(
                    Q(redemption__order=order) | Q(service_order=order), receipt_id=receipt_id).first()
                if not witness:
                    raise ValidationError('主单退款后的到账见证缺失')
                rebuilt = _capture_transferred(order, posting, net, at, receipt_local=True,
                                                witness_allocation=witness)
            else:
                rebuilt = capture(order, posting, net, at)
        except DRFValidationError as exc:
            raise ValidationError('主单退款后的加购资金重建异常') from exc
        if rebuilt != snapshot:
            raise ValidationError('主单退款后的加购资金快照不一致')
        combined = {'additional_store_funding_units': rebuilt['funding']['required_units'],
                    'refunded_main': rebuilt}
    else:
        try:
            combined = combined_at(order, main, refund.created_at, segment_shares=lines,
                                   include_refund_id=refund.pk)
        except (PointsFundingPlanError, DRFValidationError) as exc:
            raise ValidationError('接替后退款整单资金待核对') from exc
    source_plan = proposal.source_plan
    sources_by_id = {row.pk: row for row in payment.allocations.order_by('pk')}
    if (not isinstance(source_plan, list) or not source_plan
            or any(not isinstance(row, dict) or type(row.get('allocation_id')) is not int
                   or type(row.get('amount_raw')) is not int or row['amount_raw'] <= 0
                   or row['allocation_id'] not in sources_by_id
                   or sources_by_id[row['allocation_id']].kind != row.get('kind') for row in source_plan)
            or len({row['allocation_id'] for row in source_plan}) != len(source_plan)
            or sum(row['amount_raw'] for row in source_plan) != refund.amount):
        raise ValidationError('接替后退款原付款来源异常')
    tx = refund.buyer_transaction
    if (not tx or tx.wallet.account_id != order.customer_account_id or tx.tx_type != 'REFUND'
            or tx.status != 'SUCCESS' or tx.amount != refund.amount
            or tx.balance_after != tx.balance_before + tx.amount or tx.reversal_of_id or tx.reversals.exists()
            or tx.operator_account_id != refund.operator_id or tx.operator_id != refund.legacy_operator_id):
        raise ValidationError('接替后退款老板流水异常')
    if team:
        from django.db.models import Q
        allocations = OrderPointsFundingAllocation.objects.filter(
            Q(redemption__order=order) | Q(service_order=order), created_at__lt=refund.created_at).order_by('pk')
    else:
        allocations = OrderPointsFundingAllocation.objects.filter(redemption__order=order,
            created_at__lt=refund.created_at).order_by('pk')
    prior = list(OrderAddonFundingRelease.objects.filter(allocation__in=allocations,
        run__created_at__lt=refund.created_at).select_related('run', 'allocation').order_by('pk'))
    for row in prior:
        _addon_release_facts(row, witness_receipt_id=receipt_id)
    return {'version': 1, 'order_id': order.pk, 'refund_id': refund.pk,
            'payment_id': payment.pk, 'main_plan': asdict(main),
            'required': combined['additional_store_funding_units'],
            'refund_at': refund.created_at.isoformat(), 'payment': payment_snapshot,
            'handover_refund': people, 'transfer_id': transfers[-1].pk if transfers else None,
            'segment_shares': [list(row) for row in lines], 'funding': combined,
            'source_plan': source_plan, 'buyer_transaction': [tx.pk, tx.wallet_id, tx.amount,
                tx.balance_before, tx.balance_after, tx.operator_id, tx.operator_account_id],
            'prior_releases': [[row.pk, row.allocation_id, row.run_id, row.amount] for row in prior]}


def _verify_prior_handover_refund_recovery(refund, first, run):
    """Read the original wallet and deposit debits without locking another order."""
    from .ordinary_refund_receipt_evidence import _verify_direct_deposit

    segment = first.target_segment
    account_id = segment.provider_account_id
    user_id = refund.order.providers.filter(provider_account_id=account_id).values_list('provider_id', flat=True).first()
    debit = refund.provider_transaction
    if not user_id or (refund.wallet_deduction and (not debit or debit.wallet.user_id != user_id
            or debit.order_id is not None or not first.accepted_at < debit.created_at <= refund.created_at)):
        raise ValidationError('再次加购接替的原退款钱包流水异常')
    if not refund.deposit_deduction:
        return
    cycle = segment.provider_business_cycle
    deficits = list(refund.deposit_deficits.select_related('profile', 'source_store_transaction__wallet__user'))
    if (not cycle or len(deficits) != 1 or deficits[0].account_business_cycle != cycle
            or not refund.created_at <= deficits[0].source_store_transaction.created_at
                                    <= deficits[0].created_at <= run.created_at):
        raise ValidationError('再次加购接替的原退款押金周期或时序异常')
    try:
        _verify_direct_deposit(deficits[0], refund=refund, account_id=account_id, user_id=user_id,
                               amount=refund.deposit_deduction, addon=True)
    except RestValidationError as exc:
        raise ValidationError('再次加购接替的原退款押金凭证异常') from exc
    if debit:
        if debit.balance_after != 0:
            raise ValidationError('再次加购接替的原退款钱包尚有可用余额')
    else:
        zero = getattr(refund, 'zero_wallet_evidence', None)
        if (not zero or zero.account_id != account_id or zero.wallet.account_id != account_id
                or zero.wallet.user_id != user_id or zero.business_cycle != cycle or zero.balance != 0
                or not zero.is_active
                or not refund.created_at <= zero.created_at <= deficits[0].source_store_transaction.created_at):
            raise ValidationError('再次加购接替缺少原退款零余额钱包凭证')


def verify_before_second_handover(refund, first, *, before, receipt_id=None):
    """Prove a refund against the first owner's original fulfillment segment."""
    from .addon_funding_release import verify_run
    from .models import OrderAddonFundingReleaseRun, OrderAddonHandover

    if (refund.payment_id != first.payment_id or refund.order_id != first.payment.intent.order_id
            or first.previous_id or first.kind != 'STANDARD' or not first.accepted_at
            or not first.accepted_at < refund.created_at < before
            or not 0 < refund.amount < first.transferred_basis):
        raise ValidationError('再次加购接替的前序退款范围异常')
    run = OrderAddonFundingReleaseRun.objects.filter(refund=refund,
        created_at__gt=refund.created_at, created_at__lt=before).first()
    if not run or not run.points_addon_snapshot or not run.points_addon_hash:
        raise ValidationError('再次加购接替缺少原退款补资凭证')
    try:
        verify_run(run, _skip_usage=True, witness_receipt_id=receipt_id)
    except RestValidationError as exc:
        raise ValidationError('再次加购接替的原退款凭证待核对') from exc
    proof = run.points_addon_snapshot.get('handover_refund') or {}
    members = proof.get('participants', [])
    if (proof.get('handover_id') != first.pk or len(members) != 1
            or members[0][1] != first.target_segment.provider_account_id
            or len(members[0][4]) != 1 or members[0][4][0]['segment_id'] != first.target_segment_id
            or any(row[2] != first.target_segment_id for row in members[0][8])
            or any(row[1] != first.target_segment_id for row in members[0][9])):
        raise ValidationError('再次加购接替的原退款未归属同一履约段')
    other_payments = {row[1] for row in members[0][8] if row[1] != refund.payment_id}
    if other_payments:
        other = OrderAddonHandover.objects.filter(payment_id=min(other_payments), status='ACCEPTED',
            accepted_at__lt=refund.created_at).order_by('-accepted_at', '-pk').first()
        chain = _same_seat_cross_handovers(refund, first, other)
        if not other_payments.issubset(row.payment_id for row in chain):
            raise ValidationError('再次加购接替的跨付款扣减不属于同一席')
    if refund.wallet_deduction or refund.deposit_deduction:
        _verify_prior_handover_refund_recovery(refund, first, run)
    return [refund.pk, run.pk, run.points_addon_hash]


def verify_refunds_before_second_handover(refunds, first, *, before, receipt_id=None):
    """Prove the ordered refund history before liability passes to the next segment."""
    if sum(refund.amount for refund in refunds) >= first.transferred_basis:
        raise ValidationError('再次加购接替前的累计退款未保留可接替份额')
    boundaries = [refund.created_at for refund in refunds[1:]] + [before]
    return [verify_before_second_handover(refund, first, before=boundary, receipt_id=receipt_id)
            for refund, boundary in zip(refunds, boundaries)]


def settlement_parts(refund, *, payment=None, before=None, receipt_id=None):
    """Verify segment shares at a boundary, or against current obligations."""
    from .addon_handover import current_responsibilities
    from .models import OrderAddonRefund, OrderPointsFundingAllocation, OrderAddonFundingReleaseRun
    payment = payment or refund.payment
    if payment.intent.order_id != refund.order_id:
        raise ValidationError('退款与加购结算不属于同一订单')
    query = OrderAddonRefund.objects.filter(order=refund.order)
    if before is not None:
        if query.filter(created_at=before).exists():
            raise ValidationError('加购退款与结算边界时间无法区分')
        query = query.filter(created_at__lt=before)
    refunds = list(query.select_related('payment', 'proposal', 'order').order_by('created_at', 'pk'))
    handover_query = payment.handovers.filter(status='ACCEPTED')
    if before is not None:
        if handover_query.filter(accepted_at=before).exists():
            raise ValidationError('接替与结算边界时间无法区分')
        handover_query = handover_query.filter(accepted_at__lt=before)
    handovers = list(handover_query.order_by('accepted_at', 'pk'))
    if not handovers or not handovers[-1].accepted_at:
        raise ValidationError('接替加购履约版本待核对')
    boundary = handovers[-1].accepted_at
    own = [item for item in refunds if item.payment_id == payment.pk and item.created_at > boundary]
    from django.db.models import Q
    if receipt_id is None:
        receipt_id = OrderPointsFundingAllocation.objects.filter(
            Q(redemption__order=refund.order) | Q(service_order=refund.order)).order_by('pk')\
            .values_list('receipt_id', flat=True).first()
    memo = {}
    if own:
        facts = first_refund_facts(own[0], receipt_id=receipt_id, _memo=memo)
        original = _sum_segments((row[1], row[2], row[3], row[4]) for row in facts['original_shares'])
    else:
        from .addon_handover import verify_handover
        try:
            from .models import OrderAddonFundingReleaseRun
            team = OrderAddonFundingReleaseRun.objects.filter(handover=handovers[-1],
                points_handover_snapshot__kind='SAME_TEAM_V1').first()
            if team:
                from .addon_funding_release import verify_run
                verify_run(team, _skip_usage=True, witness_receipt_id=receipt_id)
                shares = list(handovers[-1].shares.select_related('segment').order_by('position'))
            else:
                shares = verify_handover(payment, handovers[-1], as_of=before, receipt_id=receipt_id)
        except RestValidationError as exc:
            raise ValidationError('接替加购履约版本待核对') from exc
        original = _sum_segments((row.segment_id, row.segment.provider_account_id, row.basis, row.income)
                                 for row in shares)
    for item in refunds:
        if item.created_at == boundary:
            raise ValidationError('接替与退款时序无法区分')
        if item.payment_id == payment.pk and item.created_at > boundary:
            first_refund_facts(item, receipt_id=receipt_id, _memo=memo)
        else:
            from .points_addon_receipt_usage import facts as other_refund_facts
            from .points_team_other_addon_refund import applies as other_applies, facts as other_team_facts
            other_team = OrderAddonFundingReleaseRun.objects.filter(handover__payment=item.payment,
                points_handover_snapshot__kind='SAME_TEAM_V1', handover__accepted_at__lt=item.created_at).exists()
            if other_team:
                first_refund_facts(item, receipt_id=receipt_id, _memo=memo)
            elif other_applies(item):
                other_team_facts(item, receipt_id=receipt_id)
            else:
                other_refund_facts(item, read_receipt_id=receipt_id, _event_history=False)
        if item.created_at < boundary:
            continue
        for person in item.proposal.participants.all():
            if item.payment_id == payment.pk:
                for entry in person.share_plan:
                    key = (entry['segment_id'], person.account_id)
                    original[key]['basis'] -= entry['refund_basis']
            for deduction in person.earning_deductions.filter(payment=payment):
                key = (deduction.segment_id, person.account_id)
                if key not in original:
                    raise ValidationError('跨加购退款扣减不属于该付款的实际履约段')
                original[key]['income'] -= deduction.amount
    original = {key: value for key, value in original.items() if value['basis'] or value['income']}
    if any(min(value['basis'], value['income']) < 0 or value['income'] > value['basis']
           for value in original.values()):
        raise ValidationError('接替退款后逐段净收益不一致')
    if before is None:
        _, current = current_responsibilities(payment)
        actual = _sum_segments((row['segment_id'], row['provider_id'], row['basis'], row['income']) for row in current)
        if (set(actual) != set(original)
                or any((actual[key]['basis'], actual[key]['income']) != (value['basis'], value['income'])
                       for key, value in original.items())):
            raise ValidationError('接替退款后逐段净收益不一致')
    return [{'segment_id': segment_id, 'provider_account_id': account_id,
             'basis': value['basis'], 'provider_income_units': value['income']}
            for (segment_id, account_id), value in sorted(original.items())]
