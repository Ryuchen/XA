"""Order-locked facts for a possible handover after two points refunds.

This module does not grant permission, create a handover, or release money.
"""
from contextlib import contextmanager
from contextvars import ContextVar

from rest_framework.exceptions import ValidationError
from stores.db import transaction

_refunded_reproposal = ContextVar('points_refunded_addon_reproposal', default=None)


def refunded_reproposal_active(order, payment):
    state = _refunded_reproposal.get()
    return bool(transaction.get_connection().in_atomic_block and state
                and state == (transaction.get_connection().alias, order.pk, payment.pk))


@contextmanager
def refunded_reproposal_scope(order, payment, facts, handover=None):
    """Authorize only the unchanged remainder of a refunded 0280 handover."""
    if order.payment_status != order.PaymentStatus.REFUNDED:
        yield
        return
    from .models import OrderPointsTransferFollowupFundingRevision, PointsHandoverRefundInvalidation
    revision = OrderPointsTransferFollowupFundingRevision.objects.filter(pk=facts['followup_revision_id'],
        posting__points_refund__redemption__order=order, evidence_hash=facts['followup_revision_hash']).first()
    snapshot = revision.pending_handover_snapshot if revision else None
    matches = [item for item in snapshot if isinstance(item, dict) and item.get('payment_id') == payment.pk
               and item.get('source_segment_id') == facts['source_segment_id']
               and item.get('target_segment_id') == facts['target_segment_id']] if isinstance(snapshot, list) else []
    # The original 0280 proposal has a separate, already authorized path.
    if len(matches) != 1 or (handover and handover.pk == matches[0].get('handover_id')):
        yield
        return
    origin_id = matches[0]['handover_id']
    invalidation = PointsHandoverRefundInvalidation.objects.filter(handover_id=origin_id,
        handover__payment=payment, refund__order=order,
        refund__created_at__gt=revision.posting.created_at).select_related(
            'handover__payment__intent', 'refund__payment__intent').first()
    if not invalidation or facts['remaining_basis'] <= 0:
        raise ValidationError('全退主单后重提缺少加购退款及剩余责任')
    _verify_invalidation(invalidation, revision.posting.created_at)
    token = _refunded_reproposal.set((transaction.get_connection().alias, order.pk, payment.pk))
    try:
        yield
    finally:
        _refunded_reproposal.reset(token)


