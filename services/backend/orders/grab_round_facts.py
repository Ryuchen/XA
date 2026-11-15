"""Validate persisted automatic-pool rounds against their frozen rules.

The caller owns order locking when the result will be used for a mutation.
These facts do not prove why a round was reopened or authorize allocation.
"""
from datetime import timedelta

from stores.db import transaction


def current_round(pool, round_id, *, lock=False):
    if type(round_id) is not int or round_id <= 0:
        raise ValueError('抢单轮次无效')
    if lock and not transaction.get_connection().in_atomic_block:
        raise RuntimeError('抢单轮次锁定须在门店事务内处理')
    if (type(pool.window_seconds) is not int or not 1 <= pool.window_seconds <= 86400
            or pool.first_deadline != pool.opened_at + timedelta(seconds=pool.window_seconds)
            or pool.total_deadline != max(pool.original_total_deadline, pool.first_deadline)):
        raise ValueError('抢单池窗口快照不一致，请先核对')
    query = pool.rounds.order_by('number')
    if lock:
        query = query.select_for_update()
    rounds = list(query)
    if not rounds:
        raise ValueError('抢单轮次尚未建立')
    first = rounds[0]
    if (first.number != 1 or first.simultaneous_visibility or first.opened_at != pool.opened_at
            or first.deadline != pool.first_deadline or first.total_deadline != pool.total_deadline):
        raise ValueError('首轮抢单快照不一致，请先核对')
    previous = first
    for number, row in enumerate(rounds[1:], 2):
        if (row.number != number or not row.simultaneous_visibility or row.opened_at < previous.deadline
                or row.deadline != row.opened_at + timedelta(seconds=pool.window_seconds)
                or row.total_deadline != max(previous.total_deadline, row.deadline)):
            raise ValueError('重开抢单轮次不一致，请先核对')
        previous = row
    if previous.pk != round_id:
        raise ValueError('抢单轮次已结束或不属于本订单')
    return previous
