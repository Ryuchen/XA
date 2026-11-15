"""Immutable liability changes produced by a posted points add-on refund."""
import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta

from rest_framework.exceptions import ValidationError
from stores.db import transaction

_first_refund_scope = ContextVar('points_first_transfer_addon_refund', default=None)
_first_handover_scope = ContextVar('points_first_transfer_addon_handover', default=None)


def frozen_transfer_for_handover(order, payment_id, source_segment_id, target_segment_id, before):
    """Find the actual transfer that froze this payment's source responsibility."""
    from .models import PointsPostRefundTransferRevision
    for version in PointsPostRefundTransferRevision.objects.filter(order=order, created_at__lt=before)\
            .select_related('transfer').order_by('-created_at', '-pk'):
        if (version.transfer.previous_segment_id != source_segment_id
                or version.transfer.next_segment_id != target_segment_id
                or not isinstance(version.snapshot, dict)):
            continue
        rows = version.snapshot.get('pending_handover')
        if isinstance(rows, list) and any(isinstance(row, list) and len(row) == 3
                                          and row[0] == payment_id and row[1] == source_segment_id
                                          for row in rows):
            return version
    return None


def first_refund_active(order, payment_id=None):
    state = _first_refund_scope.get()
    return bool(transaction.get_connection().in_atomic_block and state
                and state[:2] == (transaction.get_connection().alias, order.pk)
                and (payment_id is None or state[2] == payment_id))


def first_refund_pending_allowed(order, payment_id):
    state = _first_refund_scope.get()
    if first_refund_active(order) and payment_id in state[4]:
        return True
    state = _first_handover_scope.get()
    return bool(transaction.get_connection().in_atomic_block and state
                and state[:2] == (transaction.get_connection().alias, order.pk)
                and payment_id in state[2])


@contextmanager
def first_handover_pending_scope(order, payment_ids):
    """Permit only validated liabilities while one real handover is processed."""
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('待接替资金范围须在订单事务内核对')
    token = _first_handover_scope.set((transaction.get_connection().alias, order.pk, frozenset(payment_ids)))
    try:
        yield
    finally:
        _first_handover_scope.reset(token)


@contextmanager
def first_refund_preflight(order, payment, handover, revision):
    """Pin a single real STANDARD liability for the current refund request."""
    from .addon_handover import current_responsibilities
    from .models import OrderAddonHandover, OrderSegmentSettlementManifest, PointsPostRefundTransferRevision
    from .points_post_refund_transfer import verify_locked, unresolved_pending_locked
    if (not transaction.get_connection().in_atomic_block or payment.intent.order_id != order.pk
            or handover.payment_id != payment.pk or handover.kind != 'STANDARD'
            or handover.status != 'PENDING' or handover.accepted_at
            or order.refund_records.count() != 1 or order.settled_at
            or order.providers.filter(settled_at__isnull=False).exists()
            or OrderSegmentSettlementManifest.objects.filter(order=order).exists()):
        raise ValidationError('首次退款后待接替加购退款范围待核对')
    due = {}
    for version in PointsPostRefundTransferRevision.objects.filter(order=order).order_by('pk'):
        verify_locked(version)
        for row in unresolved_pending_locked(version):
            if row[0] in due:
                raise ValidationError('同一付款存在多个待交转单责任')
            due[row[0]] = (version, row)
    pending = list(OrderAddonHandover.objects.select_for_update().filter(
        payment__intent__order=order, status='PENDING').select_related('payment__intent', 'source_segment', 'target_segment')
        .order_by('payment_id', 'pk'))
    if len({item.payment_id for item in pending}) != len(pending):
        raise ValidationError('同一付款存在多个待确认接替')
    for item in pending:
        owner = frozen_transfer_for_handover(order, item.payment_id, item.source_segment_id,
                                              item.target_segment_id, item.created_at)
        _, parts = current_responsibilities(item.payment, lock=True)
        active = [part for part in parts if part['is_current']]
        if (item.kind != 'STANDARD' or item.accepted_at or len(active) != 1
                or not owner or item.payment_id not in due or due[item.payment_id][0].pk != owner.pk
                or due[item.payment_id][1] != [item.payment_id, item.source_segment_id, item.source_basis_before]
                or active[0]['segment_id'] != item.source_segment_id
                or active[0]['basis'] != item.source_basis_before
                or item.target_segment_id != owner.transfer.next_segment_id):
            raise ValidationError('同单待接替付款责任已变化')
    _, shares = current_responsibilities(payment, lock=True)
    current = [row for row in shares if row['is_current']]
    if (len(current) != 1 or payment.pk not in due or due[payment.pk][0].pk != revision.pk
            or due[payment.pk][1] != [payment.pk, handover.source_segment_id, handover.source_basis_before]
            or current[0]['segment_id'] != handover.source_segment_id
            or current[0]['basis'] != handover.source_basis_before
            or handover.target_segment_id != revision.transfer.next_segment_id):
        raise ValidationError('首次退款后待接替的付款责任已变化')
    identity = (transaction.get_connection().alias, order.pk, payment.pk, handover.pk,
                frozenset(due))
    prior_refunds = set(payment.refund_records.values_list('pk', flat=True))
    token = _first_refund_scope.set(identity)
    try:
        yield
        current_refunds = set(payment.refund_records.values_list('pk', flat=True))
        if current_refunds != prior_refunds:
            # A transaction may not commit until its resulting history can be
            # read by the normal subsequent-handover capability.
            from .points_transfer_refund_access import post_refund_handover_history
            remaining = frozenset(OrderAddonHandover.objects.filter(
                payment__intent__order=order, status='PENDING').values_list('payment_id', flat=True))
            if not post_refund_handover_history(order, pending_payment_id=remaining or None):
                raise ValidationError('退款后的接替责任和资金版本尚不能完整回放')
    finally:
        _first_refund_scope.reset(token)


