"""Pricing inputs derived from an appointment, never from client quantities."""

from rest_framework.exceptions import ValidationError

HOURLY_UNITS = frozenset(('小时', '时', 'hour', '人·小时'))
LEDGER_UNITS_PER_COIN = 10


def hourly_minutes(reservation):
    """Return the booked duration for hourly services, otherwise None."""
    if reservation.service.price_unit not in HOURLY_UNITS:
        return None
    seconds = (reservation.end_time - reservation.start_time).total_seconds()
    if seconds != reservation.duration_minutes * 60:
        raise ValidationError('预约时间与时长不一致，请联系客服核对')
    return reservation.duration_minutes


def original_amount(service, units, duration_minutes=None):
    if duration_minutes is None:
        return service.price * units * service.billing_provider_count
    if (service.price_unit not in HOURLY_UNITS or type(duration_minutes) is not int
            or duration_minutes <= 0):
        raise ValidationError('预约时长或服务计价单位无效')
    # The hourly price is in ledger units; customers pay whole store coins.
    numerator = service.price * service.billing_provider_count * duration_minutes
    divisor = 60 * LEDGER_UNITS_PER_COIN
    return ((numerator + divisor - 1) // divisor) * LEDGER_UNITS_PER_COIN


def limited_coupon_basis(service, units, limit_units, duration_minutes=None):
    # One limited unit means an order unit, including each billed provider.
    billed_unit = service.price * service.billing_provider_count
    if duration_minutes is None:
        return billed_unit * min(units, limit_units)
    # The cap applies to the qualifying hours, not the full appointment.
    return billed_unit * min(duration_minutes, limit_units * 60) // 60
