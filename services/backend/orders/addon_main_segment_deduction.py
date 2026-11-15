"""Attribute an add-on refund's unpaid main earnings to real service segments."""
from collections import defaultdict

from rest_framework.exceptions import ValidationError

from .models import (OrderAddonRefundMainSegmentDeduction, OrderOriginalShareTransfer,
                     OrderSegmentFinancialSnapshot, OrderSegmentPersonSettlement,
                     OrderTransferNegotiationEvidence, OrdinaryTransferFollowupRound)


def remaining_locked(order, account_id):
    if OrderSegmentPersonSettlement.objects.filter(manifest__order=order, account_id=account_id).exists():
        return []
    transferred = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
    if transferred and order.refund_records.count() > 3:
        from .points_service_payment import needs_integrated_processing
        if needs_integrated_processing(order):
            from .points_round_funding import current_locked
            state = current_locked(order)
            segments = list(order.original_attribution.segments.select_for_update().filter(
                provider_account_id=account_id, pk__in=state['segment_income']).order_by('-accepted_at', '-pk'))
            # A closed segment cannot be reopened, but its verified unpaid
            # retained earnings still belong to this person and may cover an
            # independent add-on refund before wallet or deposit is used.
            return [(row.pk, state['segment_income'][row.pk]) for row in segments
                    if state['segment_income'][row.pk] > 0]
    if transferred and order.refund_records.count() == 3:
        from .points_service_payment import needs_integrated_processing
        if needs_integrated_processing(order):
            return _third_remaining_locked(order, account_id)
    included_prior = {}
    points_closed = set()
    if transferred:
        from .points_service_payment import needs_integrated_processing
        points = needs_integrated_processing(order)
        from .segment_settlement_plan import build_segment_settlement_plan
        from .settlement import SettlementError
        try:
            if points and order.refund_records.exists():
                from .points_transfer_refund_evidence import reconstruct_funding_locked
                postings = []
                if order.refund_records.count() == 2:
                    from .models import PointsNegotiationPosting
                    from .points_transfer_followup_termination import verify_closed_or_settled_locked
                    postings = list(PointsNegotiationPosting.objects.filter(
                        points_refund__redemption__order=order).order_by('points_refund__refund_id'))
                    if len(postings) != 2:
                        raise ValidationError('两次主单退款的履约凭证缺失')
                    verify_closed_or_settled_locked(postings[-1])
                    posting = postings[0]
                else:
                    from .points_transfer_refund_continuation import verify_locked
                    posting = verify_locked(order)
                    postings = [posting]
                from .models import PointsPostRefundTransferRevision
                version = PointsPostRefundTransferRevision.objects.filter(order=order).order_by('-pk').first()
                if version:
                    from .points_post_refund_transfer import verify_locked as verify_version
                    from .points_transfer_refund_evidence import _reconstruct_from_verified_facts
                    verify_version(version)
                    frozen = version.snapshot['facts']
                    history = _reconstruct_from_verified_facts(order.pk, frozen,
                        refund_id=posting.points_refund.refund_id, through_transfer_id=version.transfer_id)
                else:
                    frozen = posting.participants['facts']
                    history = reconstruct_funding_locked(order.pk, posting.participants,
                        refund_id=posting.points_refund.refund_id,
                        through_transfer_id=frozen['transfers'][-1]['id'])
                income = {line[0]: line[4] for line in history.segment_shares if line[1] == account_id}
                for closed_posting in postings:
                    chosen = closed_posting.proposal.affected_segment_ids
                    if chosen is None:
                        chosen = [item['id'] for item in frozen['segments'] if item['account_id'] == account_id
                                  and item['started_at'] and account_id in closed_posting.proposal.affected_accounts]
                    points_closed.update(chosen)
            else:
                from .points_transfer_refund_access import addon_payment_read
                with addon_payment_read(order):
                    has_zero_agreement = OrderTransferNegotiationEvidence.objects.filter(order=order,
                        execution__zero_order=order, execution__termination_processed_at__isnull=False).exists()
                    plan = build_segment_settlement_plan(order, allow_recorded=True,
                        _allow_points=points,
                        _allow_recorded_refunds=bool(not points and (order.refund_records.exists() or has_zero_agreement)))
                income = {line.segment_id: line.income for line in plan.segments if line.account_id == account_id}
        except (SettlementError, KeyError, IndexError, ValueError) as exc:
            raise ValidationError('主单逐段收益待核对') from exc
    else:
        snapshots = list(OrderSegmentFinancialSnapshot.objects.select_for_update().filter(
            segment__attribution__order=order, segment__provider_account_id=account_id,
            segment__started_at__isnull=False).select_related('segment').order_by('segment_id'))
        income = {row.segment_id: row.income_at_entry for row in snapshots}
        # A terminated person's original snapshot is no longer their unpaid
        # income. Preserve the negotiated retained share before allocating a
        # subsequent add-on refund to this segment.
        from .points_service_payment import needs_integrated_processing
        if needs_integrated_processing(order) and order.refund_records.count() == 1:
            from .models import PointsNegotiationPosting
            from .points_negotiation_termination import verify_termination_locked
            posting = PointsNegotiationPosting.objects.select_related(
                'proposal__case__order', 'points_refund__refund').filter(
                    points_refund__redemption__order=order).first()
            if not posting:
                raise ValidationError('积分主单退款的履约凭证缺失')
            posting = verify_termination_locked(posting)
            shares = list(posting.shares.filter(account_id=account_id))
            if shares:
                if len(shares) != 1 or len(snapshots) != 1:
                    raise ValidationError('积分主单退款后的本人收益范围待核对')
                previous = verified_deductions(order, before=posting.created_at)
                snapshot = snapshots[0]
                share = shares[0]
                if (share.original_income != snapshot.income_at_entry - previous.get(snapshot.segment_id, 0)
                        or share.retained_income != share.original_income - share.pending_deduction
                        or share.retained_income < 0):
                    raise ValidationError('积分主单退款后的本人保留收益异常')
                income[snapshot.segment_id] = share.retained_income
                included_prior[snapshot.segment_id] = previous.get(snapshot.segment_id, 0)
        elif not needs_integrated_processing(order) and order.refund_records.count() == 1:
            from .models import NegotiationExecution
            execution = NegotiationExecution.objects.filter(refund__order=order,
                termination_processed_at__isnull=False).select_related('refund__order').first()
            if execution:
                from .ordinary_refund_receipt_evidence import verified_shares
                shares = [row for row in verified_shares(execution) if row.account_id == account_id]
                if shares:
                    if len(shares) != 1 or len(snapshots) != 1:
                        raise ValidationError('已终止陪玩主单收益版本待核对')
                    income[snapshots[0].segment_id] = shares[0].retained_income
                    # Retained income already reflects pre-negotiation add-on
                    # deductions; only later deductions remain to be charged.
                    included_prior = verified_deductions(order, before=execution.created_at)
    if not income:
        raise ValidationError('主单缺少可归属的本人履约段')
    closed = set(points_closed)
    evidence = OrderTransferNegotiationEvidence.objects.filter(order=order,
        execution__termination_processed_at__isnull=False).select_related('execution__proposal').first()
    pretransfer_terminated = set()
    if transferred and not points and not evidence:
        from .models import OrdinaryPostRefundTransferRevision
        revision = OrdinaryPostRefundTransferRevision.objects.filter(order=order,
            snapshot__positive_pretransfer_execution_id__isnull=False).order_by('pk').first()
        marker = revision.snapshot.get('positive_pretransfer_execution_id') if revision else None
        if not marker:
            revision = OrdinaryPostRefundTransferRevision.objects.filter(order=order,
                snapshot__zero_pretransfer_execution_id__isnull=False).order_by('pk').first()
            marker = revision.snapshot.get('zero_pretransfer_execution_id') if revision else None
        if marker:
            first_transfer = OrderOriginalShareTransfer.objects.filter(
                previous_segment__attribution__order=order).order_by('created_at', 'pk').first()
            if not first_transfer:
                raise ValidationError('转单前协商缺少真实转单')
            from django.core.exceptions import ValidationError as DjangoValidationError
            try:
                if revision.snapshot.get('positive_pretransfer_execution_id'):
                    from .ordinary_positive_pretransfer import verify as verify_first
                else:
                    from .ordinary_zero_pretransfer import verify as verify_first
                first = verify_first(order, before=first_transfer.created_at)
            except DjangoValidationError as exc:
                raise ValidationError('转单前协商本人收益待核对') from exc
            if not first or first.pk != marker:
                raise ValidationError('转单前协商与资金版本不一致')
            shares = [share for share in first.shares.all() if share.account_id == account_id]
            if shares:
                snapshots = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order,
                    segment__provider_account_id=account_id, segment__accepted_at__lt=first.accepted_at,
                    segment__released_at=first.accepted_at))
                if len(shares) != 1 or len(snapshots) != 1 or snapshots[0].segment_id not in income:
                    raise ValidationError('转单前协商本人原履约段待核对')
                segment_id = snapshots[0].segment_id
                income[segment_id] = shares[0].retained_income
                previous = verified_deductions(order, before=first.created_at)
                included_prior[segment_id] = previous.get(segment_id, 0)
                pretransfer_terminated.add(segment_id)
    if evidence and account_id in evidence.execution.proposal.affected_accounts:
        selected = [item['id'] for item in evidence.snapshot.get('segments', [])
                    if item['id'] in (evidence.execution.proposal.affected_segment_ids or [])
                    and item['account_id'] == account_id and item.get('started_at')]
        closed.update(selected)
        if transferred and not points and selected:
            # A negotiated end closes further service, but its verified
            # retained income remains available to fund a later add-on refund.
            from .negotiation_execution import verify_execution
            from .negotiation_termination import verify_termination_evidence
            from .negotiation_transfer_evidence import verify_locked as verify_transfer
            if evidence.execution.refund_id:
                shares = [row for row in verify_execution(evidence.execution) if row.account_id == account_id]
                verify_termination_evidence(evidence.execution)
            else:
                from .negotiation_zero import verify_zero_execution, verify_zero_termination
                shares = [row for row in verify_zero_execution(evidence.execution) if row.account_id == account_id]
                verify_zero_termination(evidence.execution)
            verify_transfer(evidence.execution)
            if len(shares) != 1:
                raise ValidationError('已终止陪玩保留收益凭证异常')
            previous = verified_deductions(order, before=evidence.execution.created_at)
            from .ordinary_retained_segment_shares import retained, pending_for
            try:
                pending = pending_for({'segment_pending_plan': evidence.snapshot.get('segment_pending_plan')},
                                      account_id, selected)
                resolved = retained(selected, income, previous, shares[0], pending=pending)
            except ValueError as exc:
                raise ValidationError('已终止陪玩逐段保留收益待核对') from exc
            for segment_id, amount in resolved.items():
                income[segment_id] = amount
                included_prior[segment_id] = previous.get(segment_id, 0)
                closed.discard(segment_id)
    terminated_segments = set(closed) | pretransfer_terminated
    if evidence and account_id in evidence.execution.proposal.affected_accounts:
        terminated_segments.update(item['id'] for item in evidence.snapshot.get('segments', [])
            if item['id'] in (evidence.execution.proposal.affected_segment_ids or [])
            and item['account_id'] == account_id and item.get('started_at'))
    for row in OrdinaryTransferFollowupRound.objects.filter(order=order,
            execution__termination_processed_at__isnull=False).order_by('sequence'):
        selected = row.facts.get('segments', {}).get(str(account_id), [])
        if transferred and not points and selected and any(segment in terminated_segments for segment in selected):
            raise ValidationError('追加协商不能重新打开已终止的履约段')
        if transferred and not points:
            terminated_segments.update(selected)
        closed.update(selected)
        if transferred and not points and selected:
            from .ordinary_transfer_followup import _hash
            if row.facts_hash != _hash(row.proposal_id, row.facts):
                raise ValidationError('追加协商后的本人履约段凭证异常')
            if row.execution.refund_id:
                from .negotiation_execution import verify_execution
                from .negotiation_termination import verify_termination_evidence
                shares = [item for item in verify_execution(row.execution) if item.account_id == account_id]
                verify_termination_evidence(row.execution)
            else:
                from .ordinary_transfer_followup_zero import verify_execution, verify_termination
                shares = [item for item in verify_execution(row.execution) if item.account_id == account_id]
                verify_termination(row.execution)
            if len(shares) != 1:
                raise ValidationError('追加协商后的本人保留收益异常')
            previous = verified_deductions(order, before=row.execution.created_at)
            from .ordinary_retained_segment_shares import retained, pending_for
            try:
                pending = pending_for(row.facts, account_id, selected)
                resolved = retained(selected, income, previous, shares[0], pending=pending)
            except ValueError as exc:
                raise ValidationError('追加协商本人逐段保留收益待核对') from exc
            for segment_id, amount in resolved.items():
                income[segment_id] = amount
                included_prior[segment_id] = previous.get(segment_id, 0)
                closed.discard(segment_id)
    used = verified_deductions(order)
    segments = list(order.original_attribution.segments.select_for_update().filter(
        pk__in=income, provider_account_id=account_id).order_by('-accepted_at', '-pk'))
    result = []
    for segment in segments:
        later_used = used.get(segment.pk, 0) - included_prior.get(segment.pk, 0)
        if later_used < 0:
            raise ValidationError('加购退款历史扣减顺序异常')
        available = income[segment.pk] - later_used
        if available < 0:
            raise ValidationError('加购退款扣减超过本人履约段收益')
        if segment.pk not in closed and available:
            result.append((segment.pk, available))
    return result