def _digest(order_id, revision_id, refund_id, payment_id, previous_id, facts):
    raw = [order_id, revision_id, refund_id, payment_id, previous_id, facts]
    return hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def verify_locked(row, *, witness_receipt_id=None):
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('加购责任版本须在订单事务内核对')
    from .addon_funding_release import verify_run
    from .addon_handover import current_responsibilities
    from .addon_payment import verify_receipt
    from .addon_refund_share_allocation import verify as verify_shares
    if (row.transfer_revision.order_id != row.order_id or row.payment.intent.order_id != row.order_id
            or row.refund.order_id != row.order_id or row.funding_run.refund_id != row.refund_id
            or row.transfer_revision.created_at >= row.refund.created_at
            or row.funding_run.created_at <= row.refund.created_at or row.created_at < row.funding_run.created_at
            or row.evidence_hash != _digest(row.order_id, row.transfer_revision_id, row.refund_id,
                                            row.payment_id, row.previous_id, row.snapshot)):
        raise ValidationError('加购责任版本的订单或时序异常')
    verify_receipt(row.payment)
    verify_shares(row.refund)
    verify_run(row.funding_run, _skip_usage=witness_receipt_id is not None,
               witness_receipt_id=witness_receipt_id)
    _, before = current_responsibilities(row.payment, before=row.refund.created_at, with_share_id=True,
                                          exclude_refund_id=row.refund_id)
    _, after = current_responsibilities(row.payment, before=row.refund.created_at + timedelta(microseconds=1),
                                         with_share_id=True)
    deductions = [list(item) for item in row.refund.earning_deductions.filter(payment=row.payment)
                  .order_by('pk').values_list('pk', 'participant_id', 'segment_id', 'amount')]
    releases = [list(item) for item in row.funding_run.releases.order_by('allocation_id')
                .values_list('allocation_id', 'amount')]
    liabilities = row.transfer_revision.snapshot.get('pending_handover', [])
    original = row.snapshot.get('original_liability') if isinstance(row.snapshot, dict) else None
    expected = {'transfer_id': row.transfer_revision.transfer_id, 'original_liability': original,
                'refund_id': row.refund_id, 'funding_run_id': row.funding_run_id,
                'before': before, 'after': after, 'deductions': deductions, 'releases': releases}
    if (original not in liabilities or not isinstance(original, list) or len(original) != 3
            or original[0] != row.payment_id or expected != row.snapshot
            or (row.payment_id != row.refund.payment_id and not deductions)):
        raise ValidationError('加购责任版本与实际退款不一致')
    if row.previous_id:
        previous = row.previous
        if (previous.order_id != row.order_id or previous.payment_id != row.payment_id
                or previous.transfer_revision_id != row.transfer_revision_id
                or previous.pk >= row.pk or previous.snapshot.get('after') != before):
            raise ValidationError('加购责任版本前驱不连续')
    elif not any(item['is_current'] and item['segment_id'] == original[1]
                 and item['basis'] == original[2] for item in before):
        raise ValidationError('加购责任版本缺少原转单份额')
    return row


