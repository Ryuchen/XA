"""Pure, isolated payment-fee quotation; this is not a channel fee record.

Every policy parameter must be supplied explicitly. No provider, database,
order state, or external channel is consulted or modified here. An estimate is
not evidence of the actual WeChat fee: actual settlement must be reconciled
against the merchant's contract and authenticated channel records.
"""
from dataclasses import dataclass

MAX_FEN = 2**63 - 1
MAX_RATE_COMPONENT = 2**63 - 1
SERVICE_AMOUNT = 'SERVICE_AMOUNT'
PAYMENT_TOTAL = 'PAYMENT_TOTAL'
ROUND_DOWN = 'DOWN'
ROUND_UP = 'UP'
ROUND_HALF_UP = 'HALF_UP'


class PaymentFeeQuoteError(ValueError):
    pass


@dataclass(frozen=True)
class PaymentFeeQuote:
    service_fen: int
    estimated_fee_fen: int
    payment_total_fen: int


def _rounded_fee(base, numerator, denominator, rounding):
    product = base * numerator  # Python integers avoid intermediate overflow.
    if rounding == ROUND_DOWN:
        return product // denominator
    if rounding == ROUND_UP:
        return (product + denominator - 1) // denominator
    return (2 * product + denominator) // (2 * denominator)


def quote_payment_fee(*, service_fen: int, rate_numerator: int, rate_denominator: int,
                      fee_basis: str, rounding: str) -> PaymentFeeQuote:
    """Quote a merchant-selected surcharge using the supplied rational rate.

    ``SERVICE_AMOUNT`` calculates fee on the service amount. ``PAYMENT_TOTAL``
    calculates it on service plus this surcharge, choosing the *smallest*
    nonnegative integral fixed point after applying the specified rounding.
    Multiple fixed points can exist. Rates above 100%, and a total-based rate
    of 100%, are rejected; the latter need not have any finite fixed point.
    Inputs/outputs are bounded in integer cents. No assumptions about provider
    fee base, rate, rounding or coupon funding are encoded in this function.
    """
    if (type(service_fen) is not int or not 0 < service_fen <= MAX_FEN
            or type(rate_numerator) is not int or type(rate_denominator) is not int
            or not 0 <= rate_numerator <= MAX_RATE_COMPONENT
            or not 0 < rate_denominator <= MAX_RATE_COMPONENT
            or rate_numerator > rate_denominator
            or not isinstance(fee_basis, str) or fee_basis not in (SERVICE_AMOUNT, PAYMENT_TOTAL)
            or not isinstance(rounding, str) or rounding not in (ROUND_DOWN, ROUND_UP, ROUND_HALF_UP)):
        raise PaymentFeeQuoteError('Invalid fee quotation inputs')
    if fee_basis == PAYMENT_TOTAL and rate_numerator == rate_denominator:
        raise PaymentFeeQuoteError('Total-based fee rate must be less than 100%')
    if fee_basis == SERVICE_AMOUNT:
        fee = _rounded_fee(service_fen, rate_numerator, rate_denominator, rounding)
    else:
        # For rate < 1, fee - round(rate * (service + fee)) is monotone:
        # increasing fee by one can increase the rounded charge by at most one.
        # Find the first crossing; it is therefore the smallest fixed point.
        low, high = 0, MAX_FEN - service_fen
        if _rounded_fee(service_fen + high, rate_numerator, rate_denominator, rounding) > high:
            raise PaymentFeeQuoteError('Payment total exceeds supported range')
        while low < high:
            middle = (low + high) // 2
            if _rounded_fee(service_fen + middle, rate_numerator, rate_denominator, rounding) <= middle:
                high = middle
            else:
                low = middle + 1
        fee = low
    if fee > MAX_FEN - service_fen:
        raise PaymentFeeQuoteError('Payment total exceeds supported range')
    return PaymentFeeQuote(service_fen, fee, service_fen + fee)
