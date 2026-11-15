"""Atomic release of excess store funding after an ordinary main refund."""
from dataclasses import asdict
import hashlib
import json
from uuid import UUID

from django.conf import settings
from rest_framework.exceptions import ValidationError

from stores.context import current_store
from stores.db import transaction
from .ordinary_addon_production import eligible as _ordinary_addon_eligible
from .models import OrderAddonFundingRelease, OrderAddonFundingReleaseRun, OrderPointsFundingAction, OrderPointsFundingAllocation
from .points_checkout_test import require_order_funding_access, ordinary_refund_production_order
from .points_funding_allocation import PointsFundingAllocationError, plan_release
from .points_funding_plan import PointsFundingPlanError


def _facts(run, allocated):
    from .ordinary_refunded_addon_funding import plan_locked
    result = plan_locked(run.negotiation.refund.order_id, _allocated_at_boundary=allocated, _boundary=run.created_at)
    return {'execution_id': run.negotiation_id, 'refund_id': run.negotiation.refund_id,
            'combined': asdict(result.combined), 'addon_history': list(result.addon_history)}


def _digest(run, facts):
    payload = [run.negotiation_id, run.created_at.isoformat(), run.allocation_snapshot,
               run.required_amount, run.allocated_before, run.released_amount, facts]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def verify_ordinary_run(run):
    """Verify the original platform allocations without re-entering receipt totals."""
    if not _ordinary_addon_eligible(run.negotiation.refund.order, allow_open=True):
        require_order_funding_access(run.negotiation.refund.order)
    if not transaction.get_connection().in_atomic_block or current_store() is None or not run.negotiation_id:
        raise ValidationError('普通退款补资释放须在门店事务内核对')
    snapshot = run.allocation_snapshot
    ids = [row[0] for row in snapshot]
    order_id = run.negotiation.refund.order_id
    original = list(OrderPointsFundingAllocation.objects.filter(service_order_id=order_id,
        created_at__lte=run.created_at).select_related('receipt').order_by('pk'))
    if (any(row.created_at == run.created_at for row in original)
            or [row.pk for row in original] != ids):
        raise ValidationError('普通退款前的补资分配记录不一致')
    from stores.models import PlatformPointsFundingOperation
    from stores.platform_pass_repayment import RepaymentError, _claimed_receipt, _sha
    from stores.platform_points_funding import _verified_receipt
    for allocation in original:
        try:
            key = allocation.request_key
            if not key.startswith('points:') or len(key) != 39:
                raise ValueError('invalid allocation key')
            operation_id = UUID(hex=key[7:])
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValidationError('普通退款原补资编号异常') from exc
        operation = PlatformPointsFundingOperation.objects.using('default').select_related('claim').filter(
            pk=operation_id, claim__store__slug=current_store().slug).first()
        action = OrderPointsFundingAction.objects.filter(request_key=key).first()
        expected = {'receipt_id': allocation.receipt_id, 'allocation_id': allocation.pk,
                    'order_id': order_id, 'amount': allocation.amount}
        if (not operation or not operation.completed_at or operation.rejected_at or operation.voided_at
                or not action or operation.tenant_action_id != action.pk or action.allocation_id != allocation.pk
                or operation.claim.tenant_receipt_id != allocation.receipt_id
                or action.actor != str(operation.actor_id) or allocation.allocated_by != str(operation.actor_id)
                or action.result != expected or operation.payload_digest != _sha(json.dumps(
                    [str(operation.claim_id), order_id, allocation.amount], separators=(',', ':')))):
            raise ValidationError('普通退款原补资平台记录异常')
        try:
            _claimed_receipt(operation.claim, current_store().slug, allocation.receipt)
            _verified_receipt(operation.claim, allocation.receipt)
        except RepaymentError as exc:
            raise ValidationError('普通退款原到账凭证异常') from exc
    if (isinstance(run.ordinary_refund_snapshot, dict)
            and run.ordinary_refund_snapshot.get('ordinary_transfer_followup_version') == 1):
        from .ordinary_transfer_followup_release import facts_locked
        facts = facts_locked(run)
        required = facts['funding']['required_units']
    elif (isinstance(run.ordinary_refund_snapshot, dict)
            and run.ordinary_refund_snapshot.get('ordinary_transfer_refund_version') == 1):
        from .ordinary_transfer_refund_release import facts_locked
        facts = facts_locked(run)
        required = facts['funding']['required_units']
    elif (isinstance(run.ordinary_refund_snapshot, dict)
            and 'followup_link_id' in run.ordinary_refund_snapshot):
        from .ordinary_addon_followup_release import facts_locked
        facts = facts_locked(run)
        required = facts['required_units']
    else:
        try:
            facts = _facts(run, run.allocated_before)
        except PointsFundingPlanError as exc:
            raise ValidationError('普通退款后的整单资金凭证异常') from exc
        required = facts['combined']['required_units']
    if (run.ordinary_refund_snapshot != facts or run.ordinary_refund_hash != _digest(run, facts)
            or required != run.required_amount):
        raise ValidationError('普通退款补资释放快照异常')
    return run


