"""Tenant-local audit of funding revisions for service transfers.

This verifies immutable platform allocation authorizations without recursively
asking a shared receipt for its available balance. Callers must already hold
the order and receipt locks.
"""
from contextlib import nullcontext
from dataclasses import dataclass
import hashlib
import json
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from stores.context import current_database, current_store
from stores.db import connection, transaction
from .models import (OrderOriginalShareTransfer, OrderPointsFundingAction, OrderPointsFundingAllocation,
                     OrderPointsFundingRelease, OrderPointsTransferFundingRelease, OrderPointsTransferFundingRevision)
from .points_checkout_test import require_isolated_checkout
from .points_funding_allocation import PointsFundingAllocationError, plan_rebalance
from .points_funding_plan import PointsFundingPlanError
from .points_transfer_history import plan_historic_points_transfer


@dataclass(frozen=True)
class PendingRevision:
    last_transfer_id: int
    previous_transfer_id: int
    allocations: list
    before_funding: dict
    after_funding: dict
    releases: tuple


def _check_multiple_addon_scope(order, group):
    """Admit pre-start add-ons only when their original payment precedes service transfer."""
    from .models import OrderAddonPayment, OrderPrestartHandoff
    if not OrderPrestartHandoff.objects.filter(order=order).exists():
        return
    at = group[-1].created_at
    payments = list(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__lt=at).select_related('intent'))
    if len(payments) <= 1:
        return
    for payment in payments:
        refunded = sum(payment.refund_records.filter(created_at__lt=at).values_list('amount', flat=True))
        if refunded:
            raise PointsFundingPlanError('改派后的多项加购退款转单资金尚未接入')


def revision_tables_exist():
    with connection.cursor() as cursor:
        tables = set(connection.introspection.table_names(cursor))
    required = {OrderPointsTransferFundingRevision._meta.db_table, OrderPointsTransferFundingRelease._meta.db_table}
    if required.issubset(tables):
        return True
    if (required.intersection(tables) or 'django_migrations' not in tables
            or MigrationRecorder.Migration.objects.using(current_database()).filter(
                app='orders', name='0141_orderpointstransferfundingrevision_and_more').exists()):
        raise DatabaseError('转单补资版本凭证表缺失，须先核对数据库')
    return False


def _digest(order_id, revision):
    payload = [order_id, revision.last_transfer_id, revision.previous_transfer_id,
               revision.allocations, revision.before_funding, revision.after_funding]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _verify_allocation(order, allocation, *, verified_receipts=None):
    """Reconcile each allocation with its own platform and tenant actions."""
    from stores.models import PlatformPointsFundingOperation
    from stores.platform_pass_repayment import RepaymentError, _claimed_receipt, _sha
    from stores.platform_points_funding import _verified_receipt
    key = allocation.request_key
    try:
        if not key.startswith('points:') or len(key) != 39:
            raise ValueError('invalid operation key')
        operation_id = UUID(hex=key[7:])
    except (ValueError, TypeError, AttributeError):
        raise PointsFundingPlanError('转单补资原分配编号异常') from None
    operation = (PlatformPointsFundingOperation.objects.using('default').select_related('claim')
                 .filter(pk=operation_id, claim__store__slug=current_store().slug).first())
    action = OrderPointsFundingAction.objects.filter(request_key=key).first()
    expected = {'receipt_id': allocation.receipt_id, 'allocation_id': allocation.pk,
                'order_id': order.pk, 'amount': allocation.amount}
    if (not operation or not operation.completed_at or operation.rejected_at or operation.voided_at
            or operation.tenant_action_id is None or not action or operation.tenant_action_id != action.pk
            or action.allocation_id != allocation.pk or action.actor != str(operation.actor_id)
            or allocation.allocated_by != str(operation.actor_id) or action.result != expected
            or not allocation.redemption_id or allocation.service_order_id is not None
            or allocation.redemption.order_id != order.pk or operation.claim.tenant_receipt_id != allocation.receipt_id
            or operation.payload_digest != _sha(json.dumps([str(operation.claim_id), order.pk, allocation.amount],
                                                           separators=(',', ':')))):
        raise PointsFundingPlanError('转单补资原分配凭证异常')
    receipt, usage = (verified_receipts or {}).get(allocation.receipt_id, (allocation.receipt, None))
    try:
        _claimed_receipt(operation.claim, current_store().slug, receipt)
        if verified_receipts is not None and usage is None and receipt.usage_version is not None:
            from users.receipt_usage_guard import capture_allocation_usage_locked
            usage = capture_allocation_usage_locked(receipt)
        _verified_receipt(operation.claim, receipt, verified_usage=usage)
    except (RepaymentError, DjangoValidationError) as exc:
        raise PointsFundingPlanError('转单补资原到账凭证异常') from exc
    if verified_receipts is not None and usage is not None:
        verified_receipts[receipt.pk] = (receipt, usage)
    return operation.completed_at


