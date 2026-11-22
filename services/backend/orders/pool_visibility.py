"""Existing provider pool visibility shared by listing and order consultation."""
from datetime import timedelta
from django.db.models import F, Q
from django.db import DatabaseError
from django.utils import timezone
from .models import Order, OrderGrabRound
from .services import count_capacity_orders
from .capacity import current_capacity_limit
from users.models import EscortProfile


def pending_deadline_expired(order, *, now=None):
    """Return True/False, or None when a real pool cutoff is uncertain."""
    if order.status != Order.Status.PENDING:
        return False
    from .grab_clock_contract import production_enabled
    if production_enabled():
        round_ = (OrderGrabRound.objects.filter(pool__order_id=order.pk)
                  .select_related('pool__order').order_by('-number').first())
        if round_:
            from .grab_receipt_probe import _db_clock
            from .grab_round_opening import cutoff_bounds
            try:
                lower, upper = cutoff_bounds(round_, 'PARKED')
                clock_now, precision, _ = _db_clock()
            except (ValueError, DatabaseError):
                return None
            quantum = timedelta(microseconds=precision)
            if clock_now - quantum >= upper:
                return True
            if clock_now + quantum < lower:
                return False
            return None
    return bool(order.auto_cancel_at and order.auto_cancel_at <= (now or timezone.now()))

def visible_provider_pool(account):
    profile = EscortProfile.objects.filter(account=account).select_related('account', 'user', 'level').prefetch_related(
        'user__schedules', 'qualifications__game_category', 'service_items').first()
    # A refund-created deposit deficit may permit exactly one recovery
    # order. The assignment transaction still reserves that permit under lock.
    available = bool(profile and not profile.order_acceptance_error(allow_refund_recovery=True))
    active_count = count_capacity_orders(account)
    capacity_limit = current_capacity_limit()
    if not available or active_count >= capacity_limit:
        return Order.objects.none(), None
    delay = profile.order_visibility_delay_seconds
    from .grab_clock_contract import production_enabled
    if production_enabled():
        from .grab_receipt_probe import _db_clock
        now, precision, _ = _db_clock()
        visible_now = now - timedelta(microseconds=precision)
    else:
        now = visible_now = timezone.now()
    visible_before = visible_now - timedelta(seconds=delay)
    from .direct_payment import test_domain_available
    payment_candidate = Q(direct_payment__isnull=True)
    if test_domain_available():
        payment_candidate |= Q(direct_payment__status='PAID',
            direct_payment__fee_quote__contract_version='FEE_QUOTE_V2',
            direct_payment__fee_quote__service_units=F('amount'),
            direct_payment__fee_quote__total_fen=F('direct_payment__amount_fen'))
    from .grab_single_flow import ADMISSION_STATES, enabled as single_enabled
    from .grab_team_flow import enabled as team_enabled
    from .grab_round_facts import latest_status_for_pool
    deadline_candidates = Q(auto_cancel_at__isnull=True) | Q(auto_cancel_at__gt=now)
    if production_enabled():
        # auto_cancel_at was sampled before the opening transaction committed.
        # Include pool orders provisionally; below, an observed round's actual
        # cutoff can prove that its full waiting period has not yet elapsed.
        deadline_candidates |= Q(grab_pool_snapshot__isnull=False)
    orders = Order.objects.filter(
        deadline_candidates,
        provider_account__isnull=True, provider__isnull=True,
        source_order__isnull=True, status=Order.Status.PENDING, payment_status=Order.PaymentStatus.PAID,
    ).filter(Q(dispatch_route=Order.DispatchRoute.PUBLIC)
             | Q(dispatch_route=Order.DispatchRoute.STAFF, grab_pool_snapshot__isnull=False)
    ).filter(Q(provider_gender='ANY') | Q(provider_gender=profile.gender)).exclude(is_gift_snapshot=True).filter(payment_candidate)
    # Both audition sizes use buyer selection. Automatic orders need an admitting round.
    eligible_modes = Q(escort_mode__in=(Order.EscortMode.SINGLE, Order.EscortMode.DOUBLE),
                       dispatch_route=Order.DispatchRoute.PUBLIC,
                       requires_boss_selection=True, grab_pool_snapshot__isnull=True)
    if single_enabled():
        orders = orders.alias(single_round_status=latest_status_for_pool(1))
        eligible_modes |= Q(escort_mode=Order.EscortMode.SINGLE, requires_boss_selection=False,
                            grab_pool_snapshot__required_provider_count=1,
                            single_round_status__in=ADMISSION_STATES)
    if team_enabled():
        orders = orders.alias(team_round_status=latest_status_for_pool(2))
        eligible_modes |= Q(escort_mode=Order.EscortMode.DOUBLE, requires_boss_selection=False,
            grab_pool_snapshot__required_provider_count=2,
            team_round_status__in=ADMISSION_STATES)
    orders = orders.filter(eligible_modes).exclude(grab_review_cancellation__status='PENDING')
    if production_enabled():
        from .grab_round_opening import cutoff_bounds
        # Only orders excluded by the provisional timestamp need this extra
        # check. Missing activation evidence cannot extend their visibility.
        provisional_expired = list(orders.filter(grab_pool_snapshot__isnull=False,
            auto_cancel_at__lte=now).values_list('pk', flat=True).distinct())
        still_open = []
        for order_id in provisional_expired:
            round_ = (OrderGrabRound.objects.filter(pool__order_id=order_id)
                      .select_related('pool__order').order_by('-number').first())
            if not round_:
                continue
            try:
                _, total_upper = cutoff_bounds(round_, 'PARKED')
            except (ValueError, DatabaseError):
                continue
            if visible_now < total_upper:
                still_open.append(order_id)
        orders = orders.filter(Q(auto_cancel_at__isnull=True) | Q(auto_cancel_at__gt=now)
                               | Q(pk__in=still_open))
    from .grab_pool_visibility import visibility_for_orders
    times = visibility_for_orders(orders.values_list('pk', flat=True), profile)
    visible_ids = [order_id for order_id, at in times.items() if at <= visible_now]
    legacy = (Q(grab_pool_snapshot__isnull=True, grab_opening_intent__isnull=True)
              & (Q(pool_entered_at__lte=visible_before) | Q(pool_entered_at__isnull=True, created_at__lte=visible_before)))
    orders = orders.filter(legacy | Q(pk__in=visible_ids))
    from users.qualifications import qualified_order_q, authorized_skill_q
    from .paid_service_scope import enabled as paid_scope_enabled
    if paid_scope_enabled():
        from .paid_service_scope import acceptance_error
        saved = orders.filter(paid_service_scope__isnull=False).select_related(
            'paid_service_scope', 'service', 'service__required_level', 'service__game_category', 'service__service_category')
        eligible_ids = [order.pk for order in saved
                        if not acceptance_error(profile, order, allow_refund_recovery=True)]
        orders = orders.filter((Q(paid_service_scope__isnull=True) & qualified_order_q(profile) & authorized_skill_q(profile)) |
                               Q(pk__in=eligible_ids))
    else:
        orders = orders.filter(qualified_order_q(profile)).filter(authorized_skill_q(profile))
    return orders.distinct(), (profile, available, active_count, delay, capacity_limit)
