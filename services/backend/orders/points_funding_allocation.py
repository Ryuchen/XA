"""Arithmetic for allocating verified points-funding, with no ledger writes.

The caller must obtain and validate the locked receipt and order records from
the same tenant and write the allocation atomically. This helper does not
establish receipt authenticity, permissions, idempotency or settlement readiness.
All amounts use integer wallet units (ten units = one store coin).
"""
from dataclasses import dataclass


class PointsFundingAllocationError(ValueError):
    pass


@dataclass(frozen=True)
class AllocationResult:
    receipt_remaining: int
    order_remaining: int


@dataclass(frozen=True)
class ReleasePart:
    allocation_id: int
    amount: int


@dataclass(frozen=True)
class RebalanceResult:
    allocated_before: int
    required: int
    release_parts: tuple
    allocated_after: int
    remaining: int


def plan_rebalance(*, allocations, required):
    """Plan a funding revision from individually verified, net allocations.

    This is arithmetic only: callers must prove the order change, lock and
    verify each source receipt, and commit release evidence before reusing it.
    """
    if type(required) is not int or not 0 <= required <= 2147483647:
        raise PointsFundingAllocationError('订单补资需求无效')
    # plan_release validates IDs, per-allocation release totals and bounds,
    # including when no further release is needed.
    plan_release(allocations=allocations, amount=0)
    net = sum(amount - released for _, amount, released in allocations)
    if net > 2147483647:
        raise PointsFundingAllocationError('订单已分配资金超出范围')
    released = max(net - required, 0)
    parts = tuple(plan_release(allocations=allocations, amount=released))
    return RebalanceResult(net, required, parts, net - released, max(required - net, 0))


def plan_allocation(*, receipt_amount, receipt_allocated, order_required, order_allocated, amount):
    values = (receipt_amount, receipt_allocated, order_required, order_allocated, amount)
    if any(type(value) is not int or value < 0 or value > 2147483647 for value in values):
        raise PointsFundingAllocationError('补资金额必须是有效的整数记账单位')
    if not receipt_amount or not amount:
        raise PointsFundingAllocationError('到账和分配金额必须大于零')
    if receipt_allocated > receipt_amount or order_allocated > order_required:
        raise PointsFundingAllocationError('历史分配金额异常，需要核对')
    remaining = receipt_amount - receipt_allocated
    outstanding = order_required - order_allocated
    if amount > remaining:
        raise PointsFundingAllocationError('分配金额超过到账未分配余额')
    if amount > outstanding:
        raise PointsFundingAllocationError('分配金额超过订单待补资金')
    return AllocationResult(receipt_remaining=remaining - amount, order_remaining=outstanding - amount)


def plan_shared_receipt_allocation(*, receipt_amount, pass_allocated, points_allocated,
                                   order_required, order_allocated, amount):
    """Use one receipt balance for pass repayments and points funding.

    Both allocation totals must be read while the same receipt row is locked;
    callers must check actual, durable allocation evidence for both purposes.
    """
    for value in (pass_allocated, points_allocated):
        if type(value) is not int or value < 0 or value > 2147483647:
            raise PointsFundingAllocationError('补资金额必须是有效的整数记账单位')
    return plan_allocation(receipt_amount=receipt_amount, receipt_allocated=pass_allocated + points_allocated,
                           order_required=order_required, order_allocated=order_allocated, amount=amount)


def order_funding_is_complete(*, order_required, order_allocated):
    """Check whole-order funding only; this does not authorize any payout."""
    for value in (order_required, order_allocated):
        if type(value) is not int or value < 0 or value > 2147483647:
            raise PointsFundingAllocationError('订单补资金额无效')
    if order_allocated > order_required:
        raise PointsFundingAllocationError('订单已分配资金异常，需要核对')
    return order_allocated == order_required


def plan_release(*, allocations, amount):
    """Return reserved allocations in descending ID order, without writes.

    Each tuple contains a verified (allocation ID, original amount, amount
    already released). This does not prove the refund or that any allocation
    is unconsumed; the posting path must lock and verify those facts itself.
    """
    if type(amount) is not int or amount < 0 or amount > 2147483647:
        raise PointsFundingAllocationError('释放金额无效')
    if not isinstance(allocations, (list, tuple)):
        raise PointsFundingAllocationError('补资分配记录格式无效')
    verified = {}
    for row in allocations:
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            raise PointsFundingAllocationError('补资分配记录格式无效')
        pk, allocated, released = row
        if (type(pk) is not int or pk <= 0 or pk in verified
                or type(allocated) is not int or not 0 < allocated <= 2147483647
                or type(released) is not int or not 0 <= released <= allocated):
            raise PointsFundingAllocationError('补资分配或历史释放记录异常')
        verified[pk] = allocated - released
    if amount > sum(verified.values()):
        raise PointsFundingAllocationError('释放金额超过订单已分配余额')
    remaining, parts = amount, []
    for pk in sorted(verified, reverse=True):
        released = min(remaining, verified[pk])
        if released:
            parts.append(ReleasePart(pk, released))
            remaining -= released
        if not remaining:
            break
    return parts