def effective_at(transfer, payment, boundary, *, witness_receipt_id=None):
    """Read a frozen liability plus each real refund before the boundary."""
    from .models import PointsTransferAddonLiabilityRevision
    pending = transfer.snapshot.get('pending_handover', [])
    original = [row for row in pending if isinstance(row, list) and len(row) == 3 and row[0] == payment.pk]
    if len(original) != 1 or payment.intent.order_id != transfer.order_id:
        raise ValidationError('付款不属于该转单待接替责任')
    versions = list(PointsTransferAddonLiabilityRevision.objects.filter(
        transfer_revision=transfer, payment=payment, created_at__lt=boundary).order_by('pk'))
    for index, row in enumerate(versions):
        if row.previous_id != (versions[index - 1].pk if index else None):
            raise ValidationError('待接替退款责任版本链不连续')
        verify_locked(row, witness_receipt_id=witness_receipt_id)
    from django.db.models import Q
    from .models import OrderAddonRefund
    actual = set(OrderAddonRefund.objects.filter(order_id=transfer.order_id,
        created_at__gt=transfer.created_at, created_at__lt=boundary).filter(
        Q(payment=payment) | Q(earning_deductions__payment=payment)).values_list('pk', flat=True).distinct())
    if actual != {row.refund_id for row in versions}:
        raise ValidationError('待接替责任缺少真实退款版本')
    if not versions:
        return original[0]
    active = [row for row in versions[-1].snapshot['after'] if row['is_current']]
    if not active:
        return [payment.pk, original[0][1], 0]
    if len(active) != 1 or active[0]['segment_id'] != original[0][1]:
        raise ValidationError('退款后待接替负责人异常')
    return [payment.pk, active[0]['segment_id'], active[0]['basis']]


def hint_at(transfer, payment, boundary):
    """Classify a frozen chain before receipt locks; callers verify it later."""
    from django.db.models import Q
    from .models import OrderAddonRefund, PointsTransferAddonLiabilityRevision
    original = [item for item in transfer.snapshot.get('pending_handover', [])
                if isinstance(item, list) and len(item) == 3 and item[0] == payment.pk]
    if len(original) != 1:
        return None
    rows = list(PointsTransferAddonLiabilityRevision.objects.filter(transfer_revision=transfer,
        payment=payment, created_at__lt=boundary).order_by('pk'))
    for index, row in enumerate(rows):
        if (row.previous_id != (rows[index - 1].pk if index else None)
                or row.order_id != transfer.order_id or row.funding_run.refund_id != row.refund_id
                or row.evidence_hash != _digest(row.order_id, row.transfer_revision_id, row.refund_id,
                                                row.payment_id, row.previous_id, row.snapshot)):
            return None
    actual = set(OrderAddonRefund.objects.filter(order_id=transfer.order_id,
        created_at__gt=transfer.created_at, created_at__lt=boundary).filter(
        Q(payment=payment) | Q(earning_deductions__payment=payment)).values_list('pk', flat=True).distinct())
    if actual != {row.refund_id for row in rows}:
        return None
    if not rows:
        return original[0]
    active = [item for item in rows[-1].snapshot.get('after', []) if item.get('is_current')]
    if not active:
        return [payment.pk, original[0][1], 0]
    if len(active) != 1 or active[0].get('segment_id') != original[0][1]:
        return None
    return [payment.pk, active[0]['segment_id'], active[0]['basis']]


