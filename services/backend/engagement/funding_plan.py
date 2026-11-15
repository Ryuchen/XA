"""Deterministic source plans for the new boss wallet payment flow.

Amounts are integer internal wallet units (10 units = 1 store coin). This
module only calculates plans; callers must lock and re-read the wallet and
source lots, persist the plan and make the debit in the same transaction.
"""
from dataclasses import dataclass


COIN_UNIT = 10
KINDS = ('PREPAID', 'GIFT', 'COMPENSATION')


class FundingPlanError(ValueError):
    pass


@dataclass(frozen=True)
class AvailablePart:
    lot_id: int
    kind: str
    amount: int


@dataclass(frozen=True)
class PlannedPart:
    lot_id: int
    kind: str
    amount: int


@dataclass(frozen=True)
class RefundablePart:
    allocation_id: int
    kind: str
    amount: int
    refunded: int = 0


def _whole_coin(value, name):
    if type(value) is not int or value < 0 or value % COIN_UNIT:
        raise FundingPlanError(f'{name}须为非负整数店币')


def plan_payment(parts, amount, *, gift=0, compensation=0):
    """Allocate explicit gift/compensation first, then principal for the rest.

    Parts are in stable funding-lot order. A mixed historical recharge may
    yield two parts with the same lot ID, but never two of the same kind.
    """
    for value, name in ((amount, '支付金额'), (gift, '赠送余额'), (compensation, '赔付余额')):
        _whole_coin(value, name)
    if gift + compensation > amount:
        raise FundingPlanError('赠送和赔付不能超过应付金额')
    seen = set()
    for part in parts:
        if (type(part.lot_id) is not int or part.lot_id <= 0 or part.kind not in KINDS
                or type(part.amount) is not int or part.amount < 0
                or (part.lot_id, part.kind) in seen):
            raise FundingPlanError('资金来源记录无效')
        seen.add((part.lot_id, part.kind))
    wanted = {'PREPAID': amount - gift - compensation, 'GIFT': gift, 'COMPENSATION': compensation}
    result = []
    for kind in KINDS:
        left = wanted[kind]
        for part in parts:
            if part.kind != kind or not left:
                continue
            used = min(left, part.amount)
            if used:
                result.append(PlannedPart(part.lot_id, kind, used))
                left -= used
        if left:
            raise FundingPlanError({'PREPAID': '本金不足', 'GIFT': '赠送余额不足',
                                    'COMPENSATION': '赔付余额不足'}[kind])
    return result


def plan_refund(parts, additional_amount):
    """Return per-allocation deltas: principal, gift, then compensation.

    Each refund is an integer number of store coins. Original allocations may
    contain fractions of a coin because a legacy mixed lot can split a source
    at an internal-unit boundary. Cumulative targets make retries and multiple
    partial refunds independent of the number of requests.
    """
    _whole_coin(additional_amount, '退款金额')
    seen = set()
    for part in parts:
        if (type(part.allocation_id) is not int or part.allocation_id <= 0
                or part.allocation_id in seen or part.kind not in KINDS
                or type(part.amount) is not int or part.amount <= 0
                or type(part.refunded) is not int or not 0 <= part.refunded <= part.amount):
            raise FundingPlanError('原付款来源或退款记录无效')
        seen.add(part.allocation_id)
    ordered = [part for kind in KINDS for part in parts if part.kind == kind]
    previous = sum(part.refunded for part in ordered)
    target = previous + additional_amount
    if target > sum(part.amount for part in ordered):
        raise FundingPlanError('退款超过本单可退金额')
    # Reject inconsistent existing refunds instead of silently reclassifying
    # them when calculating a later refund.
    consumed = 0
    result = {}
    for part in ordered:
        old_target = min(part.amount, max(0, previous - consumed))
        new_target = min(part.amount, max(0, target - consumed))
        if part.refunded != old_target:
            raise FundingPlanError('原退款来源顺序不一致，请先核对')
        result[part.allocation_id] = new_target - old_target
        consumed += part.amount
    return result
