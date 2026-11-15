"""Invalidate unaccepted add-on handovers when their actual target leaves."""
import hashlib
import json

from rest_framework.exceptions import ValidationError
from stores.db import transaction


def digest(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def facts(handover, original_revision, *, transfer=None, posting=None):
    """Read historical facts without taking order or receipt locks."""
    from .models import OrderAddonFundingReleaseRun
    from .points_third_addon_responsibilities import current
    payment, order = handover.payment, handover.payment.intent.order
    if (bool(transfer) == bool(posting) or handover.kind != 'STANDARD' or handover.status != 'SUPERSEDED'
            or handover.accepted_at or OrderAddonFundingReleaseRun.objects.filter(handover=handover).exists()
            or original_revision.order_id != order.pk or original_revision.evidence_hash != digest(original_revision.snapshot)):
        raise ValidationError('接替目标失效的原方案或资金版本异常')
    if transfer:
        when = transfer.created_at
        if (transfer.previous_segment_id != handover.target_segment_id
                or transfer.previous_segment.attribution.order_id != order.pk):
            raise ValidationError('目标转出事件与原方案不一致')
        kind, trigger_id = 'TRANSFER', transfer.pk
    else:
        when = posting.accepted_at
        selected = set(posting.proposal.affected_segment_ids or [])
        if (not when or not posting.termination_processed_at or posting.termination_processed_at < when
                or posting.proposal.case.order_id != order.pk or
                handover.target_segment_id not in selected):
            raise ValidationError('目标协商终止事件与原方案不一致')
        kind, trigger_id = 'TERMINATION', posting.pk
    if (not when or when <= handover.created_at or original_revision.created_at >= handover.created_at
            or not any(row[0] == payment.pk and row[1] == handover.source_segment_id
                       and 0 < handover.source_basis_before <= row[2]
                       for row in original_revision.snapshot.get('pending_handover', []))):
        raise ValidationError('接替失效事件或原待交责任时序异常')
    prior, shares = current(payment, before=when, with_share_id=True)
    active = [row for row in shares if row['is_current']]
    if ((prior.pk if prior else None) != handover.previous_id or len(active) != 1
            or active[0]['segment_id'] != handover.source_segment_id
            or active[0]['basis'] != handover.source_basis_before):
        raise ValidationError('接替目标失效前责任已变化')
    return {'kind': 'POINTS_THIRD_TARGET_INVALIDATION_V1', 'handover_id': handover.pk,
        'payment_id': payment.pk, 'original_revision_id': original_revision.pk,
        'original_revision_hash': original_revision.evidence_hash, 'trigger_kind': kind, 'trigger_id': trigger_id,
        'event_at': when.isoformat(), 'target_segment_id': handover.target_segment_id,
        'decisions': [[d.pk, d.account_id, d.role, d.accepted, d.decided_at.isoformat() if d.decided_at else None]
                      for d in handover.decisions.order_by('pk')],
        'shares': [[s.get('share_id'), s['segment_id'], s['provider_id'], s['basis'], s['income'],
                    s['commission_snapshot'], s['is_current']] for s in shares]}


def _record_locked(order, *, transfer=None, posting=None):
    from .models import (OrderAddonHandover, PointsThirdTransferRevision,
                         PointsThirdHandoverTargetInvalidation)
    if not transaction.get_connection().in_atomic_block or bool(transfer) == bool(posting):
        raise ValidationError('目标失效须在原订单事务处理')
    if transfer:
        if transfer.previous_segment.attribution.order_id != order.pk:
            raise ValidationError('目标转出不属于本订单')
        target_ids = [transfer.previous_segment_id]
    else:
        target_ids = posting.proposal.affected_segment_ids or []
        if not posting.termination_processed_at or posting.proposal.case.order_id != order.pk or not target_ids:
            raise ValidationError('目标协商终止缺少实际履约段')
    pending = list(OrderAddonHandover.objects.select_for_update().filter(
        payment__intent__order=order, target_segment_id__in=target_ids,
        kind='STANDARD', status='PENDING').select_related('payment__intent').order_by('pk'))
    for handover in pending:
        versions = PointsThirdTransferRevision.objects.filter(order=order,
            transfer__previous_segment_id=handover.source_segment_id,
            transfer__created_at__lt=handover.created_at).order_by('-created_at', '-pk')
        original = next((row for row in versions if any(item[0] == handover.payment_id
            and item[1] == handover.source_segment_id for item in row.snapshot.get('pending_handover', []))), None)
        if not original:
            raise ValidationError('原待交责任的真实转单版本缺失')
        handover.status = 'SUPERSEDED'
        handover.save(update_fields=['status'])
        snapshot = facts(handover, original, transfer=transfer, posting=posting)
        PointsThirdHandoverTargetInvalidation.objects.create(handover=handover, original_revision=original,
            transfer=transfer, posting=posting, snapshot=snapshot, evidence_hash=digest(snapshot))
        from site_messages.addon_handover_proposal_notice import refresh_delivered
        refresh_delivered(handover)


def record_transfer_locked(order, transfer):
    return _record_locked(order, transfer=transfer)


def record_termination_locked(order, posting):
    """For a future real, processed later posting; this does not authorize it."""
    return _record_locked(order, posting=posting)


def verify(record):
    expected = facts(record.handover, record.original_revision, transfer=record.transfer, posting=record.posting)
    if record.snapshot != expected or record.evidence_hash != digest(expected):
        raise ValidationError('接替目标失效事实不一致')
    return expected
