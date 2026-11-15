"""Transaction-local evidence for a future points voluntary add-on handover.

This module does not authorize or expose a handover endpoint. The caller
must own the order, money and receipt locks and create the funding run and
its release rows in the same tenant transaction as the final confirmation.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from types import SimpleNamespace

from django.utils import timezone
from rest_framework.exceptions import ValidationError

from stores.context import current_store
from stores.db import transaction

KIND = 'POINTS_VOLUNTARY_HANDOVER_V1'
_accepting = ContextVar('points_voluntary_handover_accepting', default=None)
_entry = ContextVar('points_voluntary_handover_entry', default=None)


@contextmanager
def entry(order_id, payment_id):
    store = current_store()
    connection = transaction.get_connection()
    if (not store or not connection.in_atomic_block or store.database_alias != connection.alias
            or type(order_id) is not int or order_id <= 0 or type(payment_id) is not int or payment_id <= 0):
        raise ValidationError('自愿接替入口上下文无效')
    identity = (connection.alias, store.slug, order_id, payment_id)
    if _entry.get() is not None:
        if _entry.get() != identity:
            raise ValidationError('自愿接替不能跨订单或付款嵌套')
        yield
        return
    token = _entry.set(identity)
    try:
        yield
    finally:
        _entry.reset(token)


def entry_matches(order, payment_id=None):
    store = current_store()
    connection = transaction.get_connection()
    value = _entry.get()
    return bool(store and connection.in_atomic_block and value
                and value[:3] == (connection.alias, store.slug, order.pk)
                and (payment_id is None or value[3] == payment_id))


@contextmanager
def accepting(handover):
    """Temporarily verify only this confirmed split under its tenant transaction."""
    store = current_store()
    connection = transaction.get_connection()
    if (not store or not connection.in_atomic_block or store.database_alias != connection.alias
            or not handover.pk or handover.status != 'ACCEPTED' or not handover.accepted_at):
        raise ValidationError('自愿接替确认事务无效')
    identity = (connection.alias, store.slug, handover.payment.intent.order_id,
                handover.payment_id, handover.pk)
    if _accepting.get() is not None:
        raise ValidationError('自愿接替确认上下文冲突')
    token = _accepting.set(identity)
    try:
        yield
    finally:
        _accepting.reset(token)


def provisional(handover):
    if _accepting.get() is None:
        return None
    store = current_store()
    connection = transaction.get_connection()
    expected = (connection.alias, store.slug if store else None, handover.payment.intent.order_id,
                handover.payment_id, handover.pk)
    if not connection.in_atomic_block or _accepting.get() != expected:
        return None
    run = SimpleNamespace(handover=handover, refund_id=None, payment_id=None,
                          created_at=timezone.now(), refunded_main_snapshot=None,
                          refunded_main_hash='', allocated_before=0, released_amount=0)
    facts(run, provisional=True)
    return list(handover.shares.select_related('segment__attribution').order_by('position'))


def facts(run, *, receipt_id=None, provisional=False):
    """Rebuild the actual confirmed parties and split, without posting money."""
    from .addon_handover import current_responsibilities, verify_handover, _income
    from .addon_intents import _commission
    from .addon_offers import AddonSelectionError
    from .addon_payment import verify_receipt
    from .models import OrderAddonFundingReleaseRun
    from console.models import AdminAuditLog

    handover = run.handover
    if (not handover or run.refund_id or run.payment_id or not run.created_at
            or handover.kind != 'VOLUNTARY'
            or handover.status != 'ACCEPTED' or not handover.created_at or not handover.accepted_at
            or not handover.created_at < handover.accepted_at < run.created_at):
        raise ValidationError('自愿接替确认及资金事件时序异常')
    payment, order = handover.payment, handover.payment.intent.order
    if (current_store() is None or current_store().database_alias != transaction.get_connection().alias
            or order.is_gift_snapshot or order.source_order_id):
        raise ValidationError('自愿接替门店或订单来源异常')
    verify_receipt(payment)
    previous, shares = current_responsibilities(payment, before=handover.created_at)
    if (previous.pk if previous else None) != handover.previous_id:
        raise ValidationError('自愿接替前次责任版本异常')
    if previous:
        prior_run = OrderAddonFundingReleaseRun.objects.filter(handover=previous).first()
        if not prior_run or prior_run.created_at >= handover.created_at:
            raise ValidationError('自愿接替前次资金事件缺失')
        if isinstance(prior_run.points_handover_snapshot, dict) and prior_run.points_handover_snapshot.get('kind') == KIND:
            from .addon_funding_release import verify_run
            verify_run(prior_run, _skip_usage=True, witness_receipt_id=receipt_id)
        else:
            verify_handover(payment, previous, as_of=handover.created_at, receipt_id=receipt_id)
            from .addon_funding_release import verify_run
            verify_run(prior_run, _skip_usage=True, witness_receipt_id=receipt_id)
    earlier = payment.handovers.filter(version__lt=handover.version).order_by('version')
    versions = list(earlier.values_list('version', flat=True))
    if versions != list(range(1, handover.version)):
        raise ValidationError('自愿接替方案版本缺失')
    intervening = list(payment.handovers.filter(version__gt=previous.version if previous else 0,
        version__lt=handover.version).order_by('version'))
    from .points_transfer_followup_handover_scope import _verify_rejected
    for attempt in intervening:
        if attempt.kind != 'VOLUNTARY' or attempt.status not in ('REJECTED', 'SUPERSEDED') or attempt.previous_id != handover.previous_id:
            raise ValidationError('自愿接替的前次方案状态待核对')
        if attempt.status == 'REJECTED':
            _verify_rejected(payment, attempt, before=handover.created_at)
        else:
            from .models import PointsHandoverRefundInvalidation
            from .points_followup_handover_invalidation import verify as verify_invalidation
            record = PointsHandoverRefundInvalidation.objects.filter(handover=attempt).select_related(
                'handover__payment__intent', 'refund__payment__intent').first()
            if not record or record.created_at >= handover.created_at:
                raise ValidationError('自愿接替失效的退款凭证缺失')
            verify_invalidation(record, receipt_id=receipt_id)
    if (payment.refund_records.filter(created_at__gte=handover.created_at,
                                             created_at__lte=handover.accepted_at).exists()
            or payment.refund_earning_deductions.filter(refund__created_at__gte=handover.created_at,
                refund__created_at__lte=handover.accepted_at).exists()):
        raise ValidationError('自愿接替确认期间责任已变化')
    active = [row for row in shares if row['is_current']]
    source, target = handover.source_segment, handover.target_segment
    if (len(active) != 1 or active[0]['segment_id'] != source.pk
            or active[0]['basis'] != handover.source_basis_before
            or source.attribution.order_id != order.pk or target.attribution.order_id != order.pk
            or source.provider_account_id == target.provider_account_id
            or not source.started_at or not target.started_at
            or source.started_at > handover.created_at or target.started_at > handover.created_at
            or (source.released_at and source.released_at <= handover.accepted_at)
            or (target.released_at and target.released_at <= handover.accepted_at)
            or not handover.transferred_basis or handover.retained_basis + handover.transferred_basis != active[0]['basis']
            or not handover.remaining_work or (handover.retained_basis and not handover.completed_work)):
        raise ValidationError('自愿接替的履约范围或份额异常')
    decisions = list(handover.decisions.order_by('account_id').values_list('account_id', 'role', 'accepted', 'decided_at'))
    expected = {(payment.intent.buyer_id, 'BOSS', True), (source.provider_account_id, 'PROVIDER', True),
                (target.provider_account_id, 'PROVIDER', True)}
    if (len(decisions) != 3 or {(a, role, accepted) for a, role, accepted, _ in decisions} != expected
            or any(not at or at < handover.created_at for _, _, _, at in decisions)
            or max(at for _, _, _, at in decisions) != handover.accepted_at):
        raise ValidationError('自愿接替三方确认异常')
    try:
        commission = _commission(target, lock=False)
    except AddonSelectionError as exc:
        raise ValidationError('自愿接替抽成凭证异常') from exc
    source_row = active[0]
    expected_shares = [row for row in shares if not row['is_current']]
    if handover.retained_basis:
        full = _income(source_row['basis'], source_row['commission_snapshot'])
        retained = (_income(handover.retained_basis, source_row['commission_snapshot'])
                    if source_row['income'] == full else source_row['income'] * handover.retained_basis // source_row['basis'])
        expected_shares.append({**source_row, 'basis': handover.retained_basis, 'income': retained, 'is_current': False})
    expected_shares.append({'segment_id': target.pk, 'basis': handover.transferred_basis,
        'income': _income(handover.transferred_basis, commission), 'commission_snapshot': commission, 'is_current': True})
    rows = list(handover.shares.order_by('position'))
    if ([row.position for row in rows] != list(range(len(rows))) or
            [(row.segment_id, row.basis, row.income, row.commission_snapshot, row.is_current) for row in rows] !=
            [(row['segment_id'], row['basis'], row['income'], row['commission_snapshot'], row['is_current'])
             for row in expected_shares]):
        raise ValidationError('自愿接替逐段收益异常')
    path = f'/api/admin/orders/{order.pk}/addons/payments/{payment.pk}/handovers/'
    audits = list(AdminAuditLog.objects.filter(resource='addon-handover', object_id=str(handover.pk),
                                               path=path, method='POST', status_code=200))
    body = {'order_id': order.pk, 'payment_id': payment.pk, 'source_provider': source.provider_account_id,
            'target_provider': target.provider_account_id, 'retained_basis': handover.retained_basis,
            'completed_work': handover.completed_work, 'remaining_work': handover.remaining_work}
    if len(audits) != 1 or audits[0].operator_account_id != handover.created_by_id or audits[0].request_body != body:
        raise ValidationError('自愿接替客服审计异常')
    if provisional:
        return None
    funding = run.refunded_main_snapshot
    if (not isinstance(funding, dict) or not isinstance(funding.get('funding'), dict)
            or funding['funding'].get('required_units') != run.required_amount):
        raise ValidationError('自愿接替整单资金事件缺失')
    return {'kind': KIND, 'order_id': order.pk, 'payment_id': payment.pk, 'handover_id': handover.pk,
            'previous_id': handover.previous_id, 'accepted_at': handover.accepted_at.isoformat(),
            'decisions': [[a, role, yes, at.isoformat()] for a, role, yes, at in decisions],
            'shares': [[row.pk, row.segment_id, row.basis, row.income, row.commission_snapshot, row.is_current]
                       for row in rows], 'funding_hash': run.refunded_main_hash,
            'allocated_before': run.allocated_before, 'released_amount': run.released_amount}


def write_locked(run):
    """Persist the evidence on an already posted run within its own transaction."""
    from .addon_funding_release import verify_run
    from .points_addon_receipt_usage import digest
    from .models import OrderAddonFundingReleaseRun
    if not transaction.get_connection().in_atomic_block or current_store() is None:
        raise ValidationError('自愿接替资金事件须在门店事务内记录')
    if not run.pk:
        raise ValidationError('自愿接替资金事件尚未保存')
    run = OrderAddonFundingReleaseRun.objects.select_for_update().get(pk=run.pk)
    if run.points_handover_snapshot is not None or run.points_handover_hash:
        return verify_run(run)
    # The complete original payment, funding version and release rows must
    # already exist; failure rolls the caller's confirmation back.
    verify_run(run, _skip_usage=True)
    value = facts(run)
    run.points_handover_snapshot, run.points_handover_hash = value, digest(value)
    run.save(update_fields=['points_handover_snapshot', 'points_handover_hash'])
    return verify_run(run, _skip_usage=True)
