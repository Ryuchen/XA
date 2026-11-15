"""Exact transfer-base allocation in the existing integer accounting unit.

This module only calculates a plan. A caller must separately establish the
per-segment deal basis and immutable commission snapshots, and must not post
funds from the result until the segment settlement flow is available.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class TransferBasis:
    total: int
    original_before: int
    original_retained: int
    original_transferred: int
    retained: int
    transferred: int
    remainder_numerator: int


@dataclass(frozen=True)
class TwoPartyFixedCommission:
    outgoing: int
    incoming: int
    outgoing_remainder: int
    incoming_remainder: int


def allocate_same_rule_fixed_seat(deal_total, fixed_total, seat_fixed, outgoing_basis, incoming_basis):
    """Preserve the affected seat's fixed fee when its rule is unchanged.

    The outgoing fee uses its share of the whole deal and is rounded down;
    the incoming party gets the remaining fee already assigned to this seat.
    These internal units are 0.1 coin; the result is not a payment.
    """
    values = (deal_total, fixed_total, seat_fixed, outgoing_basis, incoming_basis)
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError('成交基数和固定抽成必须为非负整数记账单位')
    if (deal_total == 0 or outgoing_basis + incoming_basis > deal_total
            or fixed_total > deal_total or seat_fixed > outgoing_basis + incoming_basis):
        raise ValueError('固定抽成席位基数或已分配金额无效')
    outgoing = fixed_total * outgoing_basis // deal_total
    remaining = seat_fixed - outgoing
    if outgoing > outgoing_basis or remaining < 0:
        raise ValueError('固定抽成席位尾差超出本人份额，请先核对')
    incoming = min(remaining, incoming_basis)
    return outgoing, incoming, remaining - incoming


def allocate_two_party_fixed_commission(deal_total, outgoing_basis, incoming_basis,
                                        outgoing_fixed_total, incoming_fixed_total):
    """Calculate the confirmed two-party case only; does not authorize payout.

    Values are internal units (0.1 store coin). Each party uses their own
    immutable whole-order fixed rule; the outgoing share is floored and the
    incoming share is ceiled. This is not a multi-transfer allocation or a
    replacement for refund, fee-cap, or settlement handling.
    """
    values = (deal_total, outgoing_basis, incoming_basis, outgoing_fixed_total, incoming_fixed_total)
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError('成交基数和固定抽成必须为非负整数记账单位')
    if not deal_total or outgoing_basis + incoming_basis != deal_total:
        raise ValueError('成交基数必须合计为正的整单成交额')
    outgoing, outgoing_remainder = divmod(outgoing_fixed_total * outgoing_basis, deal_total)
    incoming, incoming_remainder = divmod(incoming_fixed_total * incoming_basis, deal_total)
    return TwoPartyFixedCommission(outgoing, incoming + bool(incoming_remainder),
                                   outgoing_remainder, incoming_remainder)


def allocate_transfer_basis(total, original_before, original_retained, original_transferred):
    """Floor the outgoing share to 0.1 coin; the incoming gets the remainder.

    All arguments and returned bases use the existing integer internal unit:
    one unit is 0.1 coin. Original-price shares must be whole store coins.
    The remainder numerator is audit evidence of the pre-rounding fraction;
    its denominator is ``original_before`` and it is not a money movement.
    """
    values = (total, original_before, original_retained, original_transferred)
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError('成交额和原价份额必须为非负整数记账单位')
    if any(value % 10 for value in values[1:]):
        raise ValueError('原价份额必须为整数店币')
    if original_retained + original_transferred != original_before:
        raise ValueError('转出和接替份额合计必须等于原价份额')
    if original_before == 0:
        if total:
            raise ValueError('原价份额为零但成交基数不为零，请先核对')
        return TransferBasis(total, 0, 0, 0, 0, 0, 0)
    retained, remainder = divmod(total * original_retained, original_before)
    return TransferBasis(total, original_before, original_retained, original_transferred,
                         retained, total - retained, remainder)
