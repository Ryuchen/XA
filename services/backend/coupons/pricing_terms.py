"""Read-only effective terms for existing VIP coupons during checkout."""
from copy import copy
from datetime import date

from rest_framework.exceptions import ValidationError

from engagement.models import VipTier
from .models import VipMonthlyCouponGrant, VipMonthlyCutover


def safety_box_rate(tier, *, locked=False, existing_coupon=False):
    cutover = VipMonthlyCutover.objects.filter(pk=1)
    if locked:
        cutover = cutover.select_for_update()
    # Read the registered marker under the caller's lock when requested;
    # first-edition pricing is implemented with or without the old switch.
    cutover.first()
    rate = tier.season_discount
    if type(rate) is not int or not 0 <= rate <= 100:
        raise ValidationError('门店安全箱券折扣配置无效')
    return rate


def effective_coupon(account, owned, *, vip_enabled, locked=False):
    """Copy only mutable private VIP terms; never create or modify benefits."""
    coupon = owned.coupon
    if not vip_enabled or owned.vip_terms_locked_by_downgrade or not account.boss_type_id:
        return coupon
    tiers = VipTier.objects.filter(active=True, boss_type_id=account.boss_type_id,
                                   boss_type__is_active=True)
    if locked:
        tiers = tiers.select_for_update()
    tier = tiers.first()
    if tier is None:
        return coupon
    key = coupon.grant_key or ''
    prefix = f'vip-month:{account.pk}:{account.business_cycle}:'
    adjusted = copy(coupon)
    if key.startswith(prefix):
        try:
            month = date.fromisoformat(key[len(prefix):].split(':', 1)[0] + '-01')
        except ValueError:
            return coupon
        grants = VipMonthlyCouponGrant.objects.filter(account=account, business_cycle=account.business_cycle,
            month=month, source='SYSTEM', terms_frozen=False, tier_id_snapshot=tier.pk)
        if locked:
            grants = grants.select_for_update()
        grant = grants.first()
        if not grant:
            return coupon
        if not 1 <= tier.monthly_discount <= 100:
            raise ValidationError('门店月券折扣配置无效')
        adjusted.discount_rate = tier.monthly_discount
        from engagement.vip_policy import issuance_policy
        adjusted.max_discount = issuance_policy(tier)['monthly_max_discount']
        return adjusted
    if key.startswith(f'vip-safe:{account.pk}:{account.business_cycle}:') and coupon.scope == 'SAFE_BOX':
        adjusted.discount_rate = safety_box_rate(tier, locked=locked, existing_coupon=True)
        adjusted.service_ids = tier.safe_box_service_ids or []
        return adjusted
    return coupon


def materialize_effective_coupon(account, owned, *, vip_enabled):
    """Persist the terms actually used while the caller holds the coupon locks."""
    from stores.db import transaction
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('优惠券付款条款必须在本店事务中保存')
    coupon = owned.coupon
    effective = effective_coupon(account, owned, vip_enabled=vip_enabled, locked=True)
    fields = ('discount_rate', 'max_discount', 'service_ids', 'valid_to')
    changed = [field for field in fields if getattr(coupon, field) != getattr(effective, field)]
    if changed:
        for field in changed:
            setattr(coupon, field, getattr(effective, field))
        coupon.save(update_fields=changed)
        from .notifier import notify_owner_coupon_changed
        transaction.on_commit(lambda: notify_owner_coupon_changed(account.pk))
    return coupon
