"""Existing provider pool visibility shared by listing and order consultation."""
from datetime import datetime, timedelta
from typing import NamedTuple
from django.db.models import Exists, F, OuterRef, Q, Subquery, Value
from django.db import DatabaseError, InterfaceError
from django.utils import timezone
from rest_framework.exceptions import APIException
from .models import Order, OrderGrabRound
from .services import count_capacity_orders
from .capacity import current_capacity_limit
from users.models import EscortProfile


class PoolReadState(NamedTuple):
    profile: EscortProfile
    available: bool
    active_count: int
    delay: int
    capacity_limit: int
    visibility_times: dict[int, datetime]
    deadline_states: dict[int, bool | None]
    refund_deficits: list[dict] | None


class PoolClockUnavailable(APIException):
    status_code = 503
    default_detail = '抢单池时钟暂不可用，请稍后重试'
    default_code = 'grab_pool_clock_unavailable'


def with_pool_display_evidence(orders, account_id):
    """Load additional assignment facts for the pool."""
    from .models import OrderApplication, OrderStatusLog
    order_id = OuterRef('pk')
    return with_provider_list_evidence(orders, account_id).annotate(
        _pool_snapshot_exists=Q(grab_pool_snapshot__isnull=False),
        _pool_other_assignment=(
            Q(grab_opening_intent__isnull=False) | Q(grab_pool_snapshot__isnull=False)
            | Exists(OrderStatusLog.objects.filter(order_id=order_id, action='ASSIGN', reason='老板指定陪玩'))
        ),
        _pool_has_applications=Exists(OrderApplication.objects.filter(order_id=order_id)),
    )


def with_provider_list_evidence(orders, account_id):
    """Follow the requesting provider's fulfillment and settlement evidence."""
    from .models import (OrderOriginalAttribution, OrderParticipationSegment,
                         OrderSegmentSettlementManifest, OrderWorkStint)
    from .segment_financial_capture import covered_transfers
    from .team_progress import formal_transfer_display_enabled
    order_id = OuterRef('pk')
    if formal_transfer_display_enabled():
        orders = orders.annotate(_provider_has_points_transfer=Exists(covered_transfers(order_id).filter(
            previous_segment__attribution__financial_coverage__source='WALLET_POINTS')))
    return orders.annotate(
        _provider_display_account_id=Value(account_id),
        _provider_has_manifest=Exists(OrderSegmentSettlementManifest.objects.filter(order_id=order_id)),
        _provider_has_covered_transfer=Exists(covered_transfers(order_id, account_id)),
        _provider_has_segments=Exists(OrderParticipationSegment.objects.filter(
            attribution__order_id=order_id, provider_account_id=account_id)),
        _provider_has_attribution=Exists(OrderOriginalAttribution.objects.filter(order_id=order_id)),
        _provider_pending_work_stint=Subquery(OrderWorkStint.objects.filter(order_id=order_id,
            account_id=account_id, incoming__isnull=False, started_at__isnull=True,
            released_at__isnull=True).order_by('pk').values('pk')[:1]),
    )


def pending_deadline_expired(order, *, now=None):
    """Return True/False, or None when a real pool cutoff is uncertain."""
    if order.status != Order.Status.PENDING:
        return False
    from .grab_clock_contract import production_enabled
    if production_enabled() and getattr(order, '_pool_snapshot_exists', True):
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

