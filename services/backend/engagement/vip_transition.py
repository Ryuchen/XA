"""Preserve current store-local VIP voucher terms before disabling VIP."""
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def transition_guard_enabled():
    if any(getattr(settings, name, False) for name in (
        'VIP_TERMS_FREEZE_ENABLED', 'VIP_MONTHLY_DECISIONS_ENABLED', 'DISCOUNT_COMBINATIONS_PAYMENT_ENABLED')):
        return True
    from coupons.models import VipMonthlyCutover
    return VipMonthlyCutover.objects.filter(pk=1).exists()


def transition_guard_available():
    if not transition_guard_enabled():
        return False
    # Both normal and registered-cutover first-edition paths are implemented.
    return True


def resume_guard_available():
    """Resume can use recorded coupons in either first-edition path."""
    return True


def freeze_existing_vip_terms(*, vip_enabled=True, account_id=None, kinds=('month', 'safe')):
    """Caller must hold the store operation-config lock and an atomic block."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('VIP 停用固化必须在门店事务中执行')
    from club_accounts.models import ClubAccount
    from coupons.models import Coupon, UserCoupon
    from coupons.pricing_terms import effective_coupon
    from engagement.models import VipTier
    from users.models import BossType

    # Matches order checkout's config -> BossType -> VipTier -> coupon order.
    type_ids = list(VipTier.objects.order_by('boss_type_id').values_list('boss_type_id', flat=True))
    list(BossType.objects.select_for_update().filter(pk__in=type_ids).order_by('pk').values_list('pk', flat=True))
    list(VipTier.objects.select_for_update().order_by('pk').values_list('pk', flat=True))
    if not kinds or any(kind not in ('month', 'safe') for kind in kinds):
        raise ValueError('无效的 VIP 券类型')
    coupon_kinds = Q()
    for kind in kinds:
        coupon_kinds |= Q(coupon__grant_key__startswith=f'vip-{kind}:')
    query = UserCoupon.objects.filter(status='UNUSED').filter(coupon_kinds)
    if account_id is not None:
        query = query.filter(account_id=account_id)
    rows = list(query.order_by('coupon_id', 'pk').values_list('pk', 'coupon_id'))
    affected = set()
    updated = 0
    now = timezone.now()
    for row_id, coupon_id in rows:
        coupon = Coupon.objects.select_for_update().filter(pk=coupon_id).first()
        owned = UserCoupon.objects.select_for_update().filter(pk=row_id).first()
        if not coupon or not owned or owned.status != 'UNUSED' or owned.coupon_id != coupon_id:
            continue
        deadline = owned.expires_at or coupon.valid_to
        if deadline and deadline <= now:
            continue
        account = ClubAccount.objects.filter(pk=owned.account_id, account_type='BOSS').first()
        if account is None:
            raise ValidationError('存在无法核实归属的 VIP 券，请先核对后再停用')
        if owned.business_cycle != account.business_cycle:
            continue
        key = coupon.grant_key or ''
        if not (key.startswith(f'vip-month:{account.pk}:{account.business_cycle}:') or
                key.startswith(f'vip-safe:{account.pk}:{account.business_cycle}:')):
            raise ValidationError('存在归属或周期不一致的 VIP 券，请先核对后再停用')
        if UserCoupon.objects.filter(coupon_id=coupon_id).exclude(pk=owned.pk).exists():
            raise ValidationError('存在多人共用的 VIP 专属券，请先核对后再停用')
        owned.coupon = coupon
        effective = effective_coupon(account, owned, vip_enabled=vip_enabled)
        changes = [field for field in ('discount_rate', 'max_discount', 'service_ids', 'valid_to')
                   if getattr(coupon, field) != getattr(effective, field)]
        if changes:
            for field in changes:
                setattr(coupon, field, getattr(effective, field))
            coupon.save(update_fields=changes)
            updated += 1
            affected.add(account.pk)
    from coupons.notifier import notify_owner_coupon_changed
    for account_id in affected:
        transaction.on_commit(lambda account_id=account_id: notify_owner_coupon_changed(account_id))
    return {'examined': len(rows), 'updated': updated, 'accounts': len(affected)}