def _third_remaining_locked(order, account_id):
    """Read this person's verified unpaid segment income after round three."""
    from .models import PointsRefundRoundEvidence
    from .points_third_refund_settlement_plan import build_locked
    from .points_transfer_refund_evidence import _reconstruct_from_verified_facts
    from .aftersale_segment_scope import closed_segments_locked
    from .settlement import SettlementError
    try:
        build_locked(order.pk, funding_only=True, _allow_addons=True)
        version = PointsRefundRoundEvidence.objects.select_related(
            'transfer_revision__first_posting__points_refund').get(order=order)
        transfer = version.transfer_revision
        facts = transfer.snapshot['facts']
        history = _reconstruct_from_verified_facts(order.pk, facts,
            refund_id=transfer.first_posting.points_refund.refund_id, through_transfer_id=transfer.transfer_id)
        closed = closed_segments_locked(order)
        if closed is None:
            raise ValidationError('第三轮后已终止履约段待核对')
        used = verified_deductions(order)
        income = {line[0]: line[4] for line in history.segment_shares if line[1] == account_id}
        rounds = version.snapshot.get('rounds') if isinstance(version.snapshot, dict) else None
        if not isinstance(rounds, list) or len(rounds) != 3:
            raise ValidationError('第三轮本人逐段承担版本缺失')
        for event in rounds:
            for entry in event.get('shares', []):
                if not isinstance(entry, list) or len(entry) < 9:
                    raise ValidationError('第三轮本人逐段承担版本异常')
                if entry[1] != account_id:
                    continue
                for segment_id, amount in entry[8]:
                    if segment_id not in income or type(amount) is not int or amount <= 0:
                        raise ValidationError('第三轮本人主单承担段异常')
                    income[segment_id] -= amount
        segments = list(order.original_attribution.segments.select_for_update().filter(
            pk__in=income, provider_account_id=account_id).order_by('-accepted_at', '-pk'))
        result = []
        for segment in segments:
            remaining = income[segment.pk] - used.get(segment.pk, 0)
            if remaining < 0:
                raise ValidationError('第三轮后加购退款超过本人主单段收益')
            # A confirmed termination cannot be reopened as service; its
            # evidenced retained but unpaid earnings remain the same person's
            # available funds for a separate add-on refund.
            if remaining:
                result.append((segment.pk, remaining))
        return result
    except (SettlementError, KeyError, TypeError, ValueError) as exc:
        raise ValidationError('第三轮后本人主单收益待核对') from exc


