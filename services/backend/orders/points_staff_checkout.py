"""Frozen commission evidence for a staff-created points grab order."""
from rest_framework.exceptions import ValidationError
from .models import Order


def pending_quote_data(data):
    from .commission import resolve, snapshot
    from .pricing import PricingError
    try:
        plan = resolve(data['service'], data['amount'], profile=None, promotion=data['promotion'])
    except PricingError as exc:
        raise ValidationError(str(exc)) from exc
    if not plan.get('commission_allocation_pending'):
        return data
    return {**data, 'required_provider_count': data['service'].fulfillment_provider_count,
            'provider_accounts': [], 'dispatch_mode': 'AUTO_GRAB', 'dispatch_route': Order.DispatchRoute.STAFF,
            'commission_allocation_pending': True, 'commission_rate': None,
            'commission_snapshot': snapshot(plan)}
