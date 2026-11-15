"""Rebalance verified store funding after a negotiated add-on handover."""
from dataclasses import asdict
import hashlib
import json

from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import OrderAddonFundingRelease, OrderAddonFundingReleaseRun
from .points_checkout_test import require_isolated_checkout
from .points_funding_allocation import PointsFundingAllocationError, plan_release
from .points_funding_plan import PointsFundingPlanError


def _facts(run, allocated):
    handover = run.handover
    order_id = handover.payment.intent.order_id
    if handover.status != 'ACCEPTED' or not handover.accepted_at or handover.accepted_at >= run.created_at:
        raise ValidationError('加购交接与补资记录时序待核对')
    from .ordinary_refunded_addon_funding import plan_locked
    result = plan_locked(order_id, _allocated_at_boundary=allocated, _boundary=run.created_at,
                         _allow_settled=True, _allow_finalized=True, _allow_unconfirmed_addons=True)
    if not any(row['payment_id'] == handover.payment_id and row['handover_id'] == handover.pk
               for row in result.addon_history):
        raise ValidationError('加购交接补资依据不一致')
    from .models import NegotiationExecution
    previous_refunds = list(NegotiationExecution.objects.select_for_update().filter(
        refund__order_id=order_id, refund__created_at__lt=run.created_at).order_by('pk'))
    if len(previous_refunds) > 1:
        raise ValidationError('加购交接前的协商退款记录待核对')
    if previous_refunds:
        execution = previous_refunds[0]
    else:
        from .negotiation_settlement import execution_for_order
        execution = execution_for_order(handover.payment.intent.order, lock=True)
    if not execution or not execution.termination_processed_at or execution.termination_processed_at >= run.created_at:
        raise ValidationError('加购交接的协商终止凭证待核对')
    shares = list(handover.shares.select_related('segment').order_by('position'))
    decisions = list(handover.decisions.order_by('account_id', 'role'))
    kind = 'refunded_addon_handover_v1' if execution.refund_id else 'zero_addon_handover_v1'
    return {'kind': kind, 'handover_id': handover.pk, 'execution_id': execution.pk,
            'handover': {'source_segment_id': handover.source_segment_id, 'target_segment_id': handover.target_segment_id,
                'source_basis': handover.source_basis_before, 'retained_basis': handover.retained_basis,
                'transferred_basis': handover.transferred_basis, 'completed_work': handover.completed_work,
                'remaining_work': handover.remaining_work,
                'shares': [{'segment_id': row.segment_id, 'account_id': row.segment.provider_account_id,
                            'basis': row.basis, 'income': row.income, 'current': row.is_current,
                            'commission': row.commission_snapshot} for row in shares],
                'decisions': [{'account_id': row.account_id, 'role': row.role, 'accepted': row.accepted,
                               'decided_at': row.decided_at.isoformat() if row.decided_at else None} for row in decisions]},
            'combined': asdict(result.combined), 'addon_history': list(result.addon_history)}


def _digest(run, facts):
    data = [run.handover_id, run.created_at.isoformat(), run.allocation_snapshot,
            run.required_amount, run.allocated_before, run.released_amount, facts]
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def verify_zero_handover_run(run):
    require_isolated_checkout()
    if not transaction.get_connection().in_atomic_block or not run.handover_id:
        raise ValidationError('加购交接补资核验须在门店事务内执行')
    try:
        facts = _facts(run, run.allocated_before)
    except PointsFundingPlanError as exc:
        raise ValidationError('加购交接后的整单资金凭证异常') from exc
    if (run.ordinary_refund_snapshot != facts or run.ordinary_refund_hash != _digest(run, facts)
            or facts['combined']['required_units'] != run.required_amount):
        raise ValidationError('加购交接补资快照异常')
    return run


def record_locked(order, handover):
    """Return only surplus allocations, atomically with the final approval."""
    require_isolated_checkout()
    if not transaction.get_connection().in_atomic_block or handover.payment.intent.order_id != order.pk:
        raise ValidationError('加购交接补资须在门店事务内处理')
    from .points_funding_evidence import verified_order_allocations_locked
    from .addon_funding_release import addon_release_totals, verify_run
    from .ordinary_refunded_addon_funding import plan_locked
    allocations = verified_order_allocations_locked(order)
    previous = addon_release_totals(allocations)
    snapshot = [[row.pk, row.amount, previous[row.pk], []] for row in allocations]
    normalized = [[row[0], row[1], row[2]] for row in snapshot]
    before = sum(row[1] - row[2] for row in normalized)
    try:
        # Other add-ons may still be awaiting their own confirmations. Their
        # last agreed shares remain the liability for this release; normal
        # settlement continues to require every affected item to be resolved.
        plan = plan_locked(order.pk, _allow_unconfirmed_addons=True)
        if plan.allocated != before:
            raise ValidationError('加购交接补资分配已变化')
        parts = plan_release(allocations=normalized, amount=plan.releasable)
    except (PointsFundingPlanError, PointsFundingAllocationError) as exc:
        raise ValidationError('加购交接补资计算待核对') from exc
    if not plan.releasable:
        return None
    run = OrderAddonFundingReleaseRun.objects.create(handover=handover, required_amount=plan.required,
        allocated_before=before, released_amount=plan.releasable, allocation_snapshot=snapshot)
    try:
        facts = _facts(run, before)
    except PointsFundingPlanError as exc:
        raise ValidationError('加购交接补资依据待核对') from exc
    if facts['combined']['required_units'] != plan.required:
        raise ValidationError('加购交接补资计算已变化')
    run.ordinary_refund_snapshot = facts
    run.ordinary_refund_hash = _digest(run, facts)
    run.save(update_fields=['ordinary_refund_snapshot', 'ordinary_refund_hash'])
    OrderAddonFundingRelease.objects.bulk_create([
        OrderAddonFundingRelease(run=run, allocation_id=part.allocation_id, amount=part.amount) for part in parts])
    return verify_run(run)
