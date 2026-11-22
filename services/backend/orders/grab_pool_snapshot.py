"""Persist initial pool configuration and integrate single-player entry.

The caller opens the pool inside its order/payment transaction. This module
does not register candidates, perform ranked allocation or cancel orders.

It is not ready for full ranked admission: opened_at is captured before this outer
transaction commits. A slow snapshot or commit could consume the window
before any provider can observe it. The eventual integration must prove a
durable opening time as well as safe lock ordering and bounded capture cost.
"""
from datetime import timedelta

from django.utils import timezone
from stores.db import transaction

from .grab_config import CONFIG_KEY, parse_rules, snapshot_for_round
from .models import Order, OrderGrabPoolPassSnapshot, OrderGrabPoolSnapshot, Reservation
from wallet.models import SystemConfig


def create_initial_snapshot(order_id, *, committed_opening=False):
    """Save one immutable snapshot, under the caller's tenant transaction.

    The order must be locked by this function. A retry cannot overwrite a
    snapshot; it returns the existing record even after the store changes its
    configuration. The timeout duration is the order's payment-time snapshot.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('抢单池快照须与订单入池在同一门店事务内提交')
    order = Order.objects.select_for_update().get(pk=order_id)
    # Current read after the order lock: on MySQL REPEATABLE READ an earlier
    # consistent read in the caller's transaction may predate another opening.
    prior = OrderGrabPoolSnapshot.objects.select_for_update().filter(order=order).first()
    if prior:
        if (not prior.pass_snapshot_complete or prior.pass_snapshot_count is None
                or len(list(prior.entry_passes.select_for_update().values_list('pk', flat=True))) != prior.pass_snapshot_count):
            raise ValueError('订单入池通行证快照不完整，请先核对')
        return prior
    if (order.status != Order.Status.PENDING or order.payment_status != Order.PaymentStatus.PAID
            or order.provider_id or order.provider_account_id or order.providers.exists()
            or order.is_gift_snapshot or order.source_order_id or order.requires_boss_selection
            or Reservation.objects.filter(order=order).exists()):
        raise ValueError('订单不符合自动抢单池条件')
    count = order.required_provider_count
    if count not in (1, 2):
        raise ValueError('订单陪玩人数缺少有效成交快照，请先核对')
    minutes = order.pending_timeout_minutes
    if type(minutes) is not int or not 1 <= minutes <= 1440:
        raise ValueError('订单等待时限缺少有效成交快照，请先核对')
    # Freeze both independently editable configurations before taking the
    # entry time. Otherwise a blocked writer could change the window or delays
    # between the recorded opening and the time their values were captured.
    from users.pass_rules import lock_pass_config
    lock_pass_config()
    config = SystemConfig.objects.select_for_update().filter(key=CONFIG_KEY).first()
    if config is None:
        raise ValueError('本店尚未配置抢单窗口')
    parse_rules(config.value)
    # Lock all profiles whose card could still be active at entry. Capture
    # identities before recording entry, even for currently offline players.
    # All live pass purchases/refunds use the same pass configuration lock.
    from users.models import EscortProfile
    from .grab_receipt_probe import _db_clock
    before_lock = _db_clock()[0] if committed_opening else timezone.now()
    profiles = list(EscortProfile.objects.select_for_update().filter(
        pass_expires_at__gt=before_lock).exclude(pass_tier='').order_by('pk')
        .values('pk', 'user_id', 'account_id', 'pass_tier', 'pass_expires_at'))
    opened_at = _db_clock()[0] if committed_opening else timezone.now()
    original_total_deadline = opened_at + timedelta(minutes=minutes)
    if timezone.is_naive(opened_at) or opened_at < before_lock or opened_at < order.created_at:
        raise ValueError('订单入池时间无效')
    rules = snapshot_for_round(opened_at=opened_at, original_total_deadline=original_total_deadline)
    pool = OrderGrabPoolSnapshot.objects.create(
        order=order, opened_at=rules['opened_at'], original_total_deadline=original_total_deadline,
        first_deadline=rules['deadline'], total_deadline=rules['total_deadline'],
        window_seconds=rules['window_seconds'], config_version=rules['config_version'],
        visibility_delays=rules['delays'], required_provider_count=count,
    )
    eligible = [row for row in profiles if row['pass_expires_at'] > opened_at]
    valid_tiers = {'BLACK', 'GOLD', 'SILVER', 'BRONZE'}
    if any(row['pass_tier'] not in valid_tiers for row in eligible):
        raise ValueError('陪玩通行证档位异常，请先核对')
    OrderGrabPoolPassSnapshot.objects.bulk_create([
        OrderGrabPoolPassSnapshot(pool=pool, profile_id_snapshot=row['pk'], user_id_snapshot=row['user_id'],
            account_id_snapshot=row['account_id'], tier=row['pass_tier'], expires_at=row['pass_expires_at'])
        for row in eligible
    ], batch_size=500)
    pool.pass_snapshot_complete = True
    pool.pass_snapshot_count = len(eligible)
    pool.save(update_fields=['pass_snapshot_complete', 'pass_snapshot_count'])
    return pool


def initialize_entry(order, *, committed_opening=False):
    """Capture automatic order entry in its payment transaction."""
    from django.conf import settings
    if not committed_opening and getattr(settings, 'GRAB_COMMITTED_OPENING_ENABLED', False):
        return None
    if not (getattr(settings, 'GRAB_POOL_ENTRY_SNAPSHOT_ENABLED', False) or committed_opening):
        return None
    if (order.provider_id or order.provider_account_id or order.requires_boss_selection
            or order.required_provider_count not in (1, 2) or order.source_order_id or order.is_gift_snapshot):
        return None
    if order.required_provider_count == 2:
        from .grab_team_flow import enabled as team_window_enabled
        if not committed_opening and not team_window_enabled():
            return None
    from .grab_team_draft import first_round
    pool = create_initial_snapshot(order.pk, committed_opening=committed_opening)
    round_ = first_round(pool.pk)
    if committed_opening:
        from .grab_round_opening import stage
        stage(round_)
    from .grab_single_flow import enabled as single_window_enabled
    if order.required_provider_count == 1 and (single_window_enabled() or committed_opening):
        from .models import OrderGrabSingleRoundState
        OrderGrabSingleRoundState.objects.get_or_create(round=round_)
        from .grab_single_flow import enqueue
        if single_window_enabled():
            transaction.on_commit(lambda: enqueue(order.pk, eta=round_.deadline, expected_round_id=round_.pk))
    elif order.required_provider_count == 2:
        from .models import OrderGrabTeamRoundState
        from .grab_team_flow import enqueue
        OrderGrabTeamRoundState.objects.get_or_create(round=round_)
        if team_window_enabled():
            transaction.on_commit(lambda: enqueue(order.pk, eta=round_.deadline))
    order.pool_entered_at = pool.opened_at
    order.auto_cancel_at = pool.total_deadline
    order.save(update_fields=['pool_entered_at', 'auto_cancel_at', 'updated_at'])
    return pool
