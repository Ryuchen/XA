"""Write third-refund release and receipt-use facts in the refund transaction.

The caller must already have verified the accepted proposal, recomputed the
preflight under the order/money locks, and written the actual original-source
refund and provider deduction in the same transaction.  This function does
not expose or authorize a third refund.
"""
from django.db.models import Q
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def record_locked(order, posting, transfer_revision, calculation):
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('第三轮释放必须在退款事务内执行')
    with transaction.atomic():
        return _record_locked(order, posting, transfer_revision, calculation)


def _record_locked(order, posting, transfer_revision, calculation):
    from .financial_lock import lock_order_money
    from .models import (Order, OrderPointsFundingAllocation, OrderPointsFundingRelease,
                         PointsRefundRoundEvidence)
    from .points_funding_allocation import plan_release, PointsFundingAllocationError
    from .points_refund_round_evidence import record_locked as record_version
    from users.models import PassRefundRepaymentReceipt
    from users.receipt_usage_sources import record_followup_release_locked, verify_allocations_locked

    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('第三轮释放必须在退款事务内执行')
    locked = Order.objects.select_for_update().filter(pk=order.pk).first()
    if (not locked or posting.proposal.case.order_id != locked.pk or transfer_revision.order_id != locked.pk
            or not isinstance(calculation, dict) or calculation.get('order_id') != locked.pk
            or calculation.get('case_id') != posting.proposal.case_id
            or calculation.get('prior_version_id') != transfer_revision.pk
            or calculation.get('segment_id') != transfer_revision.transfer.next_segment_id
            or calculation.get('account_id') != transfer_revision.transfer.next_segment.provider_account_id
            or type(calculation.get('wallet_units')) is not int or calculation['wallet_units'] < 0
            or type(calculation.get('deposit_units')) is not int or calculation['deposit_units'] < 0
            or calculation.get('shortfall_units') != 0):
        raise ValidationError('第三轮退款实际履约或承担范围不一致')
    lock_order_money()
    allocations, planned, funding = (calculation.get('allocations'), calculation.get('releases'),
                                      calculation.get('funding'))
    if not isinstance(allocations, list) or not isinstance(planned, list) or not isinstance(funding, dict):
        raise ValidationError('第三轮退款资金计划未完成')
    try:
        parts = plan_release(allocations=allocations, amount=funding.get('release_units'))
    except PointsFundingAllocationError as exc:
        raise ValidationError('第三轮退款释放计划异常') from exc
    if sorted([[part.allocation_id, part.amount] for part in parts]) != planned:
        raise ValidationError('第三轮退款释放计划已变化')
    ids = [item[0] for item in allocations]
    owner = Q(redemption__order=locked) | Q(service_order=locked)
    existing = PointsRefundRoundEvidence.objects.filter(posting=posting).first()
    later = (OrderPointsFundingAllocation.objects.filter(owner, created_at__gt=posting.created_at)
             if existing and not ids else OrderPointsFundingAllocation.objects.none())
    receipt_ids = set(OrderPointsFundingAllocation.objects.filter(owner, pk__in=ids).values_list('receipt_id', flat=True))
    receipt_ids.update(later.values_list('receipt_id', flat=True))
    receipts = list(PassRefundRepaymentReceipt.objects.select_for_update().filter(pk__in=receipt_ids).order_by('pk'))
    if len(receipts) != len(receipt_ids) or any(receipt.usage_version is None for receipt in receipts):
        raise ValidationError('第三轮退款到账用途账本缺失')
    rows = list(OrderPointsFundingRelease.objects.filter(posting=posting).select_related('allocation__receipt')
                .order_by('allocation__receipt_id', 'pk'))
    if existing:
        if sorted([[row.allocation_id, row.amount] for row in rows]) != planned:
            raise ValidationError('第三轮退款原释放流水已变化')
    else:
        if rows:
            raise ValidationError('第三轮退款存在无来源释放流水')
        OrderPointsFundingRelease.objects.bulk_create([OrderPointsFundingRelease(
            posting=posting, allocation_id=part.allocation_id, amount=part.amount) for part in parts])
        rows = list(OrderPointsFundingRelease.objects.filter(posting=posting).select_related('allocation__receipt')
                    .order_by('allocation__receipt_id', 'pk'))
    source = {'allocations': allocations, 'releases': planned, 'funding': funding}
    version = record_version(locked, posting, transfer_revision=transfer_revision, current_release=source)
    touched = set()
    if not rows and allocations:
        # A zero release has no ledger event. Verify the real funding version
        # against one already-locked receipt instead of inventing one.
        from types import SimpleNamespace
        from .points_third_refund_receipt_usage import facts
        witness = OrderPointsFundingAllocation.objects.select_related('receipt').filter(
            owner, pk__in=ids).order_by('pk').first()
        if not witness:
            raise ValidationError('第三轮零释放缺少到账见证')
        facts(SimpleNamespace(posting=posting, allocation=witness, pk=0, amount=0))
        touched.add(witness.receipt_id)
    elif not allocations:
        witness = later.select_related('receipt').order_by('pk').first()
        if witness:
            from types import SimpleNamespace
            from .points_third_refund_receipt_usage import facts
            facts(SimpleNamespace(posting=posting, allocation=witness, pk=0, amount=0))
            touched.add(witness.receipt_id)
        else:
            from .points_third_refund_no_receipt import verify_locked
            verify_locked(locked, posting, transfer_revision, source)
    for row in rows:
        record_followup_release_locked(row)
        touched.add(row.allocation.receipt_id)
    for receipt_id in sorted(touched):
        verify_allocations_locked(receipt_id)
    return version