def _enabled(order):
    from .points_transfer_refund_access import permits
    formal = any(permits(order, phase=phase) for phase in ('refund', 'transfer'))
    if not formal:
        require_isolated_checkout()
    if ((not getattr(settings, 'SERVICE_POINTS_TRANSFER_FUNDING_REVISION_TESTING', False)
            and not formal)
            or not transaction.get_connection().in_atomic_block or current_store() is None):
        raise PointsFundingPlanError('转单补资版本核对尚未开放')


def verify_revisions_locked(order, *, allow_missing_last=False, locked_receipt_ids=None):
    """Rebuild revisions, sharing receipt evidence only when all order receipts are already locked."""
    from .points_transfer_refund_access import permits, verification_scope
    read_phase = any(permits(order, phase=phase) for phase in ('report', 'settlement'))
    with verification_scope(order) if read_phase else nullcontext():
        _enabled(order)
        if locked_receipt_ids is None or getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is not True:
            return _verify_revisions_locked(order, allow_missing_last=allow_missing_last)
        from .points_addon_receipt_usage import reuse_history_locked
        with reuse_history_locked(order, locked_receipt_ids):
            return _verify_revisions_locked(order, allow_missing_last=allow_missing_last,
                                            locked_receipt_ids=locked_receipt_ids)


def _verify_revisions_locked(order, *, allow_missing_last, locked_receipt_ids=None):
    if not revision_tables_exist():
        raise PointsFundingPlanError('转单补资版本数据尚未就绪')
    from .points_funding_release import _paged
    events = list(_paged(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order)))
    from .points_transfer_refund_access import _verify_prestart_history
    _verify_prestart_history(order, events)
    owner = Q(redemption__order=order) | Q(service_order=order)
    allocations = list(_paged(OrderPointsFundingAllocation.objects.filter(owner).select_related('receipt', 'redemption')))
    if OrderPointsFundingRelease.objects.filter(Q(allocation__redemption__order=order)
                                                  | Q(allocation__service_order=order)).exists():
        raise PointsFundingPlanError('转单补资历史涉及退款，须先核对')
    verified_receipts = None
    if locked_receipt_ids is not None:
        if {row.receipt_id for row in allocations} != locked_receipt_ids:
            raise PointsFundingPlanError('转单补资到账记录已变化，请重新核对')
        verified_receipts = {}
    completed_at = {row.pk: _verify_allocation(order, row, verified_receipts=verified_receipts) for row in allocations}
    revisions = list(OrderPointsTransferFundingRevision.objects.filter(order=order).order_by('last_transfer_id'))
    by_event = {row.last_transfer_id: row for row in revisions}
    if len(by_event) != len(revisions):
        raise PointsFundingPlanError('转单资金版本重复')
    groups = []
    for event in events:
        if groups and event.created_at < groups[-1][-1].created_at:
            raise PointsFundingPlanError('转单历史时序异常')
        if groups and event.created_at == groups[-1][-1].created_at:
            groups[-1].append(event)
        else:
            groups.append([event])
    used_revisions, released_before, previous = set(), {}, 0
    pending = None
    for index, group in enumerate(groups):
        end = group[-1]
        _check_multiple_addon_scope(order, group)
        present = [item for item in allocations if item.created_at <= end.created_at]
        revision = by_event.get(end.pk)
        from .models import OrderAddonRefund
        has_prior_addon_refund = OrderAddonRefund.objects.filter(order=order, created_at__lt=end.created_at).exists()
        if present or has_prior_addon_refund:
            if any(completed_at[item.pk] > end.created_at for item in present):
                raise PointsFundingPlanError('转单前平台分配尚未完成')
            before = plan_historic_points_transfer(order.pk, through_transfer_id=previous, allow_recorded=True)
            after = plan_historic_points_transfer(order.pk, through_transfer_id=end.pk, allow_recorded=True)
            from .addon_funding_history import combined_at
            before_funding = combined_at(order, before.funding, end.created_at, segment_shares=before.segment_shares)
            after_funding = combined_at(order, after.funding, end.created_at, segment_shares=after.segment_shares)
            from .models import OrderAddonFundingRelease
            from .addon_funding_release import verify_run
            addon_releases = list(OrderAddonFundingRelease.objects.filter(
                allocation__in=present, run__created_at__lte=end.created_at).select_related('run'))
            for run in {item.run_id: item.run for item in addon_releases}.values():
                if run.created_at == end.created_at:
                    raise PointsFundingPlanError('加购补资释放与转单时序无法区分')
                try:
                    verify_run(run)
                except ValidationError as exc:
                    raise PointsFundingPlanError('加购补资释放凭证待核对') from exc
            addon_before = {}
            for release in addon_releases:
                addon_before[release.allocation_id] = addon_before.get(release.allocation_id, 0) + release.amount
            rows = [[item.pk, item.receipt_id, item.amount,
                     released_before.get(item.pk, 0) + addon_before.get(item.pk, 0)] for item in present]
            if sum(row[2] - row[3] for row in rows) > before_funding['additional_store_funding_units']:
                raise PointsFundingPlanError('转单前补资超过当时缺口')
            try:
                plan = plan_rebalance(allocations=[(row[0], row[2], row[3]) for row in rows],
                                      required=after_funding['additional_store_funding_units'])
            except PointsFundingAllocationError as exc:
                raise PointsFundingPlanError('转单补资释放计算异常') from exc
            pending = PendingRevision(end.pk, previous, rows, before_funding, after_funding, plan.release_parts)
            if not revision:
                if not allow_missing_last or index != len(groups) - 1:
                    raise PointsFundingPlanError('转单补资版本缺失')
                break
            if (revision.created_at < end.created_at or revision.previous_transfer_id != previous or revision.allocations != rows
                    or revision.before_funding != pending.before_funding or revision.after_funding != pending.after_funding
                    or revision.evidence_hash != _digest(order.pk, pending)):
                raise PointsFundingPlanError('转单补资版本与资金历史不一致')
            actual = list(revision.releases.order_by('-allocation_id').values_list('allocation_id', 'amount'))
            expected = [(part.allocation_id, part.amount) for part in pending.releases]
            if actual != expected:
                raise PointsFundingPlanError('转单补资释放流水不一致')
            for part in pending.releases:
                released_before[part.allocation_id] = released_before.get(part.allocation_id, 0) + part.amount
            used_revisions.add(revision.pk)
            pending = None
        elif revision:
            raise PointsFundingPlanError('无已分配补资的转单不应有资金版本')
        previous = end.pk
    if used_revisions != {row.pk for row in revisions}:
        raise PointsFundingPlanError('订单存在无法归属的转单资金版本')
    return released_before, pending


