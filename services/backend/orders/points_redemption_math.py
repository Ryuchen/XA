"""Read-only calculation of configured whole-group point redemption.

No account, eligibility, store configuration or order is read or changed
here. The caller must obtain those facts from the authenticated store, and
lock and revalidate them in the payment transaction before using this result.
Amounts use the existing internal wallet units: 10 units per store coin;
points use hundredths. The cap is expressed in basis points, not a float.
"""
from dataclasses import dataclass

COIN_UNIT = 10
RATIO_SCALE = 10_000


class PointsRedemptionError(ValueError):
    pass


def _integer(value, name, *, minimum=0):
    if type(value) is not int or value < minimum:
        raise PointsRedemptionError(f'{name}无效')
    return value


@dataclass(frozen=True)
class PointsRedemptionQuote:
    max_groups: int
    selected_groups: int
    points_hundredths: int
    discount_units: int
    payable_units: int


def quote_points_redemption(*, payable_before_points_units, available_point_hundredths,
                            selected_point_hundredths, group_point_hundredths,
                            group_coin_units, max_ratio_basis_points):
    """Quote selected whole configured groups after other eligible discounts.

    ``available_point_hundredths`` can be negative after a refund; it never
    permits spending. The selected amount is an upper bound, not a request to
    split a group. A zero result also permits an existing zero-price checkout.
    This result is not a reservation and does not authorize a debit.
    """
    amount = _integer(payable_before_points_units, '抵扣前应付')
    if amount % COIN_UNIT:
        raise PointsRedemptionError('抵扣前应付必须是整数店币')
    if type(available_point_hundredths) is not int:
        raise PointsRedemptionError('积分余额无效')
    selected = _integer(selected_point_hundredths, '选择积分')
    group_points = _integer(group_point_hundredths, '每组积分', minimum=1)
    group_coins = _integer(group_coin_units, '每组抵扣店币', minimum=COIN_UNIT)
    if group_coins % COIN_UNIT:
        raise PointsRedemptionError('每组抵扣必须是整数店币')
    ratio = _integer(max_ratio_basis_points, '抵扣比例上限')
    if ratio >= RATIO_SCALE:
        raise PointsRedemptionError('抵扣比例上限必须低于100%')

    balance_groups = max(available_point_hundredths, 0) // group_points
    cap_units = amount * ratio // RATIO_SCALE
    # Each group has an integer-coin value. Also retain at least one coin
    # whenever points are used, without restricting unrelated free orders.
    spendable_units = min(cap_units, max(0, amount - COIN_UNIT))
    max_groups = min(balance_groups, spendable_units // group_coins)
    selected_groups = min(max_groups, selected // group_points)
    discount = selected_groups * group_coins
    return PointsRedemptionQuote(max_groups, selected_groups, selected_groups * group_points,
                                 discount, amount - discount)
