"""Immutable pending add-on responsibilities at a real main-refund event."""
from contextlib import contextmanager
from contextvars import ContextVar
from types import SimpleNamespace

from rest_framework.exceptions import ValidationError
from stores.db import transaction

_scope = ContextVar('points_transfer_followup_pending_case', default=None)


def current_snapshot(order):
    state = _scope.get()
    if (state and transaction.get_connection().in_atomic_block
            and state[:2] == (transaction.get_connection().alias, order.pk)):
        return state[2]
    return None


def _unaffected_segments(order, affected_accounts, selected, handover, boundary):
    from django.db.models import Q
    from .models import OrderParticipationSegment, OrderProvider, OrderProviderCompletionFact
    target = handover.target_segment
    if (not target.started_at or target.started_at > boundary
            or (target.released_at and target.released_at <= boundary)
            or not OrderProvider.objects.filter(order=order, provider_account_id=target.provider_account_id)
                .filter(Q(released_at__isnull=True) | Q(released_at__gt=boundary))
                .filter(Q(settled_at__isnull=True) | Q(settled_at__gt=boundary)).exists()
            or OrderProviderCompletionFact.objects.filter(order=order, segment_id=target.pk,
                provider_account_id=target.provider_account_id, completed_at__lte=boundary).exists()):
        return False
    overlap = set(affected_accounts) & {handover.source_segment.provider_account_id,
                                       handover.target_segment.provider_account_id}
    if not overlap:
        return True
    if (not isinstance(selected, list) or not selected or len(set(selected)) != len(selected)
            or any(type(item) is not int or item <= 0 for item in selected)
            or handover.target_segment_id in selected):
        return False
    if handover.source_segment_id in selected:
        source = handover.source_segment
        if (not source.released_at or not handover.created_at
                or source.released_at > handover.created_at):
            return False
    segments = list(OrderParticipationSegment.objects.filter(attribution__order=order, pk__in=selected,
        started_at__isnull=False).values_list('pk', 'provider_account_id'))
    return len(segments) == len(selected) and {account for _, account in segments} == set(affected_accounts)


@contextmanager
def case_preflight(order, affected_accounts, selected_segments=None):
    """Bind unchanged pending responsibilities for this locked main-refund case."""
    from django.utils import timezone
    from .points_transfer_addon_liability import first_handover_pending_scope
    if (not transaction.get_connection().in_atomic_block or not isinstance(affected_accounts, list)
            or not affected_accounts or any(type(item) is not int or item <= 0 for item in affected_accounts)):
        raise ValidationError('存在待接替加购时须明确售后人员和履约范围')
    posting = SimpleNamespace(proposal=SimpleNamespace(case=SimpleNamespace(order=order),
        affected_accounts=affected_accounts, affected_segment_ids=selected_segments), points_refund=SimpleNamespace(
            refund=SimpleNamespace(created_at=timezone.now())))
    snapshot = capture_locked(posting)
    token = _scope.set((transaction.get_connection().alias, order.pk, snapshot))
    try:
        with first_handover_pending_scope(order, [item['payment_id'] for item in snapshot]):
            yield snapshot
    finally:
        _scope.reset(token)