def source_locked(order, payment, target_account_id, *, pending_handover_id=None, require_active_source=False):
    from django.db.models import Q
    from .addon_handover import current_responsibilities
    from .models import (OrderAddonHandover, OrderPointsTransferFollowupFundingRevision,
                         OrderProvider, OrderProviderCompletionFact, OrderParticipationSegment,
                         PointsNegotiationPosting, PointsHandoverRefundInvalidation, OrderPointsFundingAllocation)
    from .points_transfer_followup_funding import plan_locked
    from .points_transfer_followup_termination import verify_closed_or_settled_locked
    from .points_transfer_refund_access import permits
    if (not transaction.get_connection().in_atomic_block or not permits(order, phase='refund')
            or payment.intent.order_id != order.pk or order.status != order.Status.IN_SERVICE
            or order.refund_records.count() != 2 or order.settled_at
            or type(target_account_id) is not int or target_account_id <= 0):
        raise ValidationError('两次主单退款后的加购接替范围尚未开放')
    postings = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order)
                    .select_related('proposal__points_terms').order_by('created_at', 'pk'))
    if len(postings) != 2:
        raise ValidationError('两次主单退款的真实凭证缺失')
    first, second = postings
    terms = getattr(second.proposal, 'points_terms', None)
    from .models import PointsFollowupTransferRevision
    post_transfer = PointsFollowupTransferRevision.objects.filter(order=order).first()
    revision = OrderPointsTransferFollowupFundingRevision.objects.filter(posting=second).first()
    frozen = (revision.pending_handover_snapshot if revision and
              isinstance(revision.pending_handover_snapshot, list) else [])
    frozen_pending = [item for item in frozen if isinstance(item, dict) and item.get('payment_id') == payment.pk]
    if frozen_pending:
        from .points_transfer_followup_pending import verify_snapshot
        verify_snapshot(second, frozen)
        if len(frozen_pending) != 1:
            raise ValidationError('退款前的待接替付款责任不唯一')
        origin_id = frozen_pending[0].get('handover_id')
        if origin_id != pending_handover_id:
            from .models import PointsHandoverRefundInvalidation
            invalidation = PointsHandoverRefundInvalidation.objects.filter(handover_id=origin_id,
                handover__payment=payment, refund__order=order, refund__created_at__gt=second.created_at)
            invalidation = invalidation.select_related('handover__payment__intent', 'refund__payment__intent').first()
            if not invalidation:
                raise ValidationError('重提接替缺少原方案退款失效凭证')
            _verify_invalidation(invalidation, second.created_at)
    if (not terms or terms.prior_refund_id != first.points_refund_id
            or not isinstance(first.participants, (dict, list))
            or (isinstance(first.participants, list) and not post_transfer and len(frozen_pending) != 1)
            or not first.termination_processed_at or not second.termination_processed_at
            or first.created_at >= second.created_at or second.completed_by_negotiation
            or not second.termination_in_service):
        raise ValidationError('两次主单退款的履约终止顺序异常')
    paid = None
    if order.providers.filter(settled_at__isnull=False).exists():
        from .models import OrderSegmentSettlementManifest
        from .points_transfer_refund_evidence import _prior_settlement, _verify_prior_settlement
        from .segment_addon_settlement_revision import verify_revisions
        paid = _prior_settlement(order)
        manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
        if not paid or not manifest or manifest.finalized_at or paid.get('manifest_id') != manifest.pk:
            raise ValidationError('加购接替前已付清单待核对')
        addons = manifest.plan.get('points_funding', {}).get('addons', []) if isinstance(manifest.plan, dict) else None
        if (not isinstance(addons, list) or not any(isinstance(row, dict) and row.get('payment_id') == payment.pk
                                                    for row in addons)
                or manifest.paid_handover_events.filter(settlement_revision__isnull=True).exists()):
            raise ValidationError('加购付款或此前接替尚未进入已付清单')
        verify_revisions(manifest)
        _verify_prior_settlement(order, paid)
    verify_closed_or_settled_locked(second)
    plan_locked(order.pk, allow_recorded=bool(paid), allow_in_service=True)
    if not revision or not isinstance(revision.allocations, list):
        raise ValidationError('第二次退款的资金版本缺失')
    previous, shares = current_responsibilities(payment)
    active = [row for row in shares if row['is_current'] and row['basis'] > 0]
    if frozen_pending:
        item = frozen_pending[0]
        if (len(active) != 1 or item.get('source_segment_id') != active[0]['segment_id']
                or active[0]['basis'] > item.get('source_basis', -1)
                or not OrderParticipationSegment.objects.filter(pk=item.get('target_segment_id'),
                    attribution__order=order, provider_account_id=target_account_id).exists()):
            raise ValidationError('退款前待接替责任或目标已变化')
    if post_transfer:
        from .points_followup_transfer_revision import verify_locked
        verify_locked(post_transfer)
        frozen = post_transfer.snapshot.get('pending_handover', [])
        matches = [item for item in frozen if isinstance(item, list) and len(item) == 3
                   and item[0] == payment.pk and len(active) == 1 and item[1] == active[0]['segment_id']] if isinstance(frozen, list) else []
        if (not isinstance(frozen, list) or len(matches) != 1 or len(active) != 1
                or not 0 < active[0]['basis'] <= matches[0][2]
                or post_transfer.transfer.next_segment.provider_account_id != target_account_id):
            raise ValidationError('加购不属于该次真实转单的待接替责任')
        if active[0]['basis'] != matches[0][2]:
            from .points_transfer_followup_addon_version import latest_run_locked
            if not latest_run_locked(order, post_transfer.followup_revision):
                raise ValidationError('退款后的接替责任缺少真实资金事件')
    if paid and (len(active) != 1 or {active[0]['provider_id'], target_account_id}.intersection(
            row['account_id'] for row in paid['people'])):
        raise ValidationError('已结算陪玩不能参与加购接替')
    existing = list(payment.handovers.order_by('version'))
    accepted = [row for row in existing if row.status == 'ACCEPTED']
    pending = [row for row in existing if row.status == 'PENDING']
    prior = None
    for index, row in enumerate(existing):
        if (row.version != index + 1 or row.previous_id != (prior.pk if prior else None)
                or not row.created_at or row.created_at == second.created_at
                or (prior and (not prior.accepted_at or prior.accepted_at >= row.created_at))):
            raise ValidationError('加购接替方案版本顺序异常')
        if row.status == 'REJECTED':
            boundary = existing[index + 1].created_at if index + 1 < len(existing) else None
            if row.created_at < second.created_at and (boundary is None or second.created_at < boundary):
                boundary = second.created_at
            _verify_rejected(payment, row, before=boundary)
        elif row.status == 'SUPERSEDED':
            record = PointsHandoverRefundInvalidation.objects.filter(handover=row).select_related(
                'handover__payment__intent', 'refund__payment__intent').first()
            witness = OrderPointsFundingAllocation.objects.filter(
                Q(redemption__order=order) | Q(service_order=order)).order_by('pk').first()
            if not record:
                raise ValidationError('失效接替缺少真实退款凭证')
            _verify_invalidation(record, second.created_at, receipt_id=witness.receipt_id if witness else None)
            boundary = existing[index + 1].created_at if index + 1 < len(existing) else None
            run = getattr(record.refund, 'funding_release_run', None)
            if boundary and (not run or run.created_at >= boundary):
                raise ValidationError('重发接替早于退款释放事件')
        elif row.status == 'ACCEPTED':
            if not row.accepted_at or row.accepted_at == second.created_at:
                raise ValidationError('加购接替确认时间异常')
            prior = row
        elif row.status != 'PENDING' or index != len(existing) - 1:
            raise ValidationError('加购接替方案状态异常')
    if (len(active) != 1 or active[0]['provider_id'] == target_account_id
            or len(pending) > 1
            or (previous.pk if previous else None) != (accepted[-1].pk if accepted else None)
            or (pending_handover_id is None and pending)
            or (pending_handover_id is not None and (len(pending) != 1 or pending[0].pk != pending_handover_id
                or pending[0].version != len(existing)
                or pending[0].previous_id != (previous.pk if previous else None)
                or pending[0].source_segment_id != active[0]['segment_id']
                or pending[0].source_basis_before != active[0]['basis']
                or pending[0].target_segment.provider_account_id != target_account_id))):
        raise ValidationError('加购负责人或接替人不符合要求')
    target_rows = list(OrderParticipationSegment.objects.filter(attribution__order=order,
        provider_account_id=target_account_id, started_at__isnull=False, released_at__isnull=True))
    source_rows = list(OrderParticipationSegment.objects.filter(pk=active[0]['segment_id'],
        attribution__order=order, provider_account_id=active[0]['provider_id'],
        started_at__isnull=False, released_at__isnull=True))
    if ((require_active_source and (len(source_rows) != 1 or not OrderProvider.objects.filter(order=order,
            provider_account_id=active[0]['provider_id'], released_at__isnull=True, settled_at__isnull=True).exists()
            or OrderProviderCompletionFact.objects.filter(order=order,
                provider_account_id=active[0]['provider_id'], segment_id=active[0]['segment_id']).exists()))
            or len(target_rows) != 1 or not OrderProvider.objects.filter(order=order,
            provider_account_id=target_account_id, released_at__isnull=True).exists()
            or OrderProviderCompletionFact.objects.filter(order=order,
                provider_account_id=target_account_id, segment_id=target_rows[0].pk).exists()):
        raise ValidationError('接替人须为本单仍在履约的陪玩')
    return {'followup_revision_id': revision.pk, 'followup_revision_hash': revision.evidence_hash,
            'payment_id': payment.pk, 'previous_handover_id': previous.pk if previous else None,
            'source_segment_id': active[0]['segment_id'], 'source_provider_id': active[0]['provider_id'],
            'remaining_basis': active[0]['basis'], 'remaining_income': active[0]['income'],
            'target_segment_id': target_rows[0].pk, 'target_provider_id': target_account_id}


