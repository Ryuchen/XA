"""Read formal pool timing once for a provider's request."""
from collections import defaultdict
from datetime import datetime, timedelta
from itertools import pairwise
from typing import NamedTuple

from django.db import DatabaseError
from django.db.models import Count, OuterRef, Subquery
from django.db.models.functions import Coalesce

from stores.db import connection
from .grab_cutoff_watermark import matches_seal
from .grab_pool_visibility import _validated_delays, visibility_from_entry
from .grab_round_opening import _opening_interval, cutoff_bounds_from_rounds, provisional_lower, validated_opening
from .models import OrderGrabPoolPassSnapshot, OrderGrabPoolSnapshot, OrderGrabReceiptProbe, OrderGrabRound


class PoolTiming(NamedTuple):
    round_id: int
    visible_at: datetime
    expired: bool | None


def formal_pool_times(order_ids, profile, clock):
    now, driver, observation = clock
    identity = observation['database_identity']
    # UTC_TIMESTAMP(6) from the formal MySQL sample has microsecond precision.
    precision_us = 1
    quantum = timedelta(microseconds=precision_us)
    entry_counts = (OrderGrabPoolPassSnapshot.objects.filter(pool_id=OuterRef('pk'))
                    .order_by().values('pool_id').annotate(total=Count('pk')).values('total'))
    pools = {pool.pk: pool for pool in OrderGrabPoolSnapshot.objects.filter(order_id__in=order_ids)
             .select_related('order').only('id', 'order_id', 'opened_at', 'original_total_deadline',
                 'window_seconds', 'required_provider_count', 'pass_snapshot_complete', 'pass_snapshot_count',
                 'visibility_delays', 'order__pending_timeout_minutes', 'order__auto_cancel_at')
             .annotate(entry_count=Coalesce(Subquery(entry_counts), 0))}
    entries = {entry.pool_id: entry for entry in OrderGrabPoolPassSnapshot.objects.filter(
        pool_id__in=pools, profile_id_snapshot=profile.pk)}
    histories = defaultdict(list)
    rounds = OrderGrabRound.objects.filter(pool_id__in=pools).order_by('pool_id', 'number').select_related(
        'opening_evidence', 'cutoff_watermark', 'single_state', 'team_state')
    for row in rounds:
        row.pool = pools[row.pool_id]
        histories[row.pool_id].append(row)

    def reopened_with_seal(row):
        state_name = 'single_state' if row.pool.required_provider_count == 1 else 'team_state'
        state = getattr(row, state_name, None)
        return getattr(state, 'status', None) == 'REOPENED' and getattr(row, 'cutoff_watermark', None)

    sealed_rounds = {previous.pk for history in histories.values() for previous, following in pairwise(history)
                     if following.simultaneous_visibility and reopened_with_seal(previous)}
    receipts = defaultdict(list)
    for row in OrderGrabReceiptProbe.objects.filter(round_id__in=sealed_rounds, round_phase='OPEN').order_by('pk'):
        receipts[row.round_id].append(row)
    if connection.connection is not driver:
        raise DatabaseError('抢单池读取期间数据库连接变化')

    def read_opening(row):
        return validated_opening(getattr(row, 'opening_evidence', None), identity=identity)

    def read_seal(previous, cutoff, reopened_at):
        if previous.pk in sealed_rounds:
            mark = previous.cutoff_watermark
            if matches_seal(mark, receipts[previous.pk], cutoff=cutoff, observed_at=reopened_at,
                            precision_us=precision_us, identity=identity):
                return mark
        return None

    timings = {}
    for pool_id, history in histories.items():
        pool = pools[pool_id]
        first, latest = history[0], history[-1]
        opening = latest if latest.simultaneous_visibility else first
        opening_pending = False
        try:
            if not latest.simultaneous_visibility and first.number != 1:
                continue
            try:
                lower, upper = _opening_interval(read_opening(opening))
            except ValueError:
                lower = upper = provisional_lower(getattr(opening, 'opening_evidence', None), identity=identity)
                opening_pending = True
            visible_at = upper
            if not latest.simultaneous_visibility:
                delays = _validated_delays(pool, entry_count=pool.entry_count)
                visible_at = visibility_from_entry(pool, profile, entries.get(pool_id), delays,
                    lower=lower, upper=upper, opening_pending=opening_pending).visible_at
        except ValueError:
            continue
        if visible_at > now - quantum:
            continue

        expired = None
        provisional = pool.order.auto_cancel_at
        try:
            total_lower, total_upper = cutoff_bounds_from_rounds(
                latest, 'PARKED', lambda: history, read_opening=read_opening, read_seal=read_seal)
            if provisional and provisional <= now and now - quantum >= total_upper:
                continue
            if now - quantum >= total_upper:
                expired = True
            elif now + quantum < total_lower:
                expired = False
        except ValueError:
            if provisional and provisional <= now:
                continue
        timings[pool.order_id] = PoolTiming(latest.pk, visible_at, expired)
    return timings
