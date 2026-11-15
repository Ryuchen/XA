"""Materialize current eligible private VIP coupon terms after setup changes."""
from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def sync_tier_terms(tier):
    from coupons.models import Coupon, UserCoupon
    from coupons.pricing_terms import materialize_effective_coupon
    from engagement.models import ClubOperationConfig

    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('会员券条款更新须在门店配置事务内执行')
    operation = ClubOperationConfig.objects.get(pk=1)
    if not operation.vip_enabled or not tier.active or not tier.boss_type.is_active:
        return
    candidates = (UserCoupon.objects.filter(account__account_type='BOSS', account__boss_type_id=tier.boss_type_id,
        business_cycle=F('account__business_cycle'), status='UNUSED', vip_terms_locked_by_downgrade=False)
        .filter(Q(coupon__grant_key__startswith='vip-month:') | Q(coupon__grant_key__startswith='vip-safe:'))
        .order_by('coupon_id', 'pk').values_list('pk', 'coupon_id'))
    now = timezone.now()
    for owned_id, coupon_id in list(candidates):
        coupon = Coupon.objects.select_for_update().filter(pk=coupon_id).first()
        owned = UserCoupon.objects.select_for_update().select_related('account').filter(pk=owned_id).first()
        if not coupon or not owned or owned.coupon_id != coupon_id:
            raise ValidationError('会员券归属已变化，请重新核对')
        if (owned.status != 'UNUSED' or owned.vip_terms_locked_by_downgrade
                or owned.business_cycle != owned.account.business_cycle
                or owned.account.boss_type_id != tier.boss_type_id):
            continue
        if ((owned.expires_at and owned.expires_at <= now)
                or (coupon.valid_to and coupon.valid_to <= now)):
            continue
        if UserCoupon.objects.filter(coupon_id=coupon_id).exclude(pk=owned.pk).exists():
            raise ValidationError('会员专属券存在多个领取人，请先核对')
        owned.coupon = coupon
        materialize_effective_coupon(owned.account, owned, vip_enabled=True)
