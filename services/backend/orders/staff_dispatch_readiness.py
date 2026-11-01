"""Read-side staff dispatch eligibility; mutation rechecks under order lock.

The SQL predicate is deliberately conservative for untrusted direct-payment
records and is shared by the staff queue's list and count. A full quote hash
verification cannot be expressed portably in the paginated SQL query; the
detail action independently performs that stricter check.
"""
from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from .models import Order, OrderProvider
from .team_rules import required_team_size


def staff_dispatch_candidate_q(*, now=None):
    now = now or timezone.now()
    from .direct_payment import test_domain_available
    team = (Q(escort_mode=Order.EscortMode.DOUBLE) | Q(required_provider_count=2)
            | Q(escort_mode=Order.EscortMode.SINGLE, required_provider_count__isnull=True,
                gameplay_snapshot__has_key='billing_provider_count',
                gameplay_snapshot__billing_provider_count=2))
    any_provider = Exists(OrderProvider.objects.filter(order_id=OuterRef('pk')))
    payment_candidate = Q(direct_payment__isnull=True)
    if test_domain_available():
        payment_candidate |= Q(
            direct_payment__status='PAID', direct_payment__evidence_risk_at__isnull=True,
            direct_payment__fee_quote__contract_version='FEE_QUOTE_V2',
            direct_payment__fee_quote__service_units=F('amount'),
            direct_payment__fee_quote__total_fen=F('direct_payment__amount_fen'),
        )
    return (
        Q(dispatch_route=Order.DispatchRoute.STAFF,
          status=Order.Status.PENDING, payment_status=Order.PaymentStatus.PAID,
          provider__isnull=True, provider_account__isnull=True)
        & (Q(auto_cancel_at__isnull=True) | Q(auto_cancel_at__gt=now))
        & (~team | ~Q(any_provider))
        & payment_candidate
    )


def dispatch_assignment_allowed(order, *, now=None):
    """Whether an order can enter a staff assignment flow, not provider eligibility."""
    if (order.status != Order.Status.PENDING or order.payment_status != Order.PaymentStatus.PAID
            or order.provider_id or order.provider_account_id
            or (order.auto_cancel_at and order.auto_cancel_at <= (now or timezone.now()))):
        return False
    size = required_team_size(order)
    if size != 1 and not (size == 2 and order.dispatch_route == Order.DispatchRoute.STAFF):
        return False
    if size == 2 and order.providers.exists():
        return False
    from .direct_payment_models import DirectPaymentIntent
    from .direct_payment_quote import quote_matches_intent
    if 'direct_payment' in order._state.fields_cache:
        intent = order._state.fields_cache['direct_payment']
    else:
        intent = DirectPaymentIntent.objects.filter(order=order).first()
    return (intent is None or (intent.status == DirectPaymentIntent.Status.PAID
            and intent.evidence_risk_at is None and quote_matches_intent(intent, order)))
