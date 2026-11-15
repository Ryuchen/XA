"""Persist the current-month VIP upgrade decision without changing old coupons."""
from zoneinfo import ZoneInfo
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction

def freeze_current_month(account, *, vip_enabled=True):
    """Caller holds the account wallet and store operation configuration locks."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('会员升级月券保护必须在门店事务中执行')
    from engagement.vip_transition import freeze_existing_vip_terms
    from .models import Coupon, UserCoupon, VipMonthlyCouponGrant
    now = timezone.localtime(timezone.now(), ZoneInfo('Asia/Shanghai'))
    month = now.date().replace(day=1)
    prefix = f'vip-month:{account.pk}:{account.business_cycle}:{now:%Y-%m}:'
    # Materialize the old tier's actual live terms before the grade changes.
    freeze_existing_vip_terms(account_id=account.pk, kinds=('month', 'safe'), vip_enabled=vip_enabled)
    owned = UserCoupon.objects.filter(account=account, business_cycle=account.business_cycle,
                                      coupon__grant_key__startswith=prefix)
    coupon_ids = list(owned.order_by('coupon_id').values_list('coupon_id', flat=True).distinct())
    list(Coupon.objects.select_for_update().filter(pk__in=coupon_ids).order_by('pk').values_list('pk', flat=True))
    rows = list(owned.select_for_update().order_by('pk'))
    for item in rows:
        if UserCoupon.objects.filter(coupon_id=item.coupon_id).exclude(pk=item.pk).exists():
            raise ValidationError('存在多人共用或重复领取的 VIP 月券，请先核对')
    owned.filter(pk__in=[item.pk for item in rows]).update(vip_terms_locked_by_downgrade=True)
    # A still-valid safety-box coupon belongs to its original issuance tier.
    # Preserve its materialized terms before changing the owner's grade.
    safe = UserCoupon.objects.filter(account=account, business_cycle=account.business_cycle,
        status='UNUSED', coupon__grant_key__startswith=f'vip-safe:{account.pk}:{account.business_cycle}:').filter(
        Q(expires_at__gt=now) | Q(expires_at__isnull=True, coupon__valid_to__gt=now)
        | Q(expires_at__isnull=True, coupon__valid_to__isnull=True))
    safe_coupon_ids = list(safe.order_by('coupon_id').values_list('coupon_id', flat=True).distinct())
    list(Coupon.objects.select_for_update().filter(pk__in=safe_coupon_ids).order_by('pk').values_list('pk', flat=True))
    safe_rows = list(safe.select_for_update().order_by('coupon_id', 'pk'))
    for item in safe_rows:
        if UserCoupon.objects.filter(coupon_id=item.coupon_id).exclude(pk=item.pk).exists():
            raise ValidationError('存在多人共用或重复领取的 VIP 安全箱券，请先核对')
    safe.filter(pk__in=[item.pk for item in safe_rows]).update(vip_terms_locked_by_downgrade=True)
    grant, created = VipMonthlyCouponGrant.objects.select_for_update().get_or_create(
        account=account, business_cycle=account.business_cycle, month=month,
        defaults={'source': 'LEGACY', 'issued_count': len(rows), 'terms_frozen': True})
    if not created and not grant.terms_frozen:
        grant.terms_frozen = True
        grant.save(update_fields=['terms_frozen'])
    return grant
