"""Pool visibility based on stored entry facts, not current card tier.

This computes a time only. Callers must separately enforce order state,
membership, binding, skills, deposit, capacity, and any reopening rules.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import OrderGrabPoolPassSnapshot, OrderGrabPoolSnapshot, OrderGrabRound
from users.models import EscortProfile


@dataclass(frozen=True)
class InitialVisibility:
    entry_tier: str
    delay_seconds: int
    visible_at: datetime
    visible_lower_at: datetime | None = None
    opening_pending: bool = False


def _validated_delays(pool, *, entry_count=None):
    """Validate the immutable entry evidence before using its timing rules."""
    if (not pool.pass_snapshot_complete or pool.pass_snapshot_count is None
            or (pool.entry_passes.count() if entry_count is None else entry_count) != pool.pass_snapshot_count):
        raise ValueError('订单入池通行证快照不完整，请先核对')
    delays = pool.visibility_delays
    tiers = ('BLACK', 'GOLD', 'SILVER', 'BRONZE', '')
    if (not isinstance(delays, dict) or set(delays) != set(tiers)
            or any(type(delays[tier]) is not int or not 0 <= delays[tier] <= 86400 for tier in tiers)
            or [delays[tier] for tier in tiers] != sorted(delays[tier] for tier in tiers)):
        raise ValueError('订单入池可见延迟快照异常，请先核对')
    return delays


def initial_mixed_group_visible_at(pool_id, *, lock=False):
    """Return the first-round public group time, not authority to send.

    A mixed group includes every pass tier and people without a pass. The
    eventual sender must still lock/recheck the order, round, tenant and
    channel, and handle reopening with simultaneous visibility separately.
    """
    if type(pool_id) is not int or pool_id <= 0:
        raise ValueError('抢单池无效')
    pools = OrderGrabPoolSnapshot.objects.filter(pk=pool_id)
    pool = (pools.select_for_update() if lock else pools).first()
    if pool is None:
        raise ValueError('本店抢单池不存在')
    delays = _validated_delays(pool)
    if not isinstance(pool.opened_at, datetime) or pool.opened_at.utcoffset() is None:
        raise ValueError('订单入池时间异常，请先核对')
    from .grab_clock_contract import production_enabled
    if production_enabled():
        from .grab_round_opening import opening_bounds
        rounds = pool.rounds.filter(number=1)
        first = (rounds.select_for_update() if lock else rounds).first()
        if not first:
            raise ValueError('抢单首轮提交证据待核对')
        return opening_bounds(first, lock=lock)[1] + timedelta(seconds=max(delays.values()))
    return pool.opened_at + timedelta(seconds=max(delays.values()))


def initial_visibility(pool_id, account_id):
    """Look up one account's entry-card evidence within the current store."""
    if type(pool_id) is not int or pool_id <= 0 or type(account_id) is not int or account_id <= 0:
        raise ValueError('抢单池或陪玩账号无效')
    pool = OrderGrabPoolSnapshot.objects.filter(pk=pool_id).first()
    profile = EscortProfile.objects.filter(account_id=account_id, account__account_type='PROVIDER').first()
    if pool is None or profile is None:
        raise ValueError('本店抢单池或陪玩账号不存在')
    delays = _validated_delays(pool)
    entry = OrderGrabPoolPassSnapshot.objects.filter(pool=pool, profile_id_snapshot=profile.pk).first()
    from .grab_clock_contract import production_enabled
    lower = upper = pool.opened_at
    opening_pending = False
    if production_enabled():
        from .grab_round_opening import opening_bounds, provisional_opening_lower
        first = pool.rounds.filter(number=1).first()
        if not first:
            raise ValueError('抢单首轮提交证据待核对')
        try:
            lower, upper = opening_bounds(first)
        except ValueError:
            lower = upper = provisional_opening_lower(first)
            opening_pending = True
    return visibility_from_entry(pool, profile, entry, delays, lower=lower, upper=upper,
                                 opening_pending=opening_pending)


def visibility_from_entry(pool, profile, entry, delays, *, lower, upper, opening_pending):
    tier = ''
    if entry:
        if (entry.user_id_snapshot != profile.user_id or entry.account_id_snapshot != profile.account_id
                or entry.tier not in ('BLACK', 'GOLD', 'SILVER', 'BRONZE') or entry.expires_at <= pool.opened_at):
            raise ValueError('订单入池时陪玩通行证归属无法核对')
        if opening_pending or entry.expires_at > upper:
            tier = entry.tier
        elif entry.expires_at > lower:
            raise ValueError('开池提交期间通行证到期，待核对')
    delay = delays[tier]
    return InitialVisibility(tier, delay, upper + timedelta(seconds=delay),
                             lower + timedelta(seconds=delay), opening_pending)


def visibility_for_orders(order_ids, profile):
    """Return saved per-order visibility times for a provider, in batch."""
    pools = list(OrderGrabPoolSnapshot.objects.filter(order_id__in=order_ids))
    if not pools:
        return {}
    pool_ids = [pool.pk for pool in pools]
    entries = {row.pool_id: row for row in OrderGrabPoolPassSnapshot.objects.filter(
        pool_id__in=pool_ids, profile_id_snapshot=profile.pk)}
    rounds = {}
    for row in OrderGrabRound.objects.filter(pool_id__in=pool_ids).order_by('pool_id', '-number'):
        rounds.setdefault(row.pool_id, row)
    results = {}
    for pool in pools:
        from .grab_clock_contract import production_enabled
        if production_enabled():
            try:
                round_ = rounds.get(pool.pk)
                if round_ and round_.simultaneous_visibility:
                    from .grab_round_opening import opening_bounds, provisional_opening_lower
                    try:
                        results[pool.order_id] = opening_bounds(round_)[1]
                    except ValueError:
                        results[pool.order_id] = provisional_opening_lower(round_)
                else:
                    results[pool.order_id] = initial_visibility(pool.pk, profile.account_id).visible_at
            except ValueError:
                pass
            continue
        entry = entries.get(pool.pk)
        tier = entry.tier if entry else ''
        if (not pool.pass_snapshot_complete or pool.pass_snapshot_count is None
                or tier not in pool.visibility_delays
                or (entry and (entry.account_id_snapshot != profile.account_id
                               or entry.user_id_snapshot != profile.user_id))):
            continue
        round_ = rounds.get(pool.pk)
        if round_ and round_.simultaneous_visibility:
            results[pool.order_id] = round_.opened_at
        else:
            results[pool.order_id] = pool.opened_at + timedelta(seconds=pool.visibility_delays[tier])
    return results


def order_visibility_time(order, profile):
    """Use the entry snapshot if present, otherwise the current legacy rule."""
    times = visibility_for_orders([order.pk], profile)
    if order.pk in times:
        return times[order.pk]
    if OrderGrabPoolSnapshot.objects.filter(order_id=order.pk).exists():
        raise ValueError('订单入池可见规则待核对')
    return (order.pool_entered_at or order.created_at) + timedelta(seconds=profile.order_visibility_delay_seconds)
