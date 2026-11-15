"""Isolated, auditable release of the second refund's excess funding."""
from django.conf import settings
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import OrderPointsFollowupFundingReleaseEvidence, OrderPointsFundingRelease
from .points_checkout_test import require_isolated_checkout
from .points_followup_revision import verify_followup_revision_locked


def _formal():
    from .points_business_access import permits
    return any(permits(phase=phase, kind=kind) for kind in ('service', 'addon')
               for phase in ('funding', 'refund', 'report', 'settlement'))


def _usage_scope_locked(revision, *, verify_ledger):
    """Check even zero releases; caller holds the order and receipt locks."""
    if not _formal() and not getattr(settings, 'SERVICE_POINTS_FOLLOWUP_RECEIPT_USAGE_TESTING', False):
        return
    from types import SimpleNamespace
    from users.models import PassRefundRepaymentReceipt
    from users.receipt_usage_sources import _require_current_unlocked_reads
    from .models import OrderPointsFundingAllocation
    if not isinstance(revision.allocations, list):
        raise ValidationError('后续退款到账快照异常')
    try:
        ids = {item[0] for item in revision.allocations}
        receipt_ids = {item[1] for item in revision.allocations}
    except (TypeError, IndexError) as exc:
        raise ValidationError('后续退款到账快照异常') from exc
    receipts = list(PassRefundRepaymentReceipt.objects.select_for_update().filter(pk__in=receipt_ids).order_by('pk'))
    if len(receipts) != len(receipt_ids):
        raise ValidationError('后续退款到账凭证缺失')
    if not any(item.usage_version is not None for item in receipts):
        return
    allocation = OrderPointsFundingAllocation.objects.filter(pk__in=ids).order_by('pk').first()
    if not allocation:
        raise ValidationError('后续退款原分配不存在')
    from .points_followup_receipt_usage import facts
    from django.core.exceptions import ValidationError as DjangoValidationError
    try:
        _require_current_unlocked_reads()
        facts(SimpleNamespace(posting=revision.posting, allocation=allocation, pk=0, amount=0))
        if verify_ledger:
            from users.receipt_usage_guard import isolated_allocation_state_locked
            for receipt in receipts:
                if receipt.usage_version is not None:
                    isolated_allocation_state_locked(receipt)
    except DjangoValidationError as exc:
        raise ValidationError('后续退款到账用途凭证异常') from exc


def verify_followup_release_locked(evidence):
    formal = _formal()
    if not formal:
        require_isolated_checkout()
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('后续退款补资释放须在门店事务内核对')
    if formal or getattr(settings, 'SERVICE_POINTS_FOLLOWUP_RECEIPT_USAGE_TESTING', False):
        from .models import Order
        from .financial_lock import lock_order_money
        from .points_receipt_usage import prelock_followup_receipts
        order_id = OrderPointsFollowupFundingReleaseEvidence.objects.filter(pk=evidence.pk).values_list(
            'revision__posting__proposal__case__order_id', flat=True).first()
        order = Order.objects.select_for_update().get(pk=order_id)
        if formal:
            from .points_business_access import permits
            if not any(permits(order, phase=phase, kind=kind) for kind in ('service', 'addon')
                       for phase in ('funding', 'refund', 'report', 'settlement')):
                raise ValidationError('后续退款释放不属于当前订单')
        lock_order_money()
        prelock_followup_receipts(order)
    evidence = OrderPointsFollowupFundingReleaseEvidence.objects.select_for_update().select_related(
        'revision__posting').get(pk=evidence.pk)
    revision = evidence.revision
    from .points_followup_context import is_active
    # A shared receipt may lead back to the revision already being verified by
    # an outer frame. In that case validate its release shape here; the outer
    # frame still has to complete the full economic and platform proof.
    if is_active(revision.posting_id):
        expected = revision.releases
        if (not isinstance(expected, list) or any(not isinstance(item, list) or len(item) != 3
                or any(type(value) is not int or value <= 0 for value in item) for item in expected)):
            raise ValidationError('后续退款补资释放快照异常')
    else:
        expected = list(verify_followup_revision_locked(revision).releases)
    rows = list(OrderPointsFundingRelease.objects.select_for_update().filter(posting=revision.posting)
                .order_by('-allocation_id').values_list('allocation_id', 'allocation__receipt_id', 'amount'))
    if (rows != [tuple(item) for item in expected] or evidence.created_at < revision.created_at
            or sum(row[2] for row in rows) != revision.release_units):
        raise ValidationError('后续退款补资释放凭证不一致')
    _usage_scope_locked(revision, verify_ledger=True)
    return evidence


def record_followup_release_locked(revision):
    formal = _formal()
    if formal:
        from .points_business_access import permits
        order = revision.posting.proposal.case.order
        if not any(permits(order, phase='refund', kind=kind) for kind in ('service', 'addon')):
            raise ValidationError('后续退款释放不属于当前退款事务')
    if not formal:
        require_isolated_checkout()
    if (not transaction.get_connection().in_atomic_block
            or (not formal and not getattr(settings, 'SERVICE_POINTS_FOLLOWUP_RELEASE_TESTING', False))):
        raise ValidationError('后续退款补资释放尚未开放')
    with transaction.atomic():
        existing = OrderPointsFollowupFundingReleaseEvidence.objects.select_for_update().filter(revision=revision).first()
        if existing:
            return verify_followup_release_locked(existing)
        calculated = verify_followup_revision_locked(revision)
        if OrderPointsFundingRelease.objects.select_for_update().filter(posting=revision.posting).exists():
            raise ValidationError('后续退款已有未核对的补资释放')
        OrderPointsFundingRelease.objects.bulk_create([
            OrderPointsFundingRelease(posting=revision.posting, allocation_id=allocation_id, amount=amount)
            for allocation_id, _, amount in calculated.releases])
        evidence = OrderPointsFollowupFundingReleaseEvidence.objects.create(revision=revision)
        _usage_scope_locked(revision, verify_ledger=False)
        if formal or getattr(settings, 'SERVICE_POINTS_FOLLOWUP_RECEIPT_USAGE_TESTING', False):
            from users.receipt_usage_sources import record_followup_release_locked as record_usage, verify_allocations_locked
            rows = list(OrderPointsFundingRelease.objects.filter(posting=revision.posting)
                .select_related('allocation__receipt').order_by('allocation__receipt_id', 'pk'))
            touched = set()
            for row in rows:
                if row.allocation.receipt.usage_version is not None:
                    record_usage(row)
                    touched.add(row.allocation.receipt_id)
            for receipt_id in sorted(touched):
                verify_allocations_locked(receipt_id)
        return verify_followup_release_locked(evidence)
