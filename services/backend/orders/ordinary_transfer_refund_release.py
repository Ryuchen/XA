"""Release excess verified store funding after an ordinary transfer refund."""
from dataclasses import asdict

from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import OrderAddonFundingRelease, OrderAddonFundingReleaseRun
from .points_funding_allocation import PointsFundingAllocationError, plan_release
from .points_funding_plan import PointsFundingPlanError


def facts_locked(run):
    order = run.negotiation.refund.order
    from .models import OrderAddonPayment
    if OrderAddonPayment.objects.filter(intent__order=order).exists():
        from .ordinary_transfer_addon_funding import plan_locked
        combined = plan_locked(order.pk, allow_finalized=True, allocated_override=run.allocated_before,
                               boundary=run.created_at, allow_unconfirmed_addons=True)
        return {'ordinary_transfer_refund_version': 1, 'execution_id': run.negotiation_id,
                'refund_id': run.negotiation.refund_id, 'funding': asdict(combined.combined),
                'addon_history': list(combined.addon_history)}
    from .ordinary_transfer_refund_funding import plan_locked
    result = plan_locked(order.pk, allow_finalized=True, _allocated_override=run.allocated_before)
    return {'ordinary_transfer_refund_version': 1, 'execution_id': run.negotiation_id,
            'refund_id': run.negotiation.refund_id, 'funding': asdict(result)}


def record_locked(order, execution):
    from .ordinary_transfer_capability import permits
    if not transaction.get_connection().in_atomic_block or not permits(order):
        raise ValidationError('转单退款补资释放尚未开放')
    existing = OrderAddonFundingReleaseRun.objects.filter(negotiation=execution).first()
    if existing:
        from .addon_funding_release import verify_run
        return verify_run(existing)
    from .points_funding_evidence import verified_order_allocations_locked
    from .addon_funding_release import addon_release_totals, verify_run
    allocations = verified_order_allocations_locked(order)
    if not allocations:
        return None
    previous = addon_release_totals(allocations)
    snapshot = [[row.pk, row.amount, previous[row.pk], []] for row in allocations]
    normalized = [[row[0], row[1], row[2]] for row in snapshot]
    before = sum(amount - released for _, amount, released in normalized)
    try:
        from .models import OrderAddonPayment
        if OrderAddonPayment.objects.filter(intent__order=order).exists():
            from .ordinary_transfer_addon_funding import plan_locked
            plan = plan_locked(order.pk, allocated_override=before, allow_unconfirmed_addons=True).combined
        else:
            from .ordinary_transfer_refund_funding import plan_locked
            plan = plan_locked(order.pk, _allocated_override=before)
        parts = plan_release(allocations=normalized, amount=plan.release_units)
        release_ids = {part.allocation_id for part in parts}
        if any(row.pk in release_ids and row.receipt.usage_version is not None for row in allocations):
            from .models import OrderAddonPayment, OrderOriginalShareTransfer
            addons = OrderAddonPayment.objects.filter(intent__order=order)
            from .ordinary_transfer_event_scope import transfers_at
            from django.core.exceptions import ValidationError as DjangoValidationError
            try:
                transfers_at(order, execution.created_at)
            except DjangoValidationError as exc:
                raise ValidationError('首次转单退款的转单时序异常') from exc
            if any(row.receipt.usage_version is None for row in allocations):
                raise ValidationError('首次转单退款的共享到账释放来源尚未接通')
    except (PointsFundingPlanError, PointsFundingAllocationError) as exc:
        raise ValidationError('转单退款补资释放待核对') from exc
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
    for row in run.releases.select_related('allocation__receipt', 'run__negotiation__refund')\
            .order_by('allocation__receipt_id', 'pk'):
        if row.allocation.receipt.usage_version is not None:
            record_first_addon_release_locked(row)
            touched.add(row.allocation.receipt_id)
    for receipt_id in sorted(touched):
        verify_allocations_locked(receipt_id)
    return verified
