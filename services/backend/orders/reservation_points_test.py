"""Bind experimental points checkout to one confirmed reservation.

This is usable only through the existing disposable-database checkout gate;
it is not a production enablement switch.
"""

from django.core import signing
from rest_framework.exceptions import ValidationError

from stores.context import current_store
from .points_checkout_test import assignment_enabled, quote_for_checkout, require_isolated_checkout, verify_checkout_quote

SALT = 'orders.reservation-points-checkout-test.v1'


def _snapshot(reservation):
    store = current_store()
    if store is None:
        raise ValidationError('缺少门店上下文，请重新登录')
    if reservation.status != reservation.Status.CONFIRMED:
        raise ValidationError('仅已确认的预约可使用积分报价')
    if reservation.required_provider_count == 2:
        seats = list(reservation.seats.order_by('number').values_list('status', 'provider_account_id'))
        if len(seats) != 2 or any(status != 'CONFIRMED' or not provider for status, provider in seats):
            raise ValidationError('双陪预约人员尚未全部确认')
        providers = [provider for _, provider in seats]
    else:
        providers = [reservation.provider_account_id]
    return {'store': store.slug, 'reservation': reservation.pk,
            'customer': reservation.customer_account_id, 'service': reservation.service_id,
            'providers': providers, 'start': reservation.start_time.isoformat(),
            'end': reservation.end_time.isoformat(), 'duration': reservation.duration_minutes,
            'payment_deadline': reservation.payment_deadline.isoformat() if reservation.payment_deadline else None}


def quote(account, reservation, data, selected):
    from .points_business_access import permits
    if not permits(account=account, service=data.get('service'), phase='checkout', kind='service'):
        require_isolated_checkout()
    if not assignment_enabled() or account.pk != reservation.customer_account_id:
        raise ValidationError('预约积分支付及派单尚未接通')
    actual, inner = quote_for_checkout(account, data, selected)
    token = signing.dumps({'reservation': _snapshot(reservation), 'checkout': inner}, salt=SALT, compress=True)
    return actual, token


def verify(account, reservation, data, token):
    from .points_business_access import permits
    if not permits(account=account, service=data.get('service'), phase='checkout', kind='service'):
        require_isolated_checkout()
    if not assignment_enabled() or account.pk != reservation.customer_account_id:
        raise ValidationError('预约积分支付及派单尚未接通')
    if not isinstance(token, str) or not token or len(token) > 16384:
        raise ValidationError('预约积分报价无效，请重新核对')
    try:
        envelope = signing.loads(token, salt=SALT, max_age=600)
    except signing.BadSignature:
        raise ValidationError('预约积分报价已失效，请重新核对') from None
    if (not isinstance(envelope, dict) or set(envelope) != {'reservation', 'checkout'}
            or envelope['reservation'] != _snapshot(reservation) or not isinstance(envelope['checkout'], str)):
        raise ValidationError('预约或陪玩已变化，请重新报价')
    return verify_checkout_quote(account, data, envelope['checkout'])