def _verify_invalidation(record, second_at, *, receipt_id=None):
    """Dispatch by the actual refund event, preserving both evidence formats."""
    from .models import OrderAddonFundingReleaseRun
    from django.core.exceptions import ValidationError as DjangoValidationError
    if record.handover.created_at < second_at and record.refund.created_at < second_at:
        from .points_handover_refund_invalidation import verify_current_read, verify_order_locked
        run = OrderAddonFundingReleaseRun.objects.filter(refund=record.refund).first()
        if (record.created_at >= second_at
                or not run or run.created_at >= second_at):
            raise ValidationError('第二次退款前的失效事件或到账见证异常')
        try:
            return (verify_current_read(record, receipt_id=receipt_id) if receipt_id
                    else verify_order_locked(record))
        except DjangoValidationError as exc:
            raise ValidationError('第二次退款前的失效退款凭证异常') from exc
    from .points_followup_handover_invalidation import verify
    return verify(record, receipt_id=receipt_id)


def _verify_rejected(payment, handover, *, before=None, pending=False):
    """Verify an unaccepted attempt without treating it as a transfer."""
    from .addon_handover import current_responsibilities, _income
    from .addon_intents import _commission
    from .addon_offers import AddonSelectionError
    from .models import OrderAddonFundingReleaseRun
    from console.models import AdminAuditLog
    order, source, target = payment.intent.order, handover.source_segment, handover.target_segment
    if (handover.payment_id != payment.pk or handover.status != ('PENDING' if pending else 'REJECTED') or handover.accepted_at
            or not handover.created_at or source.attribution.order_id != order.pk
            or target.attribution.order_id != order.pk or source.provider_account_id == target.provider_account_id
            or OrderAddonFundingReleaseRun.objects.filter(handover=handover).exists()):
        raise ValidationError('已拒绝接替的来源异常')
    previous, shares = current_responsibilities(payment, before=handover.created_at)
    active = [row for row in shares if row['is_current'] and row['basis'] > 0]
    if (len(active) != 1 or active[0]['segment_id'] != source.pk
            or (previous.pk if previous else None) != handover.previous_id
            or active[0]['basis'] != handover.source_basis_before
            or handover.retained_basis + handover.transferred_basis != handover.source_basis_before
            or not handover.transferred_basis or not handover.remaining_work
            or (handover.retained_basis and not handover.completed_work)):
        raise ValidationError('已拒绝接替的原责任异常')
    parties = {(payment.intent.buyer_id, 'BOSS'), (source.provider_account_id, 'PROVIDER'),
               (target.provider_account_id, 'PROVIDER')}
    decisions = list(handover.decisions.values_list('account_id', 'role', 'accepted', 'decided_at'))
    rejected = [row[3] for row in decisions if row[2] is False]
    rejected_at = rejected[0] if len(rejected) == 1 else None
    if (len(decisions) != 3 or {(row[0], row[1]) for row in decisions} != parties
            or (pending and (rejected or all(row[2] is True for row in decisions)))
            or (not pending and (len(rejected) != 1 or not rejected_at or rejected_at < handover.created_at
                                 or (before is not None and rejected_at >= before)))
            or any((row[2] is None) != (row[3] is None) or (row[3] and
                   (row[3] < handover.created_at or (rejected_at and row[3] > rejected_at))) for row in decisions)):
        raise ValidationError('已拒绝接替的确认记录异常')
    try:
        target_snapshot = _commission(target, lock=False)
    except AddonSelectionError as exc:
        raise ValidationError('已拒绝接替的抽成快照异常') from exc
    expected = [(row['segment_id'], row['basis'], row['income'], row['commission_snapshot'], False)
                for row in shares if not row['is_current']]
    original = active[0]
    if handover.retained_basis:
        full = _income(original['basis'], original['commission_snapshot'])
        retained = (_income(handover.retained_basis, original['commission_snapshot'])
                    if original['income'] == full else
                    original['income'] * handover.retained_basis // original['basis'])
        expected.append((source.pk, handover.retained_basis, retained, original['commission_snapshot'], False))
    expected.append((target.pk, handover.transferred_basis,
                     _income(handover.transferred_basis, target_snapshot), target_snapshot, True))
    rows = list(handover.shares.order_by('position'))
    if ([row.position for row in rows] != list(range(len(rows))) or
            [(row.segment_id, row.basis, row.income, row.commission_snapshot, row.is_current) for row in rows] != expected):
        raise ValidationError('已拒绝接替的份额快照异常')
    path = f'/api/admin/orders/{order.pk}/addons/payments/{payment.pk}/handovers/'
    audits = list(AdminAuditLog.objects.filter(resource='addon-handover', object_id=str(handover.pk),
                                               path=path, method='POST', status_code=200))
    body = {'order_id': order.pk, 'payment_id': payment.pk, 'source_provider': source.provider_account_id,
            'target_provider': target.provider_account_id, 'retained_basis': handover.retained_basis,
            'completed_work': handover.completed_work, 'remaining_work': handover.remaining_work}
    if len(audits) != 1 or audits[0].operator_account_id != handover.created_by_id or audits[0].request_body != body:
        raise ValidationError('已拒绝接替的客服审计异常')


def verify_event(payment, handover, *, before=None, receipt_id=None):
    """Rebuild the entire accepted chain without acquiring order locks."""
    chain = list(payment.handovers.filter(version__lte=handover.version)
                 .select_related('source_segment__attribution', 'target_segment__attribution').order_by('version'))
    if (not chain or chain[-1].pk != handover.pk
            or [row.version for row in chain] != list(range(1, handover.version + 1))):
        raise ValidationError('加购接替版本链不连续')
    from .models import PointsNegotiationPosting
    postings = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=payment.intent.order)
                    .order_by('created_at', 'pk'))
    if len(postings) != 2 or not handover.accepted_at or handover.accepted_at <= postings[1].created_at:
        raise ValidationError('加购接替与第二次主单退款顺序异常')
    second_at = postings[1].created_at
    if handover.created_at < second_at:
        from .models import OrderPointsTransferFollowupFundingRevision
        from .points_transfer_followup_pending import verify_snapshot
        revision = OrderPointsTransferFollowupFundingRevision.objects.filter(posting=postings[1]).first()
        frozen = revision.pending_handover_snapshot if revision else None
        if (not isinstance(frozen, list) or not any(isinstance(item, dict)
                and item.get('handover_id') == handover.pk and item.get('payment_id') == payment.pk for item in frozen)):
            raise ValidationError('第二次退款缺少原待接替方案快照')
        verify_snapshot(postings[1], frozen, receipt_id=receipt_id)
    rows = None
    for index, event in enumerate(chain):
        boundary = chain[index + 1].created_at if index + 1 < len(chain) else before
        if event.status == 'REJECTED':
            if event.created_at < second_at and (boundary is None or second_at < boundary):
                boundary = second_at
            _verify_rejected(payment, event, before=boundary)
            continue
        if event.status == 'SUPERSEDED':
            from .models import PointsHandoverRefundInvalidation, OrderAddonFundingReleaseRun
            record = PointsHandoverRefundInvalidation.objects.filter(handover=event).select_related(
                'handover__payment__intent', 'refund__payment__intent').first()
            if not record:
                raise ValidationError('失效接替缺少退款凭证')
            _verify_invalidation(record, second_at, receipt_id=receipt_id)
            run = OrderAddonFundingReleaseRun.objects.filter(refund=record.refund).first()
            if boundary and (not run or run.created_at >= boundary):
                raise ValidationError('接替重发早于退款释放')
            continue
        if event.status != 'ACCEPTED' or not event.accepted_at or event.accepted_at == second_at:
            raise ValidationError('加购接替与第二次主单退款时序无法区分')
        if event.accepted_at < second_at:
            from .addon_handover import verify_handover
            rows = verify_handover(payment, event, as_of=boundary, receipt_id=receipt_id)
        else:
            rows = _verify_event_one(payment, event, before=boundary)
    return rows


