"""Validate persisted automatic-pool rounds against their frozen rules.

The caller owns order locking when the result will be used for a mutation.
These facts do not prove why a round was reopened or authorize allocation.
"""
from datetime import timedelta

from django.db.models import OuterRef, Subquery
from stores.db import transaction
from .models import OrderGrabPoolSnapshot, OrderGrabRound, OrderGrabSingleRoundState, OrderGrabTeamRoundState


class GrabRoundReviewRequired(ValueError):
    """A paid pool exists, but the round cannot authorize a new state change."""

    def __init__(self, message, *, state=None):
        super().__init__(message)
        self.state = state


def pool_exists(order_id, *, seats=None):
    pools = OrderGrabPoolSnapshot.objects.filter(order_id=order_id)
    if seats is not None:
        pools = pools.filter(required_provider_count=seats)
    return pools.exists()


def latest_status_for_pool(seats):
    field = 'single_state__status' if seats == 1 else 'team_state__status'
    latest = OrderGrabRound.objects.filter(pool_id=OuterRef('grab_pool_snapshot__pk')).order_by('-number')
    return Subquery(latest.values(field)[:1])


def latest_round_state(order_id, *, seats, lock=False):
    model = OrderGrabSingleRoundState if seats == 1 else OrderGrabTeamRoundState
    states = model.objects.select_related('round__pool__order')
    if not lock:
        latest = OrderGrabRound.objects.filter(pool__order_id=order_id, pool__required_provider_count=seats)
        latest = latest.order_by('-number').values('pk')[:1]
        return states.filter(round_id=Subquery(latest)).first()

    # The caller already holds the order row lock. Re-read the round and
    # state as current rows in that same transaction before using either.
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('抢单状态锁定须在门店事务内处理')
    pool = OrderGrabPoolSnapshot.objects.select_for_update().filter(order_id=order_id).first()
    if pool is None:
        return None
    if pool.required_provider_count != seats:
        raise GrabRoundReviewRequired('抢单池人数待核对')
    rounds = _rounds(pool, lock=True)
    if not rounds:
        raise GrabRoundReviewRequired('抢单轮次尚未建立')
    state = states.select_for_update().filter(round=rounds[-1]).first()
    if state is None:
        raise GrabRoundReviewRequired('当前抢单轮次状态待核对')
    try:
        _validate_rounds(pool, rounds)
    except GrabRoundReviewRequired as exc:
        raise GrabRoundReviewRequired(str(exc), state=state) from exc
    return state


def current_round(pool, round_id, *, lock=False):
    if type(round_id) is not int or round_id <= 0:
        raise ValueError('抢单轮次无效')
    latest = _validate_rounds(pool, _rounds(pool, lock=lock))
    if latest.pk != round_id:
        raise ValueError('抢单轮次已结束或不属于本订单')
    return latest


def _rounds(pool, *, lock):
    if lock and not transaction.get_connection().in_atomic_block:
        raise RuntimeError('抢单轮次锁定须在门店事务内处理')
    query = pool.rounds.order_by('number')
    return list(query.select_for_update() if lock else query)


def _validate_rounds(pool, rounds):
    if (type(pool.window_seconds) is not int or not 1 <= pool.window_seconds <= 86400
            or pool.first_deadline != pool.opened_at + timedelta(seconds=pool.window_seconds)
            or pool.total_deadline != max(pool.original_total_deadline, pool.first_deadline)):
        raise GrabRoundReviewRequired('抢单池窗口快照不一致，请先核对')
    if not rounds:
        raise GrabRoundReviewRequired('抢单轮次尚未建立')
    first = rounds[0]
    if (first.number != 1 or first.simultaneous_visibility or first.opened_at != pool.opened_at
            or first.deadline != pool.first_deadline or first.total_deadline != pool.total_deadline):
        raise GrabRoundReviewRequired('首轮抢单快照不一致，请先核对')
    previous = first
    for number, row in enumerate(rounds[1:], 2):
        if (row.number != number or not row.simultaneous_visibility or row.opened_at < previous.deadline
                or row.deadline != row.opened_at + timedelta(seconds=pool.window_seconds)
                or row.total_deadline != max(previous.total_deadline, row.deadline)):
            raise GrabRoundReviewRequired('重开抢单轮次不一致，请先核对')
        previous = row
    return previous
