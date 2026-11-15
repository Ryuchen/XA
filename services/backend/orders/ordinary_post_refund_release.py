"""Return surplus receipt allocations caused by a later service transfer."""
import hashlib
import json
from uuid import UUID

from rest_framework.exceptions import ValidationError
from stores.context import current_store
from stores.db import transaction

from .models import OrderAddonFundingRelease, OrderAddonFundingReleaseRun, OrderPointsFundingAction, OrderPointsFundingAllocation
from .points_funding_allocation import plan_release


def _digest(run, facts):
    payload = [run.ordinary_transfer_revision_id, run.created_at.isoformat(), run.allocation_snapshot,
               run.required_amount, run.allocated_before, run.released_amount, facts]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True,
                                    separators=(',', ':')).encode()).hexdigest()


def verify_run(run):
    from .ordinary_post_refund_transfer import verify_locked
    if not transaction.get_connection().in_atomic_block or current_store() is None or not run.ordinary_transfer_revision_id:
        raise ValidationError('转单补资释放须在门店事务内核对')
    revision = verify_locked(run.ordinary_transfer_revision)
    facts = {'ordinary_post_refund_transfer_version': 1, 'revision_id': revision.pk,
             'transfer_id': revision.transfer_id, 'funding': revision.snapshot['funding']}
    if (run.ordinary_refund_snapshot != facts or run.ordinary_refund_hash != _digest(run, facts)
            or run.required_amount != facts['funding']['required_units']
            or run.allocated_before != facts['funding']['allocated_units']):
        raise ValidationError('退款后转单补资释放快照异常')
    ids = [row[0] for row in run.allocation_snapshot]
    allocations = list(OrderPointsFundingAllocation.objects.filter(
        service_order_id=revision.order_id, created_at__lt=run.created_at).select_related('receipt').order_by('pk'))
    if [row.pk for row in allocations] != ids:
        raise ValidationError('转单前补资分配记录不一致')
    from stores.models import PlatformPointsFundingOperation
    from stores.platform_pass_repayment import RepaymentError, _claimed_receipt, _sha
    from stores.platform_points_funding import _verified_receipt
    for allocation in allocations:
        key = allocation.request_key
        try:
            if not key.startswith('points:') or len(key) != 39:
                raise ValueError('invalid allocation key')
            operation_id = UUID(hex=key[7:])
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValidationError('原补资编号异常') from exc
        operation = PlatformPointsFundingOperation.objects.using('default').select_related('claim').filter(
            pk=operation_id, claim__store__slug=current_store().slug).first()
        action = OrderPointsFundingAction.objects.filter(request_key=key).first()
        expected = {'receipt_id': allocation.receipt_id, 'allocation_id': allocation.pk,
                    'order_id': revision.order_id, 'amount': allocation.amount}
        if (not operation or not operation.completed_at or operation.rejected_at or operation.voided_at
                or not action or operation.tenant_action_id != action.pk or action.allocation_id != allocation.pk
                or operation.claim.tenant_receipt_id != allocation.receipt_id
                or action.actor != str(operation.actor_id) or allocation.allocated_by != str(operation.actor_id)
                or action.result != expected or operation.payload_digest != _sha(json.dumps(
                    [str(operation.claim_id), revision.order_id, allocation.amount], separators=(',', ':')))):
            raise ValidationError('转单原补资平台记录异常')
        try:
            _claimed_receipt(operation.claim, current_store().slug, allocation.receipt)
            _verified_receipt(operation.claim, allocation.receipt)
        except RepaymentError as exc:
            raise ValidationError('转单原到账凭证异常') from exc
    return run


def record_locked(revision):
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('转单补资释放须在订单事务内处理')
    from .addon_funding_release import addon_release_totals, verify_run as verify_release
    existing = OrderAddonFundingReleaseRun.objects.filter(ordinary_transfer_revision=revision).first()
    if existing:
        return verify_release(existing)
    from .points_funding_evidence import _verified_allocations_for_locked_order
    allocations = _verified_allocations_for_locked_order(revision.order)
    if not allocations:
        return None
    previous = addon_release_totals(allocations)
    snapshot = [[row.pk, row.amount, previous[row.pk], []] for row in allocations]
    normalized = [[item[0], item[1], item[2]] for item in snapshot]
    before = sum(amount - released for _, amount, released in normalized)
    funding = revision.snapshot['funding']
    if funding['allocated_units'] != before:
        raise ValidationError('转单补资分配已变化')
    amount = max(before - funding['required_units'], 0)
    parts = plan_release(allocations=normalized, amount=amount)
    run = OrderAddonFundingReleaseRun.objects.create(ordinary_transfer_revision=revision,
        required_amount=funding['required_units'], allocated_before=before, released_amount=amount,
        allocation_snapshot=snapshot)
    facts = {'ordinary_post_refund_transfer_version': 1, 'revision_id': revision.pk,
             'transfer_id': revision.transfer_id, 'funding': funding}
    run.ordinary_refund_snapshot, run.ordinary_refund_hash = facts, _digest(run, facts)
    run.save(update_fields=['ordinary_refund_snapshot', 'ordinary_refund_hash'])
    OrderAddonFundingRelease.objects.bulk_create([
        OrderAddonFundingRelease(run=run, allocation_id=part.allocation_id, amount=part.amount) for part in parts])
    verified = verify_release(run)
    from users.receipt_usage_sources import record_first_addon_release_locked, verify_allocations_locked
    touched = set()
    for row in run.releases.select_related('allocation__receipt', 'run__ordinary_transfer_revision')\
            .order_by('allocation__receipt_id', 'pk'):
        if row.allocation.receipt.usage_version is not None:
            record_first_addon_release_locked(row)
            touched.add(row.allocation.receipt_id)
    for receipt_id in sorted(touched):
        verify_allocations_locked(receipt_id)
    return verified