def allocate_locked(order, account_id, amount):
    if type(amount) is not int or amount < 0:
        raise ValidationError('主单扣减金额无效')
    remaining, parts = amount, []
    for segment_id, available in remaining_locked(order, account_id):
        take = min(remaining, available)
        if take:
            parts.append((segment_id, take))
            remaining -= take
        if not remaining:
            break
    if remaining:
        raise ValidationError('本人未结案履约段收益不足，请先核对')
    return parts


def verified_deductions(order, *, before=None, include_refund_id=None):
    from .models import OrderAddonRefundParticipant
    participants = OrderAddonRefundParticipant.objects.filter(proposal__payment__intent__order=order,
        proposal__refund__isnull=False, main_pending_deduction__gt=0)
    if before is not None:
        from django.db.models import Q
        cutoff = Q(proposal__refund__created_at__lt=before)
        if include_refund_id is not None:
            cutoff |= Q(proposal__refund_id=include_refund_id, proposal__refund__created_at=before)
        participants = participants.filter(cutoff)
    result = defaultdict(int)
    for participant in participants:
        rows = list(participant.main_segment_deductions.select_related('segment__attribution'))
        if sum(row.amount for row in rows) != participant.main_pending_deduction:
            raise ValidationError('加购退款主单履约段扣减明细不完整')
        for row in rows:
            if row.segment.attribution.order_id != order.pk or row.segment.provider_account_id != participant.account_id:
                raise ValidationError('加购退款主单段归属异常')
            result[row.segment_id] += row.amount
    return dict(result)
