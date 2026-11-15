"""Current-read facts for a same-team handover after one main refund.

Read-only evidence; this module does not authorize an endpoint, create a
funding version, release money, or settle a participant.
"""
from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as RestValidationError


def facts(run, *, receipt_id=None):
    from users.receipt_usage_sources import _require_current_unlocked_reads
    from .models import (OrderOriginalShareTransfer, OrderPrestartHandoff, OrderProvider,
                         OrderProviderCompletionFact, PointsNegotiationPosting)
    from .addon_payment import verify_receipt
    from .addon_handover import _income, current_responsibilities
    from .addon_intents import _commission
    from .addon_offers import AddonSelectionError
    from .points_negotiation_execution import verify_participants
    from .points_addon_receipt_usage import payment_facts
    from console.models import AdminAuditLog
    _require_current_unlocked_reads()
    handover = run.handover
    if not handover or not handover.accepted_at or not run.created_at:
        raise ValidationError('主单退款后的同单接替事件缺失')
    payment, order = handover.payment, handover.payment.intent.order
    earlier = list(payment.handovers.filter(version__lt=handover.version).order_by('version', 'pk'))
    if ([row.version for row in earlier] != list(range(1, handover.version))
            or any(row.status not in ('REJECTED', 'ACCEPTED', 'SUPERSEDED') for row in earlier)):
        raise ValidationError('主单退款后的接替重提方案待核对')
    try:
        from .points_team_addon_handover_facts import verify_rejected_attempts
        verify_rejected_attempts(payment, before=handover.created_at)
        from .models import PointsHandoverRefundInvalidation
        from .points_handover_refund_invalidation import _verify_facts as verify_invalidation
        for attempt in (row for row in earlier if row.status == 'SUPERSEDED'):
            record = PointsHandoverRefundInvalidation.objects.filter(handover=attempt).select_related(
                'handover__payment__intent', 'refund').first()
            if not record or record.created_at >= handover.created_at:
                raise ValidationError('主单退款后重提缺少真实失效退款')
            verify_invalidation(record, receipt_id=receipt_id)
    except RestValidationError as exc:
        raise ValidationError('主单退款后的接替拒绝凭证异常') from exc
    postings = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order,
        created_at__lt=handover.created_at).order_by('created_at', 'pk'))
    if (order.required_provider_count != 2 or order.is_gift_snapshot or order.source_order_id
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
                                                          created_at__lte=handover.accepted_at).exists()
            or OrderPrestartHandoff.objects.filter(order=order).exists() or len(postings) != 1
            or order.refund_records.filter(created_at__lte=run.created_at).count() != 1
            or handover.status != 'ACCEPTED'
            or not handover.created_at or handover.created_at >= handover.accepted_at
            or handover.accepted_at >= run.created_at
            or payment.handovers.filter(status='ACCEPTED', accepted_at__lte=run.created_at).count()
               != 1 + sum(row.status == 'ACCEPTED' for row in earlier)
            or payment.refund_records.filter(created_at__gte=handover.created_at,
                                             created_at__lte=handover.accepted_at).exists()
            or payment.refund_earning_deductions.filter(refund__created_at__gte=handover.created_at,
                                                        refund__created_at__lte=handover.accepted_at).exists()):
        raise ValidationError('主单退款后的同单接替时序尚未接入')
    posting = postings[0]
    previous, previous_shares = current_responsibilities(payment, before=handover.created_at)
    if (previous.pk if previous else None) != handover.previous_id:
        raise ValidationError('主单退款后接替的前次责任版本异常')
    if previous:
        from .models import OrderAddonFundingReleaseRun
        from .points_team_addon_handover_facts import has_ordered_funding_chain
        accepted = [row for row in earlier if row.status == 'ACCEPTED']
        prior_runs = list(OrderAddonFundingReleaseRun.objects.filter(handover__in=accepted,
            points_handover_snapshot__kind='SAME_TEAM_V1').select_related('handover')
            .order_by('handover__accepted_at', 'pk'))
        if (not prior_runs or len(prior_runs) != len(accepted) or not has_ordered_funding_chain(prior_runs)
                or prior_runs[-1].handover_id != previous.pk
                or any(not row.handover.accepted_at or row.created_at >= posting.created_at for row in prior_runs)):
            raise ValidationError('主单退款前接替资金版本异常')
        for prior_run in prior_runs:
            verify_event_current(prior_run, receipt_id=receipt_id)
    source, target = handover.source_segment, handover.target_segment
    if (not isinstance(posting.participants, list) or not posting.termination_in_service
            or not posting.termination_processed_at or posting.termination_processed_at > handover.created_at
            or source.attribution.order_id != order.pk or target.attribution.order_id != order.pk
            or (not previous and source.pk != payment.intent.segment_id)
            or source.provider_account_id == target.provider_account_id
            or not source.started_at or not target.started_at or target.started_at > handover.accepted_at
            or source.released_at != posting.accepted_at
            or (target.released_at and target.released_at <= handover.accepted_at)):
        raise ValidationError('主单退款后的原负责人或接替人履约范围异常')
    try:
        verify_receipt(payment)
        if receipt_id and OrderOriginalShareTransfer.objects.filter(
                previous_segment__attribution__order=order).exists():
            # Receipt-local replay must not reacquire the order or money lock.
            from .points_pretransfer_refund_source import verify_participants_locked
            verify_participants_locked(posting, receipt_id=receipt_id)
        else:
            verify_participants(posting)
        rate = _commission(target, lock=False)
    except (RestValidationError, AddonSelectionError) as exc:
        raise ValidationError('主单退款后的付款、履约或抽成凭证异常') from exc
    shares = list(posting.shares.filter(account_id=source.provider_account_id))
    assignments = list(OrderProvider.objects.filter(order=order,
        provider_account_id__in=[source.provider_account_id, target.provider_account_id]))
    if (len(shares) != 1 or len(assignments) != 2
            or shares[0].assignment.released_at != posting.accepted_at
            or any(item.settled_at and item.settled_at <= handover.accepted_at for item in assignments)
            or OrderProviderCompletionFact.objects.filter(order=order,
                provider_account_id__in=[source.provider_account_id, target.provider_account_id],
                completed_at__lte=handover.accepted_at).exists()):
        raise ValidationError('主单退款后的人员或结算状态异常')
    prior = list(payment.refund_records.filter(created_at__lt=handover.created_at).order_by('created_at', 'pk'))
    deductions = list(payment.refund_earning_deductions.filter(refund__created_at__lt=handover.created_at)
        .select_related('refund', 'participant', 'segment__attribution').order_by('refund__created_at', 'pk'))
    active = [row for row in previous_shares if row['is_current']]
    if len(active) != 1 or active[0]['segment_id'] != source.pk:
        raise ValidationError('主单退款后接替的当前负责人异常')
    basis, income = active[0]['basis'], active[0]['income']
    if (basis <= 0 or not 0 <= income <= basis or handover.source_basis_before != basis
            or handover.retained_basis + handover.transferred_basis != basis or handover.transferred_basis <= 0
            or not handover.remaining_work or (handover.retained_basis and not handover.completed_work)
            or (not previous and any(row.refund.order_id != order.pk or row.segment_id != source.pk
                   or row.segment.attribution.order_id != order.pk
                   or row.participant.account_id != source.provider_account_id for row in deductions))):
        raise ValidationError('主单退款后的加购剩余份额异常')
    expected = [(row['segment_id'], row['basis'], row['income'], row['commission_snapshot'], False)
                for row in previous_shares if not row['is_current']]
    if handover.retained_basis:
        commission = active[0]['commission_snapshot']
        original = _income(basis, commission)
        retained = (_income(handover.retained_basis, commission) if income == original
                    else income * handover.retained_basis // basis)
        expected.append((source.pk, handover.retained_basis, retained, commission, False))
    expected.append((target.pk, handover.transferred_basis, _income(handover.transferred_basis, rate), rate, True))
    rows = list(handover.shares.select_related('segment').order_by('position'))
    if ([(row.segment_id, row.basis, row.income, row.commission_snapshot, row.is_current) for row in rows] != expected
            or [row.position for row in rows] != list(range(len(rows)))):
        raise ValidationError('主单退款后的接替逐段收益异常')
    decisions = list(handover.decisions.order_by('account_id').values_list('account_id', 'role', 'accepted', 'decided_at'))
    expected_decisions = {(payment.intent.buyer_id, 'BOSS', True),
        (source.provider_account_id, 'PROVIDER', True), (target.provider_account_id, 'PROVIDER', True)}
    if (len(decisions) != 3 or {(a, role, yes) for a, role, yes, _ in decisions} != expected_decisions
            or any(not at or at < handover.created_at for _, _, _, at in decisions)
            or max(at for _, _, _, at in decisions) != handover.accepted_at):
        raise ValidationError('主单退款后的接替三方确认异常')
    path = f'/api/admin/orders/{order.pk}/addons/payments/{payment.pk}/handovers/'
    audits = list(AdminAuditLog.objects.filter(resource='addon-handover', object_id=str(handover.pk),
                                               path=path, method='POST', status_code=200))
    body = {'order_id': order.pk, 'payment_id': payment.pk, 'source_provider': source.provider_account_id,
            'target_provider': target.provider_account_id, 'retained_basis': handover.retained_basis,
            'completed_work': handover.completed_work, 'remaining_work': handover.remaining_work}
    if len(audits) != 1 or audits[0].operator_account_id != handover.created_by_id or audits[0].request_body != body:
        raise ValidationError('主单退款后的接替客服审计异常')
    # Bind every earlier post-main add-on event, including zero-release runs.
    # A shared-receipt reader uses current reads only and never takes an order lock.
    from .models import OrderAddonHandover, OrderAddonRefund, OrderAddonFundingReleaseRun
    prior_handovers = list(OrderAddonHandover.objects.filter(payment__intent__order=order,
        status='ACCEPTED', accepted_at__gt=posting.created_at, accepted_at__lt=handover.accepted_at)
        .exclude(pk=handover.pk))
    prior_refunds = list(OrderAddonRefund.objects.filter(order=order, created_at__gt=posting.created_at,
                                                        created_at__lt=handover.accepted_at))
    if (OrderAddonHandover.objects.filter(payment__intent__order=order, status='ACCEPTED',
            accepted_at=handover.accepted_at).exclude(pk=handover.pk).exists()
            or OrderAddonRefund.objects.filter(order=order, created_at=handover.accepted_at).exists()):
        raise ValidationError('主单退款后的接替与其他事件时间无法区分')
    events = [(row.accepted_at, 'handover', row) for row in prior_handovers]
    events += [(row.created_at, 'refund', row) for row in prior_refunds]
    if len({row[0] for row in events}) != len(events):
        raise ValidationError('主单退款后的加购事件时间无法区分')
    previous_at, prior_events = posting.created_at, []
    for event_at, kind, event in sorted(events, key=lambda row: row[0]):
        prior_run = (OrderAddonFundingReleaseRun.objects.filter(handover=event).first() if kind == 'handover'
                     else OrderAddonFundingReleaseRun.objects.filter(refund=event).first())
        if (not prior_run or event_at <= previous_at or prior_run.created_at < event_at
                or prior_run.created_at >= handover.accepted_at):
            raise ValidationError('主单退款后的此前加购资金版本异常')
        verify_event_current(prior_run, receipt_id=receipt_id)
        event_hash = prior_run.points_handover_hash if kind == 'handover' else prior_run.points_addon_hash
        prior_events.append([kind, event.pk, prior_run.pk, event_hash])
        previous_at = prior_run.created_at
    snapshot = run.refunded_main_snapshot
    if (not isinstance(snapshot, dict) or snapshot.get('posting_id') != posting.pk
            or not isinstance(snapshot.get('funding'), dict)
            or snapshot['funding'].get('required_units') != run.required_amount):
        raise ValidationError('主单退款后的接替资金版本缺失')
    result = {'kind': 'SAME_TEAM_V1', 'posting_id': posting.pk,
        'handover_id': handover.pk, 'payment': payment_facts(payment, read_receipt_id=receipt_id),
        'accepted_at': handover.accepted_at.isoformat(),
        'decisions': [[a, role, yes, at.isoformat()] for a, role, yes, at in decisions],
        'shares': [[row.pk, row.segment_id, row.basis, row.income, row.commission_snapshot, row.is_current]
                   for row in rows],
        'prior_refunds': [[row.pk, row.amount, row.created_at.isoformat()] for row in prior],
        'prior_deductions': [[row.pk, row.refund_id, row.segment_id, row.participant.account_id, row.amount]
                             for row in deductions], 'funding': snapshot['funding'], 'prior_events': prior_events}
    if previous:
        result['previous_handover_id'] = previous.pk
        result['previous_funding_event'] = [prior_runs[-1].pk, prior_runs[-1].points_handover_hash]
    return result


def require_source(order, source_segment_id, target_segment_id):
    """Check the one terminated source and the distinct continuing target."""
    from .models import OrderParticipationSegment, PointsNegotiationPosting
    postings = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order)
                    .order_by('created_at', 'pk'))
    source = OrderParticipationSegment.objects.filter(pk=source_segment_id, attribution__order=order).first()
    target = OrderParticipationSegment.objects.filter(pk=target_segment_id, attribution__order=order).first()
    if (len(postings) != 1 or not source or not target or source.pk == target.pk
            or source.provider_account_id == target.provider_account_id
            or not postings[0].termination_in_service or not postings[0].termination_processed_at
            or source.released_at != postings[0].accepted_at or not source.started_at
            or not postings[0].shares.filter(account_id=source.provider_account_id,
                                            assignment__released_at=postings[0].accepted_at).exists()
            or target.released_at or not target.started_at
            or not order.providers.filter(provider_account_id=target.provider_account_id,
                                          released_at__isnull=True, settled_at__isnull=True).exists()):
        raise ValidationError('接替须由已终止主单陪玩的未完成加购交给当前履约陪玩')
    return postings[0]


def verify_event_current(run, *, receipt_id=None):
    """Verify a prior event without acquiring an order or another receipt lock."""
    from .addon_funding_release import verify_run
    if run.refunded_main_snapshot is None:
        return verify_run(run, _skip_usage=True, witness_receipt_id=receipt_id)
    if receipt_id is None:
        return verify_run(run, _skip_usage=True)
    from types import SimpleNamespace
    from .models import OrderPointsFundingAllocation
    from users.receipt_usage_sources import after_main_addon_release_facts
    if not isinstance(run.allocation_snapshot, list):
        raise ValidationError('此前加购资金分配快照异常')
    if not run.allocation_snapshot:
        # A later real receipt can witness the historical empty snapshot;
        # it was not an allocation at the earlier event's time.
        return verify_run(run, _skip_usage=True, witness_receipt_id=receipt_id)
    original = OrderPointsFundingAllocation.objects.filter(pk=run.allocation_snapshot[0][0]).first()
    if not original:
        raise ValidationError('此前加购资金版本的原分配缺失')
    return after_main_addon_release_facts(SimpleNamespace(run=run, allocation=original, pk=0, amount=0),
                                         witness_receipt_id=receipt_id)
