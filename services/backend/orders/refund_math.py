"""Exact refund calculations; all money is integer internal units.

Ten internal units equal one store coin. Points use hundredths of a point.
These functions calculate a proposal only; they never change wallets, points,
or order state and must be called with verified payment/participant snapshots.
"""
from dataclasses import dataclass
from typing import Mapping

COIN_UNIT = 10


class RefundCalculationError(ValueError):
    pass


def _integer(value, label, *, positive=False):
    if type(value) is not int or value < (1 if positive else 0):
        raise RefundCalculationError(f'{label}必须是有效的正整数' if positive else f'{label}必须是有效的非负整数')
    return value


def _whole_coin(value, label, *, positive=False):
    _integer(value, label, positive=positive)
    if value % COIN_UNIT:
        raise RefundCalculationError(f'{label}必须是整数店币')
    return value


@dataclass(frozen=True)
class PaymentRefund:
    coin_units: int
    point_hundredths: int
    is_final: bool


@dataclass(frozen=True)
class WalletSourceRefund:
    prepaid_units: int
    gift_units: int
    compensation_units: int


def wallet_source_refund(*, coin_units, paid_prepaid_units, paid_gift_units,
                         paid_compensation_units, refunded_prepaid_units=0,
                         refunded_gift_units=0, refunded_compensation_units=0):
    """Return this request's coins in prepaid, gift, compensation order.

    Original and previous amounts must be verified snapshots from this order;
    account-wide current balances cannot identify what the order spent.
    """
    amount = _whole_coin(coin_units, '本次退回店币')
    paid = [_whole_coin(paid_prepaid_units, '本金支付'),
            _whole_coin(paid_gift_units, '赠送支付'),
            _whole_coin(paid_compensation_units, '赔付支付')]
    prior = [_whole_coin(refunded_prepaid_units, '已退本金'),
             _whole_coin(refunded_gift_units, '已退赠送'),
             _whole_coin(refunded_compensation_units, '已退赔付')]
    if any(refunded > original for original, refunded in zip(paid, prior)):
        raise RefundCalculationError('历史退款超过原钱包支付来源')
    remaining = [original - refunded for original, refunded in zip(paid, prior)]
    if amount > sum(remaining):
        raise RefundCalculationError('本次店币退款超过原钱包支付余额')
    values = []
    for available in remaining:
        returned = min(amount, available)
        values.append(returned)
        amount -= returned
    return WalletSourceRefund(*values)


def payment_refund(*, paid_coin_units, paid_point_hundredths, point_value_units,
                   refund_equivalent_units, previously_refunded_equivalent_units=0,
                   previously_refunded_coin_units=0, previously_refunded_point_hundredths=0):
    """Refund one request in the original payment ratio, then floor each asset.

    Previous refund facts must come from recorded successful refunds. Every
    partial request is rounded independently; the final request returns the
    remaining exact original assets, so repeated truncation cannot strand them.
    """
    coin = _whole_coin(paid_coin_units, '原店币支付')
    points = _integer(paid_point_hundredths, '原积分支付')
    point_value = _whole_coin(point_value_units, '原积分抵扣等值')
    requested = _whole_coin(refund_equivalent_units, '退款等值', positive=True)
    prior = _whole_coin(previously_refunded_equivalent_units, '历史退款等值')
    coin_returned = _whole_coin(previously_refunded_coin_units, '历史退回店币')
    points_returned = _integer(previously_refunded_point_hundredths, '历史退回积分')
    if bool(points) != bool(point_value):
        raise RefundCalculationError('原积分数量与抵扣等值不一致')
    total = coin + point_value
    if not total or prior + requested > total or coin_returned > coin or points_returned > points:
        raise RefundCalculationError('退款或历史退款超出原支付构成')
    # Per-request floors can only return less than (or equal to) the floor of
    # the cumulative exact proportion, never more. Reject inconsistent facts
    # before calculating even the final remainder.
    if (coin_returned > (coin * prior // (total * COIN_UNIT)) * COIN_UNIT
            or points_returned > points * prior // total):
        raise RefundCalculationError('历史退款构成不一致，请先核对')
    is_final = prior + requested == total
    if is_final:
        return PaymentRefund(coin - coin_returned, points - points_returned, True)
    coin_delta = (coin * requested // (total * COIN_UNIT)) * COIN_UNIT
    point_delta = points * requested // total
    if coin_delta > coin - coin_returned or point_delta > points - points_returned:
        raise RefundCalculationError('历史退款构成不一致，请先核对')
    return PaymentRefund(coin_delta, point_delta, False)


@dataclass(frozen=True)
class ProviderBurden:
    # IDs are assignment/segment IDs supplied by the caller, not account IDs.
    amounts: dict[int, int]
    rounding_to_store_units: int


def provider_refund_burden(*, refund_equivalent_units, original_price_weights: Mapping[int, int]):
    """Allocate one refund by original-price shares, rounding each provider up.

    A caller that negotiated a specific affected participant supplies only that
    participant's verified share. Do not use this function to infer affected
    participants or original shares from later payments or today's catalog.
    """
    requested = _whole_coin(refund_equivalent_units, '退款等值', positive=True)
    if not isinstance(original_price_weights, Mapping) or not original_price_weights:
        raise RefundCalculationError('缺少可核验的陪玩原价份额')
    for key, weight in original_price_weights.items():
        _integer(key, '履约份额编号', positive=True)
        _integer(weight, '原价份额', positive=True)
    total_weight = sum(original_price_weights.values())
    denominator = total_weight * COIN_UNIT
    amounts = {
        key: ((requested * weight + denominator - 1) // denominator) * COIN_UNIT
        for key, weight in sorted(original_price_weights.items())
    }
    return ProviderBurden(amounts, sum(amounts.values()) - requested)
