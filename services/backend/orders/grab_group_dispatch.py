"""Mixed-group delivery timing for an explicitly enabled automatic pool.

Pool activation and durable command watermarks are separate concerns. This
module only admits pools whose entry and round facts are internally coherent.
"""
from django.conf import settings
from datetime import timedelta
from django.utils import timezone

from .grab_pool_visibility import initial_mixed_group_visible_at
from .models import OrderGrabPoolSnapshot
from .team_rules import required_team_size


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

def delivery_timing(order):
    """Return (earliest send time, current total deadline), or reject facts.

    Callers modifying/sending an order hold its row lock. A prepared round is
    not proof of candidate registration or authority to assign an order.
    """
    pool = OrderGrabPoolSnapshot.objects.filter(order_id=order.pk).first()
    if not pool:
        raise ValueError('订单缺少抢单池快照，请先核对')
    first_visible = initial_mixed_group_visible_at(pool.pk)
    if (order.pool_entered_at != pool.opened_at or pool.required_provider_count != required_team_size(order)):
        raise ValueError('订单入池或抢单窗口快照不一致，请先核对')
    latest = pool.rounds.order_by('-number').first()
    if not latest:
        raise ValueError('订单抢单轮次尚未建立')
    from .grab_round_facts import current_round
    previous = current_round(pool, latest.pk)
    if previous.opened_at > current_time() or order.auto_cancel_at != previous.total_deadline:
        raise ValueError('订单等待期限与抢单轮次不一致，请先核对')
    from .grab_clock_contract import production_enabled
    if production_enabled():
        from .grab_round_opening import cutoff_bounds, opening_bounds
        # A group must not see a reopened order until activation is certain,
        # and cannot be sent once the earliest possible total deadline arrives.
        due = opening_bounds(previous)[1] if previous.number > 1 else first_visible
        deadline = cutoff_bounds(previous, 'PARKED')[0]
        return due, deadline
    return (previous.opened_at if previous.number > 1 else first_visible), previous.total_deadline
