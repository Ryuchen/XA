"""Preserve tier rules at VIP shutdown for later store-local reconciliation."""
from dataclasses import asdict

from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import VipDeferredRecalculation
from .vip_recalculation_plan import read_store_snapshot


def _locked_row():
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('VIP 延期配置必须在门店事务中执行')
    VipDeferredRecalculation.objects.get_or_create(pk=1)
    return VipDeferredRecalculation.objects.select_for_update().get(pk=1)


def capture_disable_baseline():
    """Called with the store configuration locked, before disabling VIP."""
    row = _locked_row()
    if row.is_open:
        raise ValidationError('本店存在尚未处理的 VIP 停用周期，请先核对')
    tiers, _ = read_store_snapshot()
    row.epoch += 1
    row.is_open = True
    row.baseline_rules = [asdict(tier) for tier in tiers]
    row.pending = False
    row.opened_at = timezone.now()
    row.closed_at = None
    row.save(update_fields=['epoch', 'is_open', 'baseline_rules', 'pending', 'opened_at', 'closed_at', 'updated_at'])
    return row



def disable_evidence(row):
    """Audit the actual tier terms alongside the compact recalculation rules."""
    from .models import VipTier
    return {'baseline_rules': row.baseline_rules, 'tier_terms': list(
        VipTier.objects.order_by('pk').values(
            'id', 'boss_type_id', 'level', 'recharge_threshold', 'active',
            'points_rate', 'benefits', 'upgrade_rewards', 'companion_service_ids',
            'safe_box_service_ids', 'monthly_discount', 'monthly_coupon_count', 'season_discount'))}


def mark_deferred():
    """Record a change without fabricating a missing historic baseline."""
    row = _locked_row()
    if not row.is_open:
        row.epoch += 1
        row.is_open = True
        row.baseline_rules = None
        row.opened_at = timezone.now()
        row.closed_at = None
    row.pending = True
    row.save(update_fields=['epoch', 'is_open', 'baseline_rules', 'pending', 'opened_at', 'closed_at', 'updated_at'])
    return row


def close_empty_setup_period():
    """Close an empty initialization period without manufacturing entitlements.

    Caller holds the BOSS quota lock and the operation-config lock.
    """
    from club_accounts.models import ClubAccount
    if ClubAccount.objects.select_for_update().filter(account_type='BOSS').exists():
        raise ValidationError('老板账号已变化，请重新核对 VIP 重算')
    row = VipDeferredRecalculation.objects.select_for_update().filter(pk=1, is_open=True).first()
    if row is None:
        return None
    row.is_open = False
    row.pending = False
    row.closed_at = timezone.now()
    row.save(update_fields=['is_open', 'pending', 'closed_at', 'updated_at'])
    return row
