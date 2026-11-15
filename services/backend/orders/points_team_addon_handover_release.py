"""Atomic release writer for a verified same-team first add-on handover.

This primitive is not an endpoint. Its dedicated order-bound handover
capability and both receipt/settlement verifiers must be wired before use.
"""
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def record_locked(order, handover):
    from .points_business_access import permits
    from .models import (OrderAddonFundingReleaseRun, OrderAddonFundingRelease,
                         OrderPointsFundingRelease,
                         OrderPointsTransferFundingRelease, ReservationPointsCancelFundingRelease)
    from .points_team_addon_handover_facts import verify_locked, funding_facts
    if (not transaction.get_connection().in_atomic_block
            or not permits(order, phase='handover', kind='addon')
            or handover.payment.intent.order_id != order.pk or order.refund_records.exists()
            or order.settled_at or order.providers.filter(settled_at__isnull=False).exists()):
        raise ValidationError('同单加购接替资金写入尚未开放')
    verify_locked(order, handover.payment, handover)
    existing = OrderAddonFundingReleaseRun.objects.filter(handover=handover).first()
    if existing:
        from .addon_funding_release import verify_run
        return verify_run(existing)
    from .points_funding_plan import plan_verified_order
    from .addon_funding import include_paid_addons
    from .points_funding_evidence import verified_order_allocations_locked
    main = plan_verified_order(order.pk)
    combined = include_paid_addons(order, main)
    allocations = verified_order_allocations_locked(order)
    if any(row.receipt.usage_version is None for row in allocations):
        raise ValidationError('同单加购接替的到账用途凭证待接入')
    if (OrderPointsFundingRelease.objects.filter(allocation__in=allocations).exists()
            or OrderPointsTransferFundingRelease.objects.filter(allocation__in=allocations).exists()
            or ReservationPointsCancelFundingRelease.objects.filter(allocation__in=allocations).exists()):
        raise ValidationError('同单加购接替已有其他资金释放')
    from .addon_funding_release import addon_release_totals, verify_run
    previous = addon_release_totals(allocations)
    snapshot = [[row.pk, row.amount, previous[row.pk], []] for row in allocations]
    normalized = [[row[0], row[1], row[2]] for row in snapshot]
    before = sum(row[1] - row[2] for row in normalized)
    required = combined.additional_store_funding_units
    from .points_funding_allocation import plan_release
    parts = plan_release(allocations=normalized, amount=max(before - required, 0))
    run = OrderAddonFundingReleaseRun.objects.create(handover=handover, required_amount=required,
        allocated_before=before, released_amount=sum(part.amount for part in parts), allocation_snapshot=snapshot)
    run.refresh_from_db(fields=['created_at'])
    facts = {'kind': 'SAME_TEAM_V1', **funding_facts(run, receipt_id=allocations[0].receipt_id if allocations else None)}
    from .points_addon_receipt_usage import digest
    run.points_handover_snapshot, run.points_handover_hash = facts, digest(facts)
    run.save(update_fields=['points_handover_snapshot', 'points_handover_hash'])
    OrderAddonFundingRelease.objects.bulk_create([
        OrderAddonFundingRelease(run=run, allocation_id=part.allocation_id, amount=part.amount) for part in parts])
    verify_run(run, _skip_usage=True)
    from users.receipt_usage_sources import record_first_addon_release_locked, verify_allocations_locked
    touched = set()
    for release in run.releases.select_related('allocation__receipt').order_by('allocation__receipt_id', 'pk'):
        record_first_addon_release_locked(release)
        touched.add(release.allocation.receipt_id)
    for receipt_id in sorted(touched):
        verify_allocations_locked(receipt_id)
    return verify_run(run)