def visible_provider_pool(account, *, order_id=None):
    profile = EscortProfile.objects.filter(account=account).select_related('account', 'user', 'level').prefetch_related(
        'user__schedules', 'qualifications__game_category', 'service_items').first()
    from wallet.refund_deposit import protection_enabled
    refund_deficits = profile.open_refund_deficits() if profile and protection_enabled() else None
    # A refund-created deposit deficit may permit exactly one recovery
    # order. The assignment transaction still reserves that permit under lock.
    available = bool(profile and not profile.order_acceptance_error(
        allow_refund_recovery=True, refund_deficits=refund_deficits))
    active_count = count_capacity_orders(account)
    capacity_limit = current_capacity_limit()
    if not available or active_count >= capacity_limit:
        return Order.objects.none(), None
    delay = profile.order_visibility_delay_seconds
    from .grab_clock_contract import production_enabled, sample
    formal = production_enabled()
    if formal:
        try:
            clock = sample()
        except (ValueError, DatabaseError, InterfaceError) as exc:
            raise PoolClockUnavailable from exc
        now = clock[0]
        visible_now = now - timedelta(microseconds=1)
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
    if formal:
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
    if order_id is not None:
        orders = orders.filter(pk=order_id)
    # Both audition sizes use buyer selection. Automatic orders need an admitting round.
    eligible_modes = Q(escort_mode__in=(Order.EscortMode.SINGLE, Order.EscortMode.DOUBLE),
                       dispatch_route=Order.DispatchRoute.PUBLIC,
                       requires_boss_selection=True, grab_pool_snapshot__isnull=True)
    single_automatic, team_automatic = single_enabled(), team_enabled()
    if single_automatic:
        orders = orders.alias(single_round_status=latest_status_for_pool(1))
        eligible_modes |= Q(escort_mode=Order.EscortMode.SINGLE, requires_boss_selection=False,
                            grab_pool_snapshot__required_provider_count=1,
                            single_round_status__in=ADMISSION_STATES)
    if team_automatic:
        orders = orders.alias(team_round_status=latest_status_for_pool(2))
        eligible_modes |= Q(escort_mode=Order.EscortMode.DOUBLE, requires_boss_selection=False,
            grab_pool_snapshot__required_provider_count=2,
            team_round_status__in=ADMISSION_STATES)
    orders = orders.filter(eligible_modes).exclude(grab_review_cancellation__status='PENDING')
    legacy = (Q(grab_pool_snapshot__isnull=True, grab_opening_intent__isnull=True)
              & (Q(pool_entered_at__lte=visible_before) | Q(pool_entered_at__isnull=True, created_at__lte=visible_before)))
    times, deadlines = {}, {}
    if formal and (single_automatic or team_automatic):
        from .grab_pool_clock_read import formal_pool_times
        try:
            timings = formal_pool_times(orders.filter(grab_pool_snapshot__isnull=False).order_by()
                                        .values_list('pk', flat=True), profile, clock)
        except (DatabaseError, InterfaceError) as exc:
            raise PoolClockUnavailable from exc
        times = {pk: row.visible_at for pk, row in timings.items()}
        deadlines = {pk: row.expired for pk, row in timings.items()}
        recent = OrderGrabRound.objects.filter(pool_id=OuterRef('grab_pool_snapshot__pk')).order_by('-number')
        # Recheck the last round in the response query if it changed during the batch.
        orders = orders.alias(_pool_clock_round=Subquery(recent.values('pk')[:1]))
        orders = orders.filter(legacy | Q(_pool_clock_round__in=[row.round_id for row in timings.values()]))
    elif single_automatic or team_automatic:
        from .grab_pool_visibility import visibility_for_orders
        times = visibility_for_orders(orders.values_list('pk', flat=True), profile)
        orders = orders.filter(legacy | Q(pk__in=[pk for pk, at in times.items() if at <= visible_now]))
    else:
        orders = orders.filter(legacy)
    from users.qualifications import qualified_order_q, authorized_skill_q
    from .paid_service_scope import enabled as paid_scope_enabled
    if paid_scope_enabled():
        from .paid_service_scope import acceptance_error
        saved = orders.filter(paid_service_scope__isnull=False).select_related(
            'paid_service_scope', 'service', 'service__required_level', 'service__game_category', 'service__service_category')
        eligible_ids = [order.pk for order in saved
                        if not acceptance_error(profile, order, allow_refund_recovery=True, refund_deficits=refund_deficits)]
        orders = orders.filter((Q(paid_service_scope__isnull=True) & qualified_order_q(profile) & authorized_skill_q(profile)) |
                               Q(pk__in=eligible_ids))
    else:
        orders = orders.filter(qualified_order_q(profile)).filter(authorized_skill_q(profile))
    return orders.distinct(), PoolReadState(profile, available, active_count, delay, capacity_limit, times, deadlines,
                                            refund_deficits)