def capture_locked(posting):
    from .addon_handover import current_responsibilities
    from .models import OrderAddonHandover
    from .points_transfer_addon_liability import effective_at, frozen_transfer_for_handover
    order = posting.proposal.case.order
    boundary = posting.points_refund.refund.created_at
    if not transaction.get_connection().in_atomic_block or not boundary:
        raise ValidationError('退款待接替快照须在原退款事务内生成')
    affected = posting.proposal.affected_accounts
    selected = posting.proposal.affected_segment_ids
    rows = list(OrderAddonHandover.objects.select_for_update().filter(
        payment__intent__order=order, status='PENDING').select_related(
            'payment__intent', 'source_segment', 'target_segment').order_by('payment_id', 'pk'))
    if len({row.payment_id for row in rows}) != len(rows):
        raise ValidationError('同一付款存在多个待确认接替')
    result = []
    for row in rows:
        source, target = row.source_segment.provider_account_id, row.target_segment.provider_account_id
        version = frozen_transfer_for_handover(order, row.payment_id, row.source_segment_id,
                                               row.target_segment_id, row.created_at)
        if (row.kind != 'STANDARD' or row.accepted_at or row.created_at >= boundary or not version
                or not _unaffected_segments(order, affected, selected, row, boundary)):
            raise ValidationError('主单退款与待接替人员或来源冲突')
        liability = effective_at(version, row.payment, boundary)
        _, parts = current_responsibilities(row.payment, lock=True)
        active = [part for part in parts if part['is_current']]
        if (liability != [row.payment_id, row.source_segment_id, row.source_basis_before]
                or len(active) != 1 or active[0]['segment_id'] != row.source_segment_id
                or active[0]['basis'] != row.source_basis_before):
            raise ValidationError('主单退款时待接替责任已变化')
        decisions = list(row.decisions.order_by('account_id', 'pk').values_list(
            'account_id', 'role', 'accepted', 'decided_at'))
        expected = {(row.payment.intent.buyer_id, 'BOSS'), (source, 'PROVIDER'), (target, 'PROVIDER')}
        if (len(decisions) != len(expected) or {(account, role) for account, role, _, _ in decisions} != expected
                or any(accepted is False or (accepted is True) != bool(at)
                       or (at and at >= boundary) for _, _, accepted, at in decisions)
                or all(accepted is True for _, _, accepted, _ in decisions)):
            raise ValidationError('接替决定与退款发生时序不一致')
        result.append({'handover_id': row.pk, 'payment_id': row.payment_id, 'transfer_revision_id': version.pk,
            'source_segment_id': row.source_segment_id, 'target_segment_id': row.target_segment_id,
            'source_basis': row.source_basis_before, 'request_hash': row.request_hash,
            'created_at': row.created_at.isoformat(),
            'decisions': [[account, role, accepted, at.isoformat() if at else None]
                          for account, role, accepted, at in decisions]})
    return result


def verify_snapshot(posting, snapshot, *, receipt_id=None):
    """Replay decisions as of the refund, without taking an order or receipt lock."""
    from .addon_handover import current_responsibilities
    from .models import OrderAddonHandover, PointsHandoverRefundInvalidation
    from .points_transfer_addon_liability import effective_at, frozen_transfer_for_handover
    order = posting.proposal.case.order
    boundary = posting.points_refund.refund.created_at
    if not isinstance(snapshot, list) or not boundary:
        raise ValidationError('退款待接替快照格式异常')
    if not snapshot:
        return
    ids = [item.get('handover_id') for item in snapshot if isinstance(item, dict)]
    if len(ids) != len(snapshot) or len(set(ids)) != len(ids):
        raise ValidationError('退款待接替方案快照异常')
    affected = posting.proposal.affected_accounts
    selected = posting.proposal.affected_segment_ids
    result = []
    rows = OrderAddonHandover.objects.filter(pk__in=ids, payment__intent__order=order, created_at__lt=boundary)\
        .select_related('payment__intent', 'source_segment', 'target_segment').order_by('payment_id', 'pk')
    for row in rows:
        decisions = list(row.decisions.order_by('account_id', 'pk').values_list(
            'account_id', 'role', 'accepted', 'decided_at'))
        if any(at == boundary for _, _, _, at in decisions):
            raise ValidationError('接替决定与退款时间无法区分')
        if (row.accepted_at and row.accepted_at < boundary) or any(
                accepted is False and at and at < boundary for _, _, accepted, at in decisions):
            continue
        if PointsHandoverRefundInvalidation.objects.filter(handover=row,
                refund__created_at__lt=boundary).exists():
            continue
        if row.kind != 'STANDARD':
            raise ValidationError('退款前的待接替种类尚未接通')
        version = frozen_transfer_for_handover(order, row.payment_id, row.source_segment_id,
                                               row.target_segment_id, row.created_at)
        if (not version or not _unaffected_segments(order, affected, selected, row, boundary)
                or effective_at(version, row.payment, boundary, witness_receipt_id=receipt_id) !=
                    [row.payment_id, row.source_segment_id, row.source_basis_before]):
            raise ValidationError('退款前待接替责任与原方案不一致')
        _, shares = current_responsibilities(row.payment, before=boundary)
        active = [part for part in shares if part['is_current']]
        if (len(active) != 1 or active[0]['segment_id'] != row.source_segment_id
                or active[0]['basis'] != row.source_basis_before):
            raise ValidationError('退款前的加购责任待核对')
        result.append({'handover_id': row.pk, 'payment_id': row.payment_id, 'transfer_revision_id': version.pk,
            'source_segment_id': row.source_segment_id, 'target_segment_id': row.target_segment_id,
            'source_basis': row.source_basis_before, 'request_hash': row.request_hash,
            'created_at': row.created_at.isoformat(), 'decisions': [[account, role,
                accepted if at and at < boundary else None, at.isoformat() if at and at < boundary else None]
                for account, role, accepted, at in decisions]})
    if result != snapshot:
        raise ValidationError('退款时待接替方案或确认快照不一致')