def _verify_event_one(payment, handover, *, before=None):
    from .addon_handover import current_responsibilities, _income
    from .addon_intents import _commission
    from .addon_offers import AddonSelectionError
    from .addon_payment import verify_receipt
    from .models import OrderProvider, OrderProviderCompletionFact
    from console.models import AdminAuditLog
    order, accepted = payment.intent.order, handover.accepted_at
    source, target = handover.source_segment, handover.target_segment
    if (handover.payment_id != payment.pk or handover.status != 'ACCEPTED'
            or handover.version < 1 or not handover.created_at or not accepted
            or handover.created_at >= accepted or (before is not None and accepted >= before)
            or source.attribution.order_id != order.pk
            or target.attribution.order_id != order.pk or source.provider_account_id == target.provider_account_id
            or not source.started_at or source.started_at > handover.created_at
            or not target.started_at or target.started_at > handover.created_at
            or (handover.kind == 'VOLUNTARY' and source.released_at and source.released_at <= accepted)
            or (target.released_at and target.released_at <= accepted)
            or (order.completed_at and order.completed_at <= accepted)
            or payment.refund_records.filter(created_at__gte=handover.created_at, created_at__lte=accepted).exists()
            or payment.refund_earning_deductions.filter(refund__created_at__gte=handover.created_at,
                refund__created_at__lte=accepted).exists()):
        raise ValidationError('二次退款后的加购接替时序或归属异常')
    verify_receipt(payment)
    previous, shares = current_responsibilities(payment, before=handover.created_at)
    active = [row for row in shares if row['is_current'] and row['basis'] > 0]
    if (len(active) != 1 or active[0]['segment_id'] != source.pk
            or (previous.pk if previous else None) != handover.previous_id
            or (previous is None and source.pk != payment.intent.segment_id)
            or (previous is not None and (handover.version <= previous.version
                or previous.accepted_at >= handover.created_at))
            or active[0]['basis'] != handover.source_basis_before
            or handover.retained_basis + handover.transferred_basis != handover.source_basis_before
            or not handover.transferred_basis or not handover.remaining_work
            or (handover.retained_basis and not handover.completed_work)):
        raise ValidationError('二次退款后的加购剩余责任异常')
    if previous:
        from .models import OrderAddonFundingReleaseRun, PointsNegotiationPosting
        from .points_transfer_followup_addon_version import digest
        postings = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order)
                        .order_by('created_at', 'pk'))
        run = OrderAddonFundingReleaseRun.objects.filter(handover=previous).first()
        if (len(postings) != 2 or not run or run.created_at < previous.accepted_at
                or run.created_at >= handover.created_at):
            raise ValidationError('此前接替的资金版本缺失或时序异常')
        if previous.accepted_at > postings[1].created_at:
            if (not isinstance(run.points_followup_snapshot, dict)
                    or run.points_followup_hash != digest(run, run.points_followup_snapshot)
                    or run.points_followup_snapshot.get('kind') != 'TRANSFER_FOLLOWUP_V1'):
                raise ValidationError('此前接替的后续资金版本异常')
        elif (previous.accepted_at == postings[1].created_at or
              (not isinstance(run.points_handover_snapshot, dict)
               and not isinstance(run.refunded_main_snapshot, dict))):
            raise ValidationError('第二次退款前的接替资金版本缺失')
    assignment = OrderProvider.objects.filter(order=order, provider_account_id=target.provider_account_id).first()
    source_assignment = OrderProvider.objects.filter(order=order, provider_account_id=source.provider_account_id).first()
    if (not assignment or (assignment.released_at and assignment.released_at <= accepted)
            or (assignment.settled_at and assignment.settled_at <= accepted)
            or OrderProviderCompletionFact.objects.filter(order=order, provider_account_id=target.provider_account_id,
                segment_id=target.pk, completed_at__lte=accepted).exists()
            or (handover.kind == 'VOLUNTARY' and (not source_assignment
                or (source_assignment.released_at and source_assignment.released_at <= accepted)
                or (source_assignment.settled_at and source_assignment.settled_at <= accepted)
                or OrderProviderCompletionFact.objects.filter(order=order, provider_account_id=source.provider_account_id,
                    segment_id=source.pk, completed_at__lte=accepted).exists()))):
        raise ValidationError('接替人当时未在本单继续履约')
    expected = {(payment.intent.buyer_id, 'BOSS', True), (source.provider_account_id, 'PROVIDER', True),
                (target.provider_account_id, 'PROVIDER', True)}
    decisions = list(handover.decisions.values_list('account_id', 'role', 'accepted', 'decided_at'))
    if (len(decisions) != 3 or {(row[0], row[1], row[2]) for row in decisions} != expected
            or any(not row[3] or row[3] < handover.created_at for row in decisions)
            or max(row[3] for row in decisions) != accepted):
        raise ValidationError('加购接替缺少三方确认')
    try:
        target_commission = _commission(target, lock=False)
    except AddonSelectionError as exc:
        raise ValidationError('接替人抽成快照异常') from exc
    source_share = active[0]
    expected_rows = [(row['segment_id'], row['basis'], row['income'], row['commission_snapshot'], False)
                     for row in shares if not row['is_current']]
    if handover.retained_basis:
        full = _income(source_share['basis'], source_share['commission_snapshot'])
        retained = (_income(handover.retained_basis, source_share['commission_snapshot'])
                    if source_share['income'] == full else
                    source_share['income'] * handover.retained_basis // source_share['basis'])
        expected_rows.append((source.pk, handover.retained_basis, retained, source_share['commission_snapshot'], False))
    expected_rows.append((target.pk, handover.transferred_basis,
                          _income(handover.transferred_basis, target_commission), target_commission, True))
    rows = list(handover.shares.order_by('position'))
    if ([(row.segment_id, row.basis, row.income, row.commission_snapshot, row.is_current) for row in rows]
            != expected_rows or [row.position for row in rows] != list(range(len(rows)))):
        raise ValidationError('加购接替的逐段收益快照异常')
    path = f'/api/admin/orders/{order.pk}/addons/payments/{payment.pk}/handovers/'
    audits = list(AdminAuditLog.objects.filter(resource='addon-handover', object_id=str(handover.pk),
                                               path=path, method='POST', status_code=200))
    body = {'order_id': order.pk, 'payment_id': payment.pk, 'source_provider': source.provider_account_id,
            'target_provider': target.provider_account_id, 'retained_basis': handover.retained_basis,
            'completed_work': handover.completed_work, 'remaining_work': handover.remaining_work}
    if len(audits) != 1 or audits[0].operator_account_id != handover.created_by_id or audits[0].request_body != body:
        raise ValidationError('加购接替客服审计异常')
    return rows
