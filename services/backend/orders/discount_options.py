"""Enumerate mutually exclusive percentage discounts and one coupon.

This is a calculation layer. Callers must validate actual promotion/coupon
ownership, scope, availability and versions under the payment transaction;
these immutable inputs are not themselves authorization to consume a coupon.
Amounts are in wallet units, where ten units equal one integer store coin.
"""
from dataclasses import dataclass

COIN_UNIT = 10


class DiscountOptionError(ValueError):
    pass


@dataclass(frozen=True)
class PercentageOption:
    key: str
    label: str
    rate: int
    source: str  # NONE, VIP or PROMOTION
    promotion_id: int | None = None


@dataclass(frozen=True)
class VoucherOption:
    coupon_id: int
    label: str
    kind: str  # AMOUNT or RATE
    threshold: int = 0
    amount: int = 0
    rate: int = 100
    max_discount: int | None = None


@dataclass(frozen=True)
class DiscountOption:
    key: str
    label: str
    percentage_key: str
    promotion_id: int | None
    coupon_id: int | None
    original_amount: int
    boss_discount: int
    promo_discount: int
    coupon_discount: int
    amount: int


def _nonnegative_int(value):
    return type(value) is int and value >= 0


def _rounded_price(amount, rate):
    # Round the customer's discounted payable *up* to the next whole coin.
    return ((amount * rate + 100 * COIN_UNIT - 1) // (100 * COIN_UNIT)) * COIN_UNIT


def discount_options(original, percentages, vouchers, *, amount_threshold_basis, rate_threshold_basis):
    """Return all legal options with the most favorable deterministic first.

    Threshold basis is deliberately explicit: price policy must be decided by
    the caller, not inferred from the existence of a VIP or campaign.
    """
    if not _nonnegative_int(original) or original % COIN_UNIT:
        raise DiscountOptionError('原价必须为非负整数店币')
    if amount_threshold_basis not in ('AFTER_RATE', 'ORIGINAL') or rate_threshold_basis not in ('ORIGINAL', 'AFTER_VIP'):
        raise DiscountOptionError('优惠券门槛口径未明确')
    if not percentages or not any(item.source == 'NONE' for item in percentages):
        raise DiscountOptionError('缺少原价选项')
    keys = set()
    for item in percentages:
        if (not item.key or item.key in keys or item.source not in ('NONE', 'VIP', 'PROMOTION')
                or type(item.rate) is not int or not 0 <= item.rate <= 100
                or (item.source == 'NONE' and item.rate != 100)
                or (item.source == 'PROMOTION' and (type(item.promotion_id) is not int or item.promotion_id <= 0))
                or (item.source != 'PROMOTION' and item.promotion_id is not None)):
            raise DiscountOptionError('比例优惠配置无效')
        keys.add(item.key)
    vip = [item for item in percentages if item.source == 'VIP']
    if len(vip) > 1 or sum(item.source == 'NONE' for item in percentages) != 1:
        raise DiscountOptionError('会员或原价选项不唯一')
    coupon_ids = set()
    for voucher in vouchers:
        if (type(voucher.coupon_id) is not int or voucher.coupon_id <= 0 or voucher.coupon_id in coupon_ids
                or voucher.kind not in ('AMOUNT', 'RATE') or not _nonnegative_int(voucher.threshold)
                or not _nonnegative_int(voucher.amount) or type(voucher.rate) is not int or not 0 <= voucher.rate <= 100
                or (voucher.max_discount is not None and not _nonnegative_int(voucher.max_discount))):
            raise DiscountOptionError('优惠券配置无效')
        coupon_ids.add(voucher.coupon_id)

    result = []
    for percentage in percentages:
        discounted = _rounded_price(original, percentage.rate)
        percentage_discount = original - discounted
        boss = percentage_discount if percentage.source == 'VIP' else 0
        promo = percentage_discount if percentage.source == 'PROMOTION' else 0
        result.append(DiscountOption(percentage.key, percentage.label, percentage.key, percentage.promotion_id,
                                     None, original, boss, promo, 0, discounted))
        for voucher in vouchers:
            if voucher.kind != 'AMOUNT':
                continue
            basis = discounted if amount_threshold_basis == 'AFTER_RATE' else original
            if basis < voucher.threshold:
                continue
            # A coupon must not make an integer-coin payment fractional.
            discount = min(discounted, voucher.amount // COIN_UNIT * COIN_UNIT)
            if discount:
                result.append(DiscountOption(f'{percentage.key}|coupon:{voucher.coupon_id}',
                    f'{percentage.label} + {voucher.label}', percentage.key, percentage.promotion_id,
                    voucher.coupon_id, original, boss, promo, discount, discounted - discount))

    no_percentage = next(item for item in percentages if item.source == 'NONE')
    for voucher in vouchers:
        if voucher.kind != 'RATE':
            continue
        basis = original if rate_threshold_basis == 'ORIGINAL' or not vip else _rounded_price(original, vip[0].rate)
        if basis < voucher.threshold:
            continue
        discount = original - _rounded_price(original, voucher.rate)
        if voucher.max_discount is not None:
            discount = min(discount, voucher.max_discount // COIN_UNIT * COIN_UNIT)
        if discount:
            result.append(DiscountOption(f'coupon:{voucher.coupon_id}', voucher.label, no_percentage.key,
                None, voucher.coupon_id, original, 0, 0, discount, original - discount))
    # Equivalent options never consume a coupon by default; remaining ties
    # are stable by key so re-read quote order doesn't fluctuate.
    return sorted(result, key=lambda item: (item.amount, item.coupon_id is not None, item.key))