def record_locked(order, refund):
    """Record each real refund against the transfer that froze its liability."""
    if not transaction.get_connection().in_atomic_block or refund.order_id != order.pk:
        raise ValidationError('加购责任版本须在原订单退款事务内写入')
    from .models import (OrderAddonFundingReleaseRun, OrderAddonPayment, PointsPostRefundTransferRevision,
                         PointsTransferAddonLiabilityRevision)
    from .addon_handover import current_responsibilities
    from .addon_payment import verify_receipt
    from .addon_refund_share_allocation import verify as verify_shares
    from .addon_funding_release import verify_run
    transfers = list(PointsPostRefundTransferRevision.objects.filter(order=order, created_at__lt=refund.created_at)
                     .select_related('transfer').order_by('-created_at', '-pk'))
    if not transfers:
        return []
    changed = {refund.payment_id} | set(refund.earning_deductions.values_list('payment_id', flat=True))
    relevant = []
    selected = set()
    for transfer in transfers:
        if not isinstance(transfer.snapshot, dict) or not isinstance(transfer.snapshot.get('pending_handover'), list):
            raise ValidationError('转单待接替责任版本异常')
        for liability in transfer.snapshot['pending_handover']:
            if not isinstance(liability, list) or len(liability) != 3:
                raise ValidationError('转单待接替责任版本异常')
            payment_id, segment_id, _ = liability
            if payment_id not in changed or payment_id in selected:
                continue
            selected.add(payment_id)
            from .models import OrderAddonHandover
            if not OrderAddonHandover.objects.filter(payment_id=payment_id, status='ACCEPTED',
                    source_segment_id=segment_id, target_segment_id=transfer.transfer.next_segment_id,
                    accepted_at__gt=transfer.created_at, accepted_at__lt=refund.created_at).exists():
                relevant.append((transfer, liability))
    if not relevant:
        return []
    run = OrderAddonFundingReleaseRun.objects.filter(refund=refund).first()
    if not run or run.created_at <= refund.created_at:
        raise ValidationError('真实退款的资金释放版本缺失')
    verify_shares(refund)
    verify_run(run)
    result = []
    for transfer, (payment_id, segment_id, original_basis) in relevant:
        payment = OrderAddonPayment.objects.select_related('intent', 'buyer_transaction').get(
            pk=payment_id, intent__order=order)
        existing = PointsTransferAddonLiabilityRevision.objects.filter(refund=refund, payment=payment).first()
        if existing:
            result.append(verify_locked(existing))
            continue
        verify_receipt(payment)
        previous = (PointsTransferAddonLiabilityRevision.objects.select_for_update().filter(
            transfer_revision=transfer, payment=payment).order_by('-pk').first())
        _, before = current_responsibilities(payment, before=refund.created_at, with_share_id=True,
                                             exclude_refund_id=refund.pk)
        _, after = current_responsibilities(payment, before=refund.created_at + timedelta(microseconds=1),
                                            with_share_id=True)
        active_before = [row for row in before if row['is_current']]
        if (len(active_before) != 1 or active_before[0]['segment_id'] != segment_id
                or (not previous and active_before[0]['basis'] != original_basis)):
            raise ValidationError('退款前的转单待接替责任不一致')
        if previous and previous.snapshot.get('after') != before:
            raise ValidationError('加购责任事件前后不连续')
        deductions = list(refund.earning_deductions.filter(payment=payment).order_by('pk').values_list(
            'pk', 'participant_id', 'segment_id', 'amount'))
        if payment.pk != refund.payment_id and not deductions:
            raise ValidationError('跨付款责任变化缺少实际收益扣减')
        facts = {'transfer_id': transfer.transfer_id, 'original_liability': [payment_id, segment_id, original_basis],
                 'refund_id': refund.pk, 'funding_run_id': run.pk, 'before': before, 'after': after,
                 'deductions': [list(row) for row in deductions],
                 'releases': [list(row) for row in run.releases.order_by('allocation_id').values_list(
                     'allocation_id', 'amount')]}
        row = PointsTransferAddonLiabilityRevision.objects.create(order=order, transfer_revision=transfer,
            payment=payment, refund=refund, funding_run=run, previous=previous, snapshot=facts,
            evidence_hash=_digest(order.pk, transfer.pk, refund.pk, payment.pk, previous.pk if previous else None, facts))
        result.append(row)
    return result
