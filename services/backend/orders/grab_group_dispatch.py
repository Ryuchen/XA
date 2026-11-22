"""Mixed-group delivery timing for an explicitly enabled automatic pool.

Pool activation and durable command watermarks are separate concerns. This
module only admits pools whose entry and round facts are internally coherent.
"""
from django.conf import settings
from datetime import timedelta
from django.utils import timezone

from .grab_pool_visibility import initial_mixed_group_visible_at
from .grab_round_facts import GrabRoundReviewRequired, current_round, latest_round_state
from .models import OrderGrabPoolSnapshot
from .team_rules import required_team_size


class GroupRoundWaiting(ValueError):
    """Keep queued deliveries while the current round cannot authorize them."""


def enabled():
    return getattr(settings, 'GRAB_POOL_GROUP_DISPATCH_ENABLED', False) is True


def current_time():
    from .grab_clock_contract import production_enabled
    if production_enabled():
        from .grab_receipt_probe import _db_clock
        now, precision, _ = _db_clock()
        return now - timedelta(microseconds=precision)
    return timezone.now()


def deadline_reached(deadline):
    """Stop before an uncertain cutoff; only the database clock authorizes it."""
    from .grab_clock_contract import production_enabled
    if production_enabled():
        from .grab_receipt_probe import _db_clock
        now, precision, _ = _db_clock()
        return now + timedelta(microseconds=precision) >= deadline
    return timezone.now() >= deadline

def delivery_timing(order, *, lock=False):
    """Return (earliest send time, current total deadline), or reject facts.

    Callers modifying/sending an order hold its row lock. A prepared round is
    not proof of candidate registration or authority to assign an order.
    """
    pool = OrderGrabPoolSnapshot.objects.filter(order_id=order.pk).first()
    if not pool:
        raise ValueError('订单缺少抢单池快照，请先核对')
    seats = required_team_size(order)
    if order.pool_entered_at != pool.opened_at or pool.required_provider_count != seats:
        raise ValueError('订单入池或抢单窗口快照不一致，请先核对')
    try:
        state = latest_round_state(order.pk, seats=seats, lock=lock)
    except GrabRoundReviewRequired as exc:
        if exc.state is None:
            raise GroupRoundWaiting(str(exc)) from exc
        raise
    if not state or state.status not in ('OPEN', 'PARKED'):
        raise GroupRoundWaiting('当前抢单轮次待核对，暂不能发单')
    latest = state.round if lock else current_round(pool, state.round_id)
    if latest.opened_at > current_time() or order.auto_cancel_at != latest.total_deadline:
        raise ValueError('订单等待期限与抢单轮次不一致，请先核对')
    from .grab_clock_contract import production_enabled, round_identity_valid
    if production_enabled():
        from .grab_round_opening import cutoff_bounds, opening_bounds
        first = latest if latest.number == 1 else pool.rounds.get(number=1)
        # A group must not see a reopened order until activation is certain,
        # and cannot be sent once the earliest possible total deadline arrives.
        try:
            if not round_identity_valid(first, lock=lock):
                raise ValueError('原付款与首轮激活凭证待核对，暂不能发单')
            first_visible = initial_mixed_group_visible_at(pool.pk, lock=lock)
            due = opening_bounds(latest, lock=lock)[1] if latest.number > 1 else first_visible
            deadline = cutoff_bounds(latest, 'PARKED', lock=lock)[0]
        except ValueError as exc:
            raise GroupRoundWaiting(str(exc)) from exc
        return due, deadline
    first_visible = initial_mixed_group_visible_at(pool.pk, lock=lock)
    return (latest.opened_at if latest.number > 1 else first_visible), latest.total_deadline
