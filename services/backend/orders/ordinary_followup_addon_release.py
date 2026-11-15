"""Funding version for an ordinary post-follow-up add-on refund."""
from dataclasses import asdict

from rest_framework.exceptions import ValidationError
from stores.db import transaction


def facts_locked(run):
    from .models import OrdinaryTransferFollowupRound
    from .ordinary_transfer_followup_funding import plan_locked
    row = OrdinaryTransferFollowupRound.objects.select_for_update().get(
        pk=run.ordinary_refund_snapshot['round_id'], order_id=run.refund.order_id)
    if not row.execution.termination_processed_at or row.execution.termination_processed_at >= run.refund.created_at:
        raise ValidationError('加购退款与追加协商的时序异常')
    plan, history = plan_locked(row.pk, allocated=run.allocated_before, boundary=run.created_at)
    return {'kind': 'ordinary_followup_addon_refund_v1', 'round_id': row.pk,
            'refund_id': run.refund_id, 'funding': asdict(plan),
            'addon_history': history.get('addon_history', [])}


def verify_locked(run):
    if not transaction.get_connection().in_atomic_block or not run.refund_id:
        raise ValidationError('加购退款资金版本须在订单事务中核验')
    from .ordinary_refunded_addon_release import _digest
    facts = facts_locked(run)
    if (run.refund.created_at >= run.created_at or facts != run.ordinary_refund_snapshot
            or _digest(run, facts) != run.ordinary_refund_hash
            or run.required_amount != facts['funding']['required_units']
            or run.released_amount != facts['funding']['release_units']):
        raise ValidationError('追加协商后加购退款资金版本异常')
    return run


def record_locked(order, refund):
    if not transaction.get_connection().in_atomic_block or refund.order_id != order.pk:
        raise ValidationError('加购退款资金版本须在本单事务中写入')
    from .models import (OrderAddonFundingReleaseRun, OrderPointsFundingRelease,
                         OrderPointsTransferFundingRelease, PointsPostRefundTransferRelease,
                         OrdinaryTransferFollowupRound)
    from .addon_funding_release import addon_release_totals, verify_run
    from .ordinary_transfer_followup_funding import plan_locked
    from .points_funding_evidence import _verified_allocations_for_locked_order
    from django.conf import settings
    from .ordinary_transfer_capability import permits
    if not permits(order, allow_addon_refund=True, allow_addon_handover=True):
        raise ValidationError('追加协商后该加购退款资金范围尚未接通')
    existing = OrderAddonFundingReleaseRun.objects.filter(refund=refund).first()
    if existing:
        return verify_run(existing)
    row = OrdinaryTransferFollowupRound.objects.select_for_update().filter(order=order,
        execution__termination_processed_at__lt=refund.created_at).order_by('-sequence').first()
    if not row:
        raise ValidationError('加购退款缺少已完成的追加协商')
    allocations = _verified_allocations_for_locked_order(order)
    if allocations and (getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is not True
                        or any(item.receipt.usage_version is None for item in allocations)):
        raise ValidationError('追加协商后加购退款须核验真实到账用途账本')
    previous = addon_release_totals(allocations)
    snapshot = []
    for allocation in allocations:
        transfer = list(OrderPointsTransferFundingRelease.objects.filter(allocation=allocation)
                        .order_by('pk').values_list('pk', 'amount'))
        main = list(OrderPointsFundingRelease.objects.filter(allocation=allocation)
                    .order_by('pk').values_list('pk', 'amount'))
        post = list(PointsPostRefundTransferRelease.objects.filter(allocation=allocation)
                    .order_by('pk').values_list('pk', 'amount'))
        snapshot.append([allocation.pk, allocation.amount, previous[allocation.pk],
                         [list(item) for item in transfer], [list(item) for item in main],
                         [list(item) for item in post]])
    before = sum(item[1] - item[2] - sum(entry[1] for group in item[3:] for entry in group)
                 for item in snapshot)
    plan, _ = plan_locked(row.pk, allocated=before)
    if not allocations and plan.release_units:
        raise ValidationError('无到账分配不能释放资金')
    from .points_funding_allocation import plan_release
    normalized = [[item[0], item[1], item[2] + sum(entry[1] for group in item[3:] for entry in group)]
                  for item in snapshot]
    parts = plan_release(allocations=normalized, amount=plan.release_units)
    run = OrderAddonFundingReleaseRun.objects.create(refund=refund, required_amount=plan.required_units,
        allocated_before=before, released_amount=plan.release_units, allocation_snapshot=snapshot,
        ordinary_refund_snapshot={'round_id': row.pk})
    facts = facts_locked(run)
    from .ordinary_refunded_addon_release import _digest
    run.ordinary_refund_snapshot, run.ordinary_refund_hash = facts, _digest(run, facts)
    run.save(update_fields=['ordinary_refund_snapshot', 'ordinary_refund_hash'])
    from .models import OrderAddonFundingRelease
    OrderAddonFundingRelease.objects.bulk_create([
        OrderAddonFundingRelease(run=run, allocation_id=part.allocation_id, amount=part.amount) for part in parts])
    verified = verify_run(run)
    from .ordinary_followup_addon_receipt import verify as verify_receipt
    if not allocations:
        verify_receipt(run, None)
    for receipt_id in sorted({item.receipt_id for item in allocations}):
        verify_receipt(run, receipt_id)
    from users.receipt_usage_sources import record_first_addon_release_locked, verify_allocations_locked
    touched = set()
    for release in run.releases.select_related('allocation__receipt', 'run__refund').order_by('allocation__receipt_id', 'pk'):
        if release.allocation.receipt.usage_version is not None:
            record_first_addon_release_locked(release)
            touched.add(release.allocation.receipt_id)
    for receipt_id in sorted(touched):
        verify_allocations_locked(receipt_id)
    return verified