def record_locked(order, execution):
    """Return excess allocations in the same transaction as the refund."""
    require_order_funding_access(order)
    if (not transaction.get_connection().in_atomic_block
            or not ((ordinary_refund_production_order(order) or _ordinary_addon_eligible(order)) or (
                getattr(settings, 'ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING', False)
                and getattr(settings, 'ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING', False)))
            or execution.refund.order_id != order.pk):
        raise ValidationError('普通主单退款后的补资释放尚未开放')
    existing = OrderAddonFundingReleaseRun.objects.filter(negotiation=execution).first()
    if existing:
        from .addon_funding_release import verify_run
        verified = verify_run(existing)
        if getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is True:
            from users.receipt_usage_sources import verify_allocations_locked
            from .ordinary_refund_receipt_evidence import verified_release
            ids = [item[0] for item in existing.allocation_snapshot]
            receipts = set(OrderPointsFundingAllocation.objects.filter(pk__in=ids)
                           .values_list('receipt_id', flat=True))
            if not receipts:
                raise ValidationError('普通退款补资到账凭证缺失')
            # Receipt locks are always acquired in the same order. Each
            # receipt's local event chain is verified under its own lock.
            for receipt_id in sorted(receipts):
                verify_allocations_locked(receipt_id)
                verified_release(existing, receipt_id)
        return verified
    from .points_funding_evidence import verified_order_allocations_locked
    allocations = verified_order_allocations_locked(order)
    if not allocations:
        return None
    from .addon_funding_release import addon_release_totals
    previous = addon_release_totals(allocations)
    snapshot = [[row.pk, row.amount, previous[row.pk], []] for row in allocations]
    normalized = [[row[0], row[1], row[2]] for row in snapshot]
    before = sum(row[1] - row[2] for row in normalized)
    from .ordinary_refunded_addon_funding import plan_locked
    try:
        # Refunds must not wait for a later confirmation of already completed
        # add-on work. This plan only returns excess funding; individual
        # earnings still recheck the confirmation before settlement.
        plan = plan_locked(order.pk, _allow_unconfirmed_addons=True)
        if plan.allocated != before:
            raise ValidationError('普通退款补资分配已变化')
        parts = plan_release(allocations=normalized, amount=plan.releasable)
    except (PointsFundingPlanError, PointsFundingAllocationError) as exc:
        raise ValidationError('普通退款补资释放计算异常') from exc
    run = OrderAddonFundingReleaseRun.objects.create(negotiation=execution, required_amount=plan.required,
        allocated_before=before, released_amount=plan.releasable, allocation_snapshot=snapshot)
    try:
        facts = _facts(run, before)
    except PointsFundingPlanError as exc:
        raise ValidationError('普通退款补资释放凭证异常') from exc
    if facts['combined']['required_units'] != plan.required:
        raise ValidationError('普通退款补资计算已变化')
    run.ordinary_refund_snapshot = facts
    run.ordinary_refund_hash = _digest(run, facts)
    run.save(update_fields=['ordinary_refund_snapshot', 'ordinary_refund_hash'])
    OrderAddonFundingRelease.objects.bulk_create([
        OrderAddonFundingRelease(run=run, allocation_id=part.allocation_id, amount=part.amount) for part in parts])
    from .addon_funding_release import verify_run
    verified = verify_run(run)
    if getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is True:
        from users.receipt_usage_sources import record_first_addon_release_locked, verify_allocations_locked
        receipts = {row.receipt_id for row in allocations}
        for release in run.releases.select_related('allocation', 'run__negotiation__refund').order_by('allocation__receipt_id', 'pk'):
            record_first_addon_release_locked(release)
        from .ordinary_refund_receipt_evidence import verified_release
        for receipt_id in sorted(receipts):
            verify_allocations_locked(receipt_id)
            verified_release(run, receipt_id)
    return verified