def record_latest_revision_locked(order):
    """Persist the newest already-authorized transfer and its release atomically."""
    from .points_transfer_revision_scope import active
    if not active(order):
        raise PointsFundingPlanError('转单补资版本须由当前转单事务建立')
    _, pending = verify_revisions_locked(order, allow_missing_last=True)
    if pending is None:
        return None
    revision = OrderPointsTransferFundingRevision.objects.create(order=order,
        last_transfer_id=pending.last_transfer_id, previous_transfer_id=pending.previous_transfer_id,
        allocations=pending.allocations, before_funding=pending.before_funding, after_funding=pending.after_funding,
        evidence_hash=_digest(order.pk, pending))
    OrderPointsTransferFundingRelease.objects.bulk_create([
        OrderPointsTransferFundingRelease(revision=revision, allocation_id=part.allocation_id, amount=part.amount)
        for part in pending.releases])
    # The domain revision and every shared-receipt RELEASE commit together.
    # Rebuild even a zero-release revision before treating it as complete.
    versioned = list(OrderPointsFundingAllocation.objects.filter(
        pk__in=[item[0] for item in revision.allocations], receipt__usage_version__isnull=False)
        .select_related('receipt', 'redemption__payment', 'redemption__ledger').order_by('receipt_id', 'pk'))
    if versioned:
        from types import SimpleNamespace
        from .points_transfer_receipt_usage import facts
        from users.receipt_usage_sources import record_transfer_revision_release_locked, verify_allocations_locked
        facts(SimpleNamespace(revision=revision, allocation=versioned[0], pk=0, amount=0))
        touched = set()
        for row in revision.releases.select_related('allocation__receipt', 'allocation__redemption', 'revision__order')\
                .order_by('allocation__receipt_id', 'pk'):
            if row.allocation.receipt.usage_version is not None:
                record_transfer_revision_release_locked(row)
                touched.add(row.allocation.receipt_id)
        for receipt_id in sorted(touched):
            verify_allocations_locked(receipt_id)
    verify_revisions_locked(order)
    return revision
