"""Receipt-local proof of sequential add-on payments after a third refund."""
import hashlib
import json
from dataclasses import asdict, replace
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from stores.db import transaction


def _verify(run, *, witness_receipt_id=None, order_locked=False):
    from .models import (PointsThirdAddonFundingRevision, OrderAddonPayment, OrderAddonRefund,
        OrderAddonHandover, OrderAddonFundingRelease,
        OrderPointsFundingAllocation, OrderPointsFundingRelease, OrderPointsTransferFundingRelease,
        PointsPostRefundTransferRelease, PointsFollowupTransferRelease, PointsThirdTransferRelease)
    from .points_refund_round_evidence import _digest as third_digest
    from .points_negotiation_preview import RefundedFunding
    from .points_funding_allocation import plan_release, PointsFundingAllocationError
    from .addon_payment import verify_receipt
    from rest_framework.exceptions import ValidationError as RestValidationError

    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('第三轮后加购核验须在门店事务内执行')
    from stores.context import current_store
    store = current_store()
    if (not store or store.database_alias != transaction.get_connection().alias
            or run._state.db != transaction.get_connection().alias):
        raise ValidationError('第三轮后加购不属于当前门店')
    revision = PointsThirdAddonFundingRevision.objects.select_related(
        'order', 'third__posting', 'third__transfer_revision', 'payment__intent',
        'payment__buyer_transaction__wallet').filter(run=run).first()
    if not revision or not isinstance(revision.snapshot, dict):
        raise ValidationError('第三轮后加购资金版本缺失')
    snap, third, order = revision.snapshot, revision.third, revision.order
    payment, refund, handover = revision.payment, revision.refund, getattr(revision, 'handover', None)
    is_refund, is_handover = refund is not None, handover is not None
    if sum(x is not None for x in (payment, refund, handover)) != 1:
        raise ValidationError('第三轮后加购事件类型异常')
    payment = refund.payment if is_refund else handover.payment if is_handover else payment
    event_time = refund.created_at if is_refund else handover.accepted_at if is_handover else payment.buyer_transaction.created_at
    common = {'kind', 'third_id', 'third_hash', 'previous_id', 'previous_hash',
              'before', 'after', 'allocations', 'releases'}
    extra = ({'refund_id', 'payment_id', 'buyer_transaction_id', 'proposal_id', 'source_plan',
              'participants', 'rounding_surplus', 'rounding_transaction_id'} if is_refund else
             {'payment_id', 'handover_id', 'accepted_at', 'decisions', 'before_shares', 'after_shares'} if is_handover else
             {'payment_id', 'payment_transaction_id', 'sources'})
    digest = hashlib.sha256(json.dumps(snap, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if revision.previous_transfer_id:
        extra |= {'previous_transfer_id', 'previous_transfer_hash'}
    if revision.previous_round_id:
        extra |= {'previous_round_id', 'previous_round_hash'}
    if (set(snap) != common | extra or revision.evidence_hash != digest
            or snap['kind'] != ('POINTS_THIRD_ADDON_REFUND_V1' if is_refund else 'POINTS_THIRD_ADDON_HANDOVER_V1' if is_handover else 'POINTS_THIRD_ADDON_PAYMENT_V1')
            or third.order_id != order.pk or third.evidence_hash != third_digest(third.snapshot)
            or snap['third_id'] != third.pk or snap['third_hash'] != third.evidence_hash
            or snap['payment_id'] != payment.pk or payment.intent.order_id != order.pk
            or (is_refund and (run.refund_id != refund.pk or run.payment_id is not None
                              or refund.order_id != order.pk or snap['refund_id'] != refund.pk
                              or snap['buyer_transaction_id'] != refund.buyer_transaction_id
                              or snap['proposal_id'] != refund.proposal_id))
            or (is_handover and (run.handover_id != handover.pk or run.payment_id is not None
                                  or run.refund_id is not None or snap['handover_id'] != handover.pk))
            or (not is_refund and not is_handover and (run.payment_id != payment.pk or run.refund_id is not None
                                   or snap['payment_transaction_id'] != payment.buyer_transaction_id))
            or not third.posting.termination_processed_at or not event_time
            or not third.posting.termination_processed_at < event_time <= run.created_at
            or revision.created_at < run.created_at):
        raise ValidationError('第三轮后加购资金版本或时序异常')
    events = [(t, 'payment', pk) for pk, t in OrderAddonPayment.objects.filter(
        intent__order=order, buyer_transaction__created_at__gt=third.posting.termination_processed_at,
        buyer_transaction__created_at__lte=event_time).values_list('pk', 'buyer_transaction__created_at')]
    events += [(t, 'refund', pk) for pk, t in OrderAddonRefund.objects.filter(
        order=order, created_at__gt=third.posting.termination_processed_at,
        created_at__lte=event_time).values_list('pk', 'created_at')]
    events += [(t, 'handover', pk) for pk, t in OrderAddonHandover.objects.filter(
        payment__intent__order=order, status='ACCEPTED', accepted_at__gt=third.posting.termination_processed_at,
        accepted_at__lte=event_time).values_list('pk', 'accepted_at')]
    events.sort()
    target = ('refund', refund.pk) if is_refund else ('handover', handover.pk) if is_handover else ('payment', payment.pk)
    if (not events or (events[-1][1], events[-1][2]) != target
            or any(a[0] == b[0] for a, b in zip(events, events[1:]))):
        raise ValidationError('第三轮后加购事件时序无法区分')
    previous = revision.previous
    prior_round = revision.previous_round
    from .models import PointsRefundRoundEvidence
    actual_round = PointsRefundRoundEvidence.objects.filter(order=order, previous__isnull=False,
        created_at__lt=event_time).order_by('-created_at', '-pk').first()
    if (actual_round.pk if actual_round else None) != revision.previous_round_id:
        raise ValidationError('第三轮后加购退款轮次前版缺失或不一致')
    if prior_round and (prior_round.order_id != order.pk
            or snap.get('previous_round_id') != prior_round.pk
            or snap.get('previous_round_hash') != prior_round.evidence_hash
            or prior_round.created_at >= event_time):
        raise ValidationError('多轮退款后加购接替前版异常')
    prior_transfer = revision.previous_transfer
    from .models import PointsThirdTransferRevision
    actual_transfer = PointsThirdTransferRevision.objects.filter(order=order,
        created_at__lt=event_time).order_by('-created_at', '-pk').first()
    if (actual_transfer.pk if actual_transfer else None) != revision.previous_transfer_id:
        raise ValidationError('第三轮后加购转单前版缺失或不一致')
    if prior_transfer and (prior_transfer.order_id != order.pk or prior_transfer.third_id != third.pk
            or snap.get('previous_transfer_id') != prior_transfer.pk
            or snap.get('previous_transfer_hash') != prior_transfer.evidence_hash
            or prior_transfer.created_at >= event_time):
        raise ValidationError('第三轮后加购的转单前版异常')
    prior_times = [row.created_at for row in (previous, prior_transfer, prior_round) if row is not None]
    if len(set(prior_times)) != len(prior_times):
        raise ValidationError('加购资金不同类型前版时序无法区分')
    if previous is None:
        if len(events) != 1 or snap['previous_id'] is not None or snap['previous_hash'] is not None:
            raise ValidationError('第三轮后较早加购资金版本缺失')
    else:
        if (previous.order_id != order.pk or previous.third_id != third.pk or previous.pk >= revision.pk
                or snap['previous_id'] != previous.pk or snap['previous_hash'] != previous.evidence_hash
                or previous.created_at >= event_time):
            raise ValidationError('第三轮后加购前驱版本或时序异常')
        chain = []
        item = previous
        while item:
            if (item.order_id != order.pk or item.third_id != third.pk
                    or (item.previous_id and item.previous_id >= item.pk)
                    or sum(x is not None for x in (item.payment_id, item.refund_id, getattr(item, 'handover_id', None))) != 1):
                raise ValidationError('第三轮后加购前驱链异常')
            chain.append(('payment', item.payment_id) if item.payment_id else ('refund', item.refund_id) if item.refund_id else ('handover', item.handover_id))
            item = item.previous
        if list(reversed(chain)) != [(kind, pk) for _, kind, pk in events[:-1]]:
            raise ValidationError('第三轮后加购与真实前驱链不一致')
    try:
        verify_receipt(payment)
    except RestValidationError as exc:
        raise ValidationError('第三轮后加购付款或退款凭据异常') from exc
    sources = list(payment.allocations.select_related('lot').order_by('pk'))
    # Each posted refund must still point to the actual original payment lots.
    # The allocation counter is cumulative, so compare it with every posted
    # refund rather than treating today's counter as the historical balance.
    by_source = {row.pk: row for row in sources}
    refunded = {row.pk: 0 for row in sources}
    for posted in payment.refund_records.select_related('proposal'):
        parts = posted.proposal.source_plan
        if not isinstance(parts, list) or not parts:
            raise ValidationError('加购退款原付款来源缺失')
        seen = set()
        for part in parts:
            if (not isinstance(part, dict) or set(part) != {'allocation_id', 'kind', 'amount_raw'}
                    or part['allocation_id'] not in by_source or part['allocation_id'] in seen
                    or part['kind'] != by_source[part['allocation_id']].kind
                    or not isinstance(part['amount_raw'], int) or part['amount_raw'] <= 0):
                raise ValidationError('加购退款原付款来源异常')
            seen.add(part['allocation_id'])
            refunded[part['allocation_id']] += part['amount_raw']
        if sum(part['amount_raw'] for part in parts) != posted.amount:
            raise ValidationError('加购退款原付款来源不守恒')
    if any(refunded[row.pk] != row.refunded_amount or refunded[row.pk] > row.amount for row in sources):
        raise ValidationError('加购退款与原付款余额不一致')
    if is_handover:
        from .points_third_addon_responsibilities import current as responsibilities_at
        try:
            _, prior_rows = responsibilities_at(payment, lock=False, before=handover.accepted_at, with_share_id=True)
        except RestValidationError as exc:
            raise ValidationError('接替前真实份额待核对') from exc
        before_shares = [[row.get('share_id'), row['segment_id'], row['provider_id'], row['basis'], row['income'],
                          row['commission_snapshot'], row['is_current']] for row in prior_rows]
        rows = list(handover.shares.select_related('segment__attribution').order_by('position'))
        after_shares = [[row.pk, row.segment_id, row.segment.provider_account_id, row.basis, row.income,
                         row.commission_snapshot, row.is_current] for row in rows]
        decisions = list(handover.decisions.order_by('pk'))
        decision_rows = [[row.pk, row.account_id, row.role, row.accepted,
                          row.decided_at.isoformat() if row.decided_at else None] for row in decisions]
        expected = {(payment.intent.buyer_id, 'BOSS', True),
                    (handover.source_segment.provider_account_id, 'PROVIDER', True),
                    (handover.target_segment.provider_account_id, 'PROVIDER', True)}
        target = handover.target_segment
        from .models import OrderProviderCompletionFact
        target_finished = OrderProviderCompletionFact.objects.filter(
            order=order, segment=target, completed_at__lte=handover.accepted_at).exists()
        if (handover.status != 'ACCEPTED' or snap['accepted_at'] != handover.accepted_at.isoformat()
                or target.attribution.order_id != order.pk or target.pk == handover.source_segment_id
                or target.accepted_at > handover.accepted_at or not target.started_at
                or target.started_at > handover.accepted_at
                or (target.released_at and target.released_at <= handover.accepted_at) or target_finished
                or snap['decisions'] != decision_rows or len(decisions) != len(expected)
                or {(row.account_id, row.role, row.accepted) for row in decisions} != expected
                or any(not row.decided_at or row.decided_at < handover.created_at for row in decisions)
                or max(row.decided_at for row in decisions) != handover.accepted_at
                or snap['before_shares'] != before_shares or snap['after_shares'] != after_shares
                or [row.position for row in rows] != list(range(len(rows)))
                or any(row.segment.attribution.order_id != order.pk for row in rows)
                or sum(row[3] for row in before_shares) != sum(row[3] for row in after_shares)):
            raise ValidationError('加购接替确认或份额快照异常')
        active = [row for row in before_shares if row[6]]
        if (len(active) != 1 or active[0][1] != handover.source_segment_id
                or active[0][3] != handover.source_basis_before
                or handover.retained_basis + handover.transferred_basis != active[0][3]
                or handover.transferred_basis <= 0):
            raise ValidationError('加购接替前负责人或份额异常')
        from .addon_handover import _income
        expected_shares = [row[1:] for row in before_shares if not row[6]]
        source = active[0]
        if handover.retained_basis:
            full = _income(source[3], source[5])
            retained = (_income(handover.retained_basis, source[5]) if source[4] == full
                        else source[4] * handover.retained_basis // source[3])
            expected_shares.append([source[1], source[2], handover.retained_basis, retained, source[5], False])
        if not after_shares:
            raise ValidationError('加购接替后份额缺失')
        target_terms = after_shares[-1][5]
        expected_shares.append([handover.target_segment_id, handover.target_segment.provider_account_id,
                                handover.transferred_basis, _income(handover.transferred_basis, target_terms),
                                target_terms, True])
        if [row[1:] for row in after_shares] != expected_shares:
            raise ValidationError('加购接替后份额与原确认不一致')
    elif is_refund:
        if (snap['source_plan'] != refund.proposal.source_plan
                or snap['rounding_surplus'] != refund.rounding_surplus
                or (snap['rounding_transaction_id'] is not None and
                    snap['rounding_transaction_id'] != refund.rounding_transaction_id)
                or (snap['rounding_transaction_id'] is None and refund.rounding_transaction_id
                    and refund.rounding_transaction.created_at <= revision.created_at)):
            raise ValidationError('加购退款方案与快照不一致')
        by_id = {row.pk: row for row in sources}
        source_plan = snap['source_plan']
        if not isinstance(source_plan, list) or any(
                not isinstance(entry, dict) or set(entry) != {'allocation_id', 'kind', 'amount_raw'}
                or entry['allocation_id'] not in by_id or by_id[entry['allocation_id']].kind != entry['kind']
                or not isinstance(entry['amount_raw'], int) or entry['amount_raw'] <= 0 for entry in source_plan):
            raise ValidationError('加购退款原付款来源异常')
        if ([e['allocation_id'] for e in source_plan] != sorted(set(e['allocation_id'] for e in source_plan))
                or sum(e['amount_raw'] for e in source_plan) != refund.amount):
            raise ValidationError('加购退款原付款来源不守恒')
        amounts = {kind: sum(e['amount_raw'] for e in source_plan if e['kind'] == kind)
                   for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
        participants = []
        for person in refund.proposal.participants.order_by('account_id'):
            participants.append({'id': person.pk, 'account_id': person.account_id, 'basis': person.basis,
                'burden': person.burden, 'share_plan': person.share_plan,
                'addon_pending': person.addon_pending_deduction, 'main_pending': person.main_pending_deduction,
                'wallet': person.wallet_deduction, 'deposit': person.deposit_deduction,
                'provider_transaction_id': person.provider_transaction_id,
                'earning_deductions': [list(x) for x in person.earning_deductions.filter(refund=refund).order_by('pk').values_list('pk','payment_id','segment_id','amount')],
                'main_deductions': [list(x) for x in person.main_segment_deductions.order_by('pk').values_list('pk','segment_id','amount')],
                'share_allocations': [list(x) for x in refund.share_allocations.filter(participant=person).order_by('pk').values_list('pk','payment_id','share_id','refund_basis','earning_deduction')],
                'deposit_deficit_ids': list(refund.deposit_deficits.filter(account_id=person.account_id).order_by('pk').values_list('pk',flat=True))})
        if snap['participants'] != participants:
            raise ValidationError('加购退款个人承担与快照不一致')
        from .models import PointsHandoverRefundInvalidation
        from .points_third_handover_invalidation import capture as capture_invalidation, digest as invalidation_digest
        for invalidation in PointsHandoverRefundInvalidation.objects.filter(refund=refund).select_related(
                'handover__payment__intent', 'refund__payment__intent'):
            frozen = invalidation.snapshot
            if isinstance(frozen, dict) and frozen.get('kind') == 'POINTS_THIRD_TRANSFER_INVALIDATION_V1':
                try:
                    actual_invalidation = capture_invalidation(invalidation.handover, refund)
                except RestValidationError as exc:
                    raise ValidationError('第三轮待交接替失效事实待核对') from exc
                predecessor = prior_transfer
                while predecessor and predecessor.pk != frozen.get('transfer_revision_id'):
                    earlier = predecessor.previous_transfer
                    if earlier and (earlier.order_id != order.pk or earlier.created_at >= predecessor.created_at):
                        raise ValidationError('转单前驱链时序异常')
                    predecessor = earlier
                if (frozen != actual_invalidation or invalidation.evidence_hash != invalidation_digest(frozen)
                        or not predecessor or frozen.get('transfer_revision_hash') != predecessor.evidence_hash):
                    raise ValidationError('第三轮待交接替失效与真实退款不一致')
        # Reconstruct each affected payment immediately before this refund,
        # including 0283 main-order deductions. This uses current reads only;
        # the receipt path must never acquire an order or money lock.
        from .points_third_addon_responsibilities import current as responsibilities_at
        from .addon_refund_cross_plan import verify_posted
        try:
            verify_posted(refund)
            affected = {payment.pk} | set(refund.earning_deductions.values_list('payment_id', flat=True))
            from .models import OrderAddonPayment
            payments = OrderAddonPayment.objects.filter(pk__in=affected, intent__order=order).select_related('intent')
            available = {}
            for affected_payment in payments:
                _, rows = responsibilities_at(affected_payment, lock=False, before=refund.created_at,
                    with_share_id=True, exclude_refund_id=refund.pk)
                for row in rows:
                    key = (affected_payment.pk, row['provider_id'], row['segment_id'])
                    prior = available.setdefault(key, [0, 0])
                    prior[0] += row['basis']
                    prior[1] += row['income']
            for person in refund.proposal.participants.all():
                for part in person.share_plan:
                    key = (payment.pk, person.account_id, part['segment_id'])
                    balance = available.get(key, [0, 0])
                    if part['refund_basis'] > balance[0]:
                        raise ValidationError('加购退款超过本人原时点份额')
                    balance[0] -= part['refund_basis']
                for deduction in person.earning_deductions.filter(refund=refund):
                    key = (deduction.payment_id, person.account_id, deduction.segment_id)
                    balance = available.get(key, [0, 0])
                    if deduction.amount > balance[1]:
                        raise ValidationError('加购退款超过本人原时点待结收益')
                    balance[1] -= deduction.amount
        except RestValidationError as exc:
            raise ValidationError('加购退款份额或确认方案待核对') from exc
    else:
        if snap['sources'] != [[s.pk, s.lot_id, s.kind, s.amount] for s in sources]:
            raise ValidationError('第三轮后加购付款来源与快照不一致')
        amounts = {kind: sum(s.amount for s in sources if s.kind == kind)
                   for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
        if sum(amounts.values()) != payment.intent.amount or amounts['PREPAID'] != payment.principal:
            raise ValidationError('第三轮后加购付款来源不守恒')
    rounds = third.snapshot.get('rounds') if isinstance(third.snapshot, dict) else None
    if not isinstance(rounds, list) or len(rounds) != 3 or not isinstance(rounds[-1], dict):
        raise ValidationError('第三轮退款资金版本异常')
    source = rounds[-1].get('release_source')
    if not isinstance(source, list) or len(source) != 2 or source[0] != 'current' or not isinstance(source[1], dict):
        raise ValidationError('第三轮退款释放来源异常')
    current = source[1]
    allocations = list(OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order=order) | Q(service_order=order), created_at__lt=run.created_at).order_by('pk'))
    if witness_receipt_id is not None:
        from users.receipt_usage_ledger import _locked_receipt
        from users.receipt_usage_sources import _require_current_unlocked_reads
        _locked_receipt(witness_receipt_id)
        _require_current_unlocked_reads()
    if prior_round is not None:
        from .points_round_receipt_usage import _verify as verify_round
        verify_round(prior_round, receipt_id=(witness_receipt_id or
            (allocations[0].receipt_id if allocations else None)), order_locked=order_locked)
    if prior_transfer is not None:
        _verify_third_transfer(prior_transfer, witness_receipt_id=(witness_receipt_id or
            (allocations[0].receipt_id if allocations else None)), order_locked=order_locked)
    if previous is not None:
        # Replaying the predecessor uses the receipt already held by the
        # caller; it never locks the predecessor's order or another receipt.
        _verify(previous.run, witness_receipt_id=(witness_receipt_id or
            (allocations[0].receipt_id if allocations else None)), order_locked=order_locked)
    elif allocations:
        from .points_third_refund_receipt_usage import facts as third_facts
        released = third.posting.funding_releases.select_related('allocation__receipt').order_by('pk').first()
        witness = released or SimpleNamespace(posting=third.posting, allocation=allocations[0], pk=0, amount=0)
        third_facts(witness, read_receipt_id=witness_receipt_id or witness.allocation.receipt_id)
    elif witness_receipt_id is not None:
        from .points_third_refund_receipt_usage import facts as third_facts
        witness = OrderPointsFundingAllocation.objects.filter(
            Q(redemption__order=order) | Q(service_order=order), receipt_id=witness_receipt_id).first()
        if not witness:
            raise ValidationError('第三轮后到账见证不属于本单')
        third_facts(SimpleNamespace(posting=third.posting, allocation=witness, pk=0, amount=0),
                    read_receipt_id=witness_receipt_id)
    elif order_locked:
        from .points_third_refund_no_receipt import verify_locked
        try:
            verify_locked(order, third.posting, third.transfer_revision, current)
        except RestValidationError as exc:
            raise ValidationError('第三轮无到账退款来源异常') from exc
    else:
        raise ValidationError('无到账时须在订单资金事务中核验')
    before_rows = []
    for item in allocations:
        prior = sum(model.objects.filter(allocation=item, **{field + '__lt': run.created_at})
                    .aggregate(total=Sum('amount'))['total'] or 0 for model, field in (
            (OrderPointsFundingRelease, 'created_at'), (OrderPointsTransferFundingRelease, 'created_at'),
            (PointsPostRefundTransferRelease, 'revision__created_at'),
            (PointsFollowupTransferRelease, 'revision__created_at'),
            (OrderAddonFundingRelease, 'run__created_at'), (PointsThirdTransferRelease, 'revision__created_at')))
        if prior > item.amount:
            raise ValidationError('第三轮后付款的历史释放超过分配')
        before_rows.append([item.pk, item.amount, prior])
    if snap['allocations'] != before_rows or run.allocation_snapshot != before_rows:
        raise ValidationError('第三轮后付款的到账与历史释放不一致')
    try:
        latest = max([x for x in (previous, prior_transfer, prior_round) if x is not None],
                     key=lambda x: x.created_at, default=None)
        if latest is None:
            base = RefundedFunding(**current['funding'])
            earlier_ids = {item[0] for item in current['allocations']}
            boundary = third.posting.created_at
        else:
            if latest is prior_round:
                round_source = latest.snapshot['rounds'][-1]['release_source'][1]
                base = RefundedFunding(**round_source['funding'])
                earlier_ids = {item[0] for item in round_source['allocations']}
            else:
                base = RefundedFunding(**latest.snapshot['after'])
                earlier_ids = {item[0] for item in latest.snapshot['allocations']}
            boundary = latest.created_at
        later = [item for item in allocations if item.pk not in earlier_ids]
        if any(item.created_at <= boundary for item in later):
            raise ValidationError('第三轮后新增到账时序异常')
        available = base.allocated_units - base.release_units + sum(item.amount for item in later)
        before = asdict(replace(base, allocated_units=available, release_units=0,
                                remaining_units=max(base.required_units - available, 0)))
        if snap['before'] != before or available != sum(a - r for _, a, r in before_rows):
            raise ValidationError('第三轮后付款前资金版本不一致')
        sign = -1 if is_refund else 1
        if is_handover:
            principal = before['net_principal_units']
            provider = before['provider_income_units'] + sum(row[4] for row in after_shares) - sum(row[4] for row in before_shares)
            external, rounding = before['planned_provider_external_units'], before['rounding_reserved_units']
        else:
            principal = before['net_principal_units'] + sign * amounts['PREPAID']
            provider = before['provider_income_units'] + (-refund.addon_pending_deduction - refund.main_pending_deduction
                        if is_refund else payment.provider_income)
            external = before['planned_provider_external_units'] + (refund.wallet_deduction + refund.deposit_deduction if is_refund else 0)
            rounding = before['rounding_reserved_units'] + (refund.rounding_surplus if is_refund else 0)
        margin = principal + external - provider - before['inviter_income_units']
        required = max(rounding - margin, 0)
        release = max(available - required, 0)
        after = dict(before, net_coin_units=before['net_coin_units'] + (0 if is_handover else sign * (refund.amount if is_refund else payment.intent.amount)),
            net_principal_units=principal, net_gift_units=before['net_gift_units'] + (0 if is_handover else sign * amounts['GIFT']),
            net_compensation_units=before['net_compensation_units'] + (0 if is_handover else sign * amounts['COMPENSATION']),
            provider_income_units=provider, planned_provider_external_units=external, rounding_reserved_units=rounding,
            store_income_units=margin + required, required_units=required,
            allocated_units=available, release_units=release, remaining_units=max(required - available, 0))
        if min(after['net_coin_units'], principal, after['net_gift_units'], after['net_compensation_units'], provider) < 0:
            raise ValidationError('第三轮后加购事件超过真实剩余资金')
        planned = sorted([[p.allocation_id, p.amount] for p in plan_release(allocations=before_rows, amount=release)])
    except (KeyError, TypeError, ValueError, PointsFundingAllocationError) as exc:
        raise ValidationError('第三轮后加购资金回放异常') from exc
    actual = sorted([list(row) for row in run.releases.values_list('allocation_id', 'amount')])
    if (snap['after'] != after or snap['releases'] != planned or actual != planned
            or (run.required_amount, run.allocated_before, run.released_amount) != (required, available, release)
            or after['net_coin_units'] != principal + after['net_gift_units'] + after['net_compensation_units']
            or principal + after['planned_provider_external_units'] + required !=
               provider + after['inviter_income_units'] + after['store_income_units']):
        raise ValidationError('第三轮后付款释放或资金不守恒')
    return revision, planned


def facts(row, *, witness_receipt_id=None):
    from users.receipt_usage_sources import _digest
    revision, releases = _verify(row.run, witness_receipt_id=witness_receipt_id or row.allocation.receipt_id)
    if row.amount <= 0 or [row.allocation_id, row.amount] not in releases:
        raise ValidationError('第三轮后加购到账释放与计划不一致')
    kind = ('points-third-addon-refund' if revision.refund_id else
            'points-third-addon-handover' if revision.handover_id else 'points-third-addon-payment')
    return ('RELEASE', f'order:{row.allocation_id}', f'{kind}:{row.pk}', row.amount,
            _digest([kind, row.pk, row.allocation_id, row.allocation.receipt_id,
                     row.amount, row.run_id, revision.pk, revision.evidence_hash]))



def _verify_third_transfer(version, *, witness_receipt_id=None, order_locked=False):
    """Replay a real transfer without locking an order in receipt context."""
    from datetime import timedelta
    from .models import (PointsThirdAddonFundingRevision, PointsThirdTransferRevision,
        PointsThirdTransferRelease, OrderAddonPayment, OrderAddonFundingRelease,
        OrderPointsFundingAllocation, OrderPointsFundingRelease, OrderPointsTransferFundingRelease,
        PointsPostRefundTransferRelease, PointsFollowupTransferRelease)
    from .points_refund_round_evidence import _digest as third_digest
    from .points_transfer_segment_math import segment_lines
    from .points_transfer_refund_evidence import _facts_current_read, _facts_without_receipt_locked, _matches_history
    from .points_third_addon_responsibilities import current as responsibilities_at
    from .points_funding_allocation import plan_release, PointsFundingAllocationError
    from .points_negotiation_preview import RefundedFunding
    from rest_framework.exceptions import ValidationError as RestValidationError
    from stores.context import current_store
    store = current_store()
    if (not transaction.get_connection().in_atomic_block or not store
            or store.database_alias != transaction.get_connection().alias
            or version._state.db != transaction.get_connection().alias):
        raise ValidationError('第三轮后转单不属于当前门店事务')
    order, third, transfer, snap = version.order, version.third, version.transfer, version.snapshot
    keys = {'kind','third_id','third_hash','previous_addon_id','previous_addon_hash',
        'previous_transfer_id','previous_transfer_hash','transfer_id','posting_ids','before_facts','facts',
        'before_income','segment_income','before','after','allocations','releases','pending_handover'}
    prior_round = version.previous_round
    if prior_round:
        keys |= {'previous_round_id', 'previous_round_hash'}
    digest = hashlib.sha256(json.dumps(snap, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if (not isinstance(snap, dict) or set(snap) != keys or snap['kind'] != 'POINTS_THIRD_TRANSFER_V1'
            or digest != version.evidence_hash or third.order_id != order.pk
            or third.evidence_hash != third_digest(third.snapshot) or snap['third_id'] != third.pk
            or snap['third_hash'] != third.evidence_hash or snap['transfer_id'] != transfer.pk
            or transfer.previous_segment.attribution.order_id != order.pk
            or transfer.next_segment.attribution.order_id != order.pk
            or not third.posting.termination_processed_at
            or not third.posting.termination_processed_at < transfer.created_at <= version.created_at
            or (not prior_round and snap['posting_ids'] != [third.transfer_revision.first_posting_id,
                third.transfer_revision.second_posting_id, third.posting_id])):
        raise ValidationError('第三轮后转单原事件或资金版本异常')
    from .models import PointsRefundRoundEvidence, PointsNegotiationPosting
    actual_round = PointsRefundRoundEvidence.objects.filter(order=order, previous__isnull=False,
        created_at__lt=transfer.created_at).order_by('-created_at', '-pk').first()
    if (actual_round.pk if actual_round else None) != version.previous_round_id:
        raise ValidationError('转单多轮退款前版缺失或不一致')
    if prior_round:
        postings = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order,
            created_at__lt=transfer.created_at).order_by('created_at', 'pk').values_list('pk', flat=True))
        if (prior_round.order_id != order.pk or prior_round.created_at >= transfer.created_at
                or snap.get('previous_round_id') != prior_round.pk
                or snap.get('previous_round_hash') != prior_round.evidence_hash
                or snap['posting_ids'] != postings
                or snap['posting_ids'] != [item['posting_id'] for item in prior_round.snapshot.get('rounds', [])]):
            raise ValidationError('转单多轮退款前版或实际记账顺序异常')
    before_addon = PointsThirdAddonFundingRevision.objects.filter(order=order, created_at__lt=transfer.created_at).order_by('-created_at','-pk').first()
    before_transfer = PointsThirdTransferRevision.objects.filter(order=order, created_at__lt=transfer.created_at).exclude(pk=version.pk).order_by('-created_at','-pk').first()
    if ((before_addon.pk if before_addon else None) != version.previous_addon_id
            or (before_transfer.pk if before_transfer else None) != version.previous_transfer_id
            or snap['previous_addon_id'] != version.previous_addon_id
            or snap['previous_transfer_id'] != version.previous_transfer_id
            or snap['previous_addon_hash'] != (before_addon.evidence_hash if before_addon else None)
            or snap['previous_transfer_hash'] != (before_transfer.evidence_hash if before_transfer else None)):
        raise ValidationError('第三轮后转单前驱版本异常')
    allocations = list(OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order=order) | Q(service_order=order), created_at__lt=version.created_at).order_by('pk'))
    witness = witness_receipt_id or (allocations[0].receipt_id if allocations and order_locked else None)
    try:
        if witness is not None:
            facts = _facts_current_read(order, witness)
        elif order_locked:
            facts = _facts_without_receipt_locked(order)
        else:
            raise ValidationError('转单无到账时须在订单资金事务中核验')
        if (not _matches_history(facts, order, snap['before_facts'], transfer.created_at - timedelta(microseconds=1))
                or not _matches_history(facts, order, snap['facts'], version.created_at)):
            raise ValidationError('第三轮后转单履约历史与快照不一致')
        if (not snap['facts']['transfers'] or snap['facts']['transfers'][-1]['id'] != transfer.pk
                or [x['id'] for x in snap['facts']['transfers'][:-1]] !=
                   [x['id'] for x in snap['before_facts']['transfers']]):
            raise ValidationError('第三轮后转单事实顺序异常')
        old_id = snap['before_facts']['transfers'][-1]['id'] if snap['before_facts']['transfers'] else 0
        old = segment_lines(snap['before_facts'], through_transfer_id=old_id)
        now = segment_lines(snap['facts'], through_transfer_id=transfer.pk)
        if (snap['before_income'] != sum(row[4] for row in old)
                or snap['segment_income'] != [[row[0], row[4]] for row in now]):
            raise ValidationError('第三轮后转单逐段收益异常')
        pending = []
        for payment in OrderAddonPayment.objects.filter(intent__order=order,
                buyer_transaction__created_at__lt=transfer.created_at).order_by('pk'):
            _, shares = responsibilities_at(payment, lock=False, before=transfer.created_at)
            source = [row for row in shares if row['is_current'] and row['basis'] > 0
                      and row['segment_id'] == transfer.previous_segment_id]
            if len(source) > 1:
                raise ValidationError('转单时加购责任不唯一')
            if source:
                pending.append([payment.pk, source[0]['segment_id'], source[0]['basis']])
        if snap['pending_handover'] != pending:
            raise ValidationError('转单时加购待交接事实异常')
        # A target's actual transfer can invalidate a pending STANDARD
        # proposal without changing the funding or producing a RELEASE.
        # Its immutable cause must still belong to this historical prefix.
        from .models import PointsThirdHandoverTargetInvalidation
        from .points_third_target_invalidation import verify as verify_target_invalidation
        for invalidation in PointsThirdHandoverTargetInvalidation.objects.filter(transfer=transfer).select_related(
                'handover__payment__intent', 'original_revision', 'transfer', 'posting'):
            verify_target_invalidation(invalidation)
            prior = before_transfer
            while prior and prior.pk != invalidation.original_revision_id:
                earlier = prior.previous_transfer
                if earlier and (earlier.order_id != order.pk or earlier.created_at >= prior.created_at):
                    raise ValidationError('目标失效的转单前驱链异常')
                prior = earlier
            if not prior or prior.evidence_hash != invalidation.original_revision.evidence_hash:
                raise ValidationError('目标失效的原待交转单不在真实前缀')
        for prior in (before_addon, before_transfer, prior_round):
            if prior is not None:
                if prior is before_addon:
                    _verify(prior.run, witness_receipt_id=witness, order_locked=order_locked)
                elif prior is before_transfer:
                    _verify_third_transfer(prior, witness_receipt_id=witness, order_locked=order_locked)
                else:
                    from .points_round_receipt_usage import _verify as verify_round
                    verify_round(prior, receipt_id=witness, order_locked=order_locked)
        if not before_addon and not before_transfer and not prior_round:
            current = third.snapshot['rounds'][-1]['release_source'][1]
            if witness is not None:
                from .points_third_refund_receipt_usage import facts as third_facts
                allocation = OrderPointsFundingAllocation.objects.filter(
                    Q(redemption__order=order) | Q(service_order=order), receipt_id=witness).first()
                if not allocation:
                    raise ValidationError('到账见证不属于原订单')
                third_facts(SimpleNamespace(posting=third.posting, allocation=allocation, pk=0, amount=0), read_receipt_id=witness)
            elif order_locked:
                from .points_third_refund_no_receipt import verify_locked
                verify_locked(order, third.posting, third.transfer_revision, current)
            else:
                raise ValidationError('无到账时须在订单资金事务中核验')
        rows = []
        for allocation in allocations:
            prior = sum(model.objects.filter(allocation=allocation, **{field + '__lt': version.created_at})
                .aggregate(total=Sum('amount'))['total'] or 0 for model, field in (
                    (OrderPointsFundingRelease, 'created_at'), (OrderPointsTransferFundingRelease, 'created_at'),
                    (PointsPostRefundTransferRelease, 'revision__created_at'),
                    (PointsFollowupTransferRelease, 'revision__created_at'),
                    (OrderAddonFundingRelease, 'run__created_at'), (PointsThirdTransferRelease, 'revision__created_at')))
            if prior > allocation.amount:
                raise ValidationError('转单前累计释放超过分配')
            rows.append([allocation.pk, allocation.amount, prior])
        if snap['allocations'] != rows:
            raise ValidationError('转单前到账分配或释放异常')
        predecessors = [x for x in (before_addon, before_transfer, prior_round) if x]
        if len({x.created_at for x in predecessors}) != len(predecessors):
            raise ValidationError('转单不同资金前版时序无法区分')
        latest = max(predecessors, key=lambda x: x.created_at, default=None)
        if latest:
            if latest is prior_round:
                round_source = latest.snapshot['rounds'][-1]['release_source'][1]
                base = RefundedFunding(**round_source['funding'])
                old_ids = {x[0] for x in round_source['allocations']}
            else:
                base = RefundedFunding(**latest.snapshot['after'])
                old_ids = {x[0] for x in latest.snapshot['allocations']}
            boundary = latest.created_at
        else:
            base = RefundedFunding(**third.snapshot['rounds'][-1]['release_source'][1]['funding'])
            old_ids = {x[0] for x in third.snapshot['rounds'][-1]['release_source'][1]['allocations']}
            boundary = third.posting.created_at
        added = [x for x in allocations if x.pk not in old_ids]
        if any(x.created_at <= boundary for x in added):
            raise ValidationError('转单新增到账时序异常')
        available = base.allocated_units - base.release_units + sum(x.amount for x in added)
        before = asdict(replace(base, allocated_units=available, release_units=0,
                                remaining_units=max(base.required_units - available, 0)))
        if snap['before'] != before or available != sum(amount - released for _, amount, released in rows):
            raise ValidationError('转单前资金来源异常')
        provider = before['provider_income_units'] + sum(x[4] for x in now) - snap['before_income']
        margin = before['net_principal_units'] + before['planned_provider_external_units'] - provider - before['inviter_income_units']
        required = max(before['rounding_reserved_units'] - margin, 0)
        release = max(available - required, 0)
        after = dict(before, provider_income_units=provider, store_income_units=margin + required,
            required_units=required, allocated_units=available, release_units=release,
            remaining_units=max(required - available, 0))
        planned = sorted([[x.allocation_id, x.amount] for x in plan_release(allocations=rows, amount=release)])
        actual = sorted([list(x) for x in version.releases.values_list('allocation_id','amount')])
        if provider < 0 or snap['after'] != after or snap['releases'] != planned or planned != actual:
            raise ValidationError('转单实际收益或到账释放异常')
    except (RestValidationError, KeyError, TypeError, ValueError, PointsFundingAllocationError) as exc:
        raise ValidationError('第三轮后转单资金事实待核对') from exc
    return version, planned


def third_transfer_facts(row, *, witness_receipt_id=None):
    from users.receipt_usage_sources import _digest
    version, releases = _verify_third_transfer(row.revision,
        witness_receipt_id=witness_receipt_id or row.allocation.receipt_id)
    if row.amount <= 0 or [row.allocation_id, row.amount] not in releases:
        raise ValidationError('转单到账释放不属于真实计划')
    return ('RELEASE', f'order:{row.allocation_id}', f'points-third-transfer:{row.pk}', row.amount,
        _digest(['points-third-transfer', row.pk, row.allocation_id, row.allocation.receipt_id,
                 row.amount, version.pk, version.evidence_hash]))


def verify_third_transfer_run_locked(version):
    from .points_transfer_refund_access import permits
    if not any(permits(version.order, phase=phase) for phase in ('transfer', 'refund', 'settlement')):
        raise ValidationError('转单资金核验须在同订单事务中进行')
    result, _ = _verify_third_transfer(version, order_locked=True)
    for row in version.releases.select_related('allocation__receipt').order_by('allocation__receipt_id','pk'):
        third_transfer_facts(row)
    return result


def verify_run_receipt(run, receipt_id):
    """Replay with only the already-held receipt; do not lock another receipt or order."""
    if receipt_id is None:
        raise ValidationError('到账本地核验缺少已持锁到账凭证')
    revision, _ = _verify(run, witness_receipt_id=receipt_id)
    return revision


def verify_run_locked(run):
    """Caller holds the order/money locks and all relevant receipt locks."""
    from .points_transfer_refund_access import permits
    if not any(permits(phase=phase) for phase in ('payment', 'refund', 'settlement')):
        raise ValidationError('第三轮后加购须在受控订单资金事务中核对')
    revision, releases = _verify(run, order_locked=True)
    if not any(permits(revision.order, phase=phase) for phase in ('payment', 'refund', 'settlement')):
        raise ValidationError('第三轮后加购与订单上下文不一致')
    for row in run.releases.select_related('allocation__receipt').order_by('allocation__receipt_id', 'pk'):
        facts(row)
    return revision
