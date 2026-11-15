"""Verified original-price attribution for grab statistics, in ledger units.

Ten ledger units equal one whole store coin. Rounding surplus below is a
separate statistical attribution, not a claim that money has moved.
"""
from dataclasses import dataclass

COIN_UNITS = 10


@dataclass(frozen=True)
class OriginalShares:
    shares: tuple[tuple[int, int], ...]
    rounding_surplus: int


def initial_shares(original_amount, account_ids):
    """Split a new order's whole-coin original price across one or two people."""
    if type(original_amount) is not int or original_amount < 0 or original_amount % COIN_UNITS:
        raise ValueError('订单原价须为非负整数店币')
    account_ids = tuple(account_ids)
    if (len(account_ids) not in (1, 2) or any(type(pk) is not int or pk <= 0 for pk in account_ids)
            or len(set(account_ids)) != len(account_ids)):
        raise ValueError('请选择不同的单陪或双陪账号')
    coins = original_amount // COIN_UNITS
    per_person = ((coins + len(account_ids) - 1) // len(account_ids)) * COIN_UNITS
    return OriginalShares(tuple((account_id, per_person) for account_id in account_ids),
                          per_person * len(account_ids) - original_amount)


def agreed_transfer(slot_amount, allocations):
    """Validate staff-agreed original shares for one transferred slot.

    The caller identifies the actual affected slot, establishes participation
    and authorization, and persists the resulting per-segment facts and audit
    atomically. No changes to payment, settlement or wallet balances occur here.
    """
    if type(slot_amount) is not int or slot_amount < 0:
        raise ValueError('原份额无效')
    allocations = tuple(allocations)
    if not allocations:
        raise ValueError('请填写协商后的原价份额')
    ids = []
    for row in allocations:
        if (not isinstance(row, tuple) or len(row) != 2 or type(row[0]) is not int or row[0] <= 0
                or type(row[1]) is not int or row[1] < 0):
            raise ValueError('协商的参与记录或原价份额无效')
        ids.append(row[0])
    if len(ids) != len(set(ids)):
        raise ValueError('同一履约段不能重复填写')
    if sum(amount for _, amount in allocations) != slot_amount:
        raise ValueError('协商份额合计须等于转出前的原价份额')
    return allocations
