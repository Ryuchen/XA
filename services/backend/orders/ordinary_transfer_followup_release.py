"""Release verified surplus store funding after a later negotiation."""
from dataclasses import asdict

from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import OrderAddonFundingRelease, OrderAddonFundingReleaseRun
from .points_funding_allocation import PointsFundingAllocationError, plan_release
from .points_funding_plan import PointsFundingPlanError


def facts_locked(run):
    from .models import OrdinaryTransferFollowupRound
    row = OrdinaryTransferFollowupRound.objects.select_for_update().get(execution=run.negotiation)
    from .ordinary_transfer_followup_funding import plan_locked
    plan, history = plan_locked(row.pk, allocated=run.allocated_before, boundary=run.created_at)
    return {'ordinary_transfer_followup_version': 1, 'round_id': row.pk,
            'execution_id': run.negotiation_id, 'refund_id': run.negotiation.refund_id,
            'funding': asdict(plan), 'addon_history': history.get('addon_history', [])}


def record_locked(order, execution):
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('追加退款补资释放须在订单事务内处理')
    existing = OrderAddonFundingReleaseRun.objects.filter(negotiation=execution).first()
    from .addon_funding_release import addon_release_totals, verify_run
    if existing:
        return verify_run(existing)
    from .points_funding_evidence import _verified_allocations_for_locked_order
    allocations = _verified_allocations_for_locked_order(order)
    if not allocations:
        return None
    released = addon_release_totals(allocations)
    snapshot = [[item.pk, item.amount, released[item.pk], []] for item in allocations]
    normalized = [[item[0], item[1], item[2]] for item in snapshot]
    before = sum(amount - previous for _, amount, previous in normalized)
    from .models import OrdinaryTransferFollowupRound
    row = OrdinaryTransferFollowupRound.objects.select_for_update().get(execution=execution, order=order)
    try:
        from .ordinary_transfer_followup_funding import plan_locked
        plan, _ = plan_locked(row.pk, allocated=before)
        parts = plan_release(allocations=normalized, amount=plan.release_units)
        release_ids = {part.allocation_id for part in parts}
        if any(item.pk in release_ids and item.receipt.usage_version is not None for item in allocations):
            from .models import OrderOriginalShareTransfer
            from .ordinary_transfer_event_scope import transfers_at
            from django.core.exceptions import ValidationError as DjangoValidationError
            try:
                transfers_at(order, execution.created_at)
            except DjangoValidationError as exc:
                raise ValidationError('追加转单退款的转单时序异常') from exc
            from .models import OrdinaryPostRefundTransferRevision
            zero_pretransfer = bool(row.previous.zero_order_id == order.pk and not row.previous.refund_id
                and OrdinaryPostRefundTransferRevision.objects.filter(order=order,
                    snapshot__zero_pretransfer_execution_id=row.previous_id).exists())
            prior_ids = row.facts.get('prior_execution_ids', []) if isinstance(row.facts, dict) else []
            multiple_zero_pretransfer = bool(row.sequence > 1 and isinstance(prior_ids, list) and prior_ids
                and OrdinaryPostRefundTransferRevision.objects.filter(order=order,
                    snapshot__zero_pretransfer_execution_id=prior_ids[0]).exists())
            from .models import OrderTransferNegotiationEvidence
            multiple_transfer_first = bool(row.sequence > 1 and isinstance(prior_ids, list) and prior_ids
                and OrderTransferNegotiationEvidence.objects.filter(order=order, execution_id=prior_ids[0]).exists())
            multiple_positive_pretransfer = bool(row.sequence > 1 and isinstance(prior_ids, list) and prior_ids
                and OrdinaryPostRefundTransferRevision.objects.filter(order=order,
                    snapshot__positive_pretransfer_execution_id=prior_ids[0]).exists())
            if ((row.sequence != 1 and not (multiple_zero_pretransfer or multiple_transfer_first or multiple_positive_pretransfer))
                    or (row.sequence == 1 and not zero_pretransfer and (not row.previous.refund_id or order.refund_records.count() != 2))
                    or (zero_pretransfer and order.refund_records.count() != 1)
                    or any(item.receipt.usage_version is None for item in allocations)):
                raise ValidationError('追加转单退款的共享到账释放来源尚未接通')
    except (PointsFundingPlanError, PointsFundingAllocationError) as exc:
        raise ValidationError('追加退款补资释放待核对') from exc
    run = OrderAddonFundingReleaseRun.objects.create(negotiation=execution, required_amount=plan.required_units,
        allocated_before=before, released_amount=plan.release_units, allocation_snapshot=snapshot)
    facts = facts_locked(run)
    from .ordinary_refunded_addon_release import _digest
    run.ordinary_refund_snapshot, run.ordinary_refund_hash = facts, _digest(run, facts)
    run.save(update_fields=['ordinary_refund_snapshot', 'ordinary_refund_hash'])
    OrderAddonFundingRelease.objects.bulk_create([
        OrderAddonFundingRelease(run=run, allocation_id=part.allocation_id, amount=part.amount) for part in parts])
    verified = verify_run(run)
    from users.receipt_usage_sources import record_first_addon_release_locked, verify_allocations_locked
    touched = set()
    for item in run.releases.select_related('allocation__receipt', 'run__negotiation__refund')\
            .order_by('allocation__receipt_id', 'pk'):
        if item.allocation.receipt.usage_version is not None:
            record_first_addon_release_locked(item)
            touched.add(item.allocation.receipt_id)
    for receipt_id in sorted(touched):
        verify_allocations_locked(receipt_id)
    return verified
