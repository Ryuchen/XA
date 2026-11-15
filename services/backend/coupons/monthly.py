"""Auditable VIP monthly issuance, including zero-coupon decisions.

The caller must decide whether a missing historical period may be issued.
This module does not activate a cutover policy or infer an unrecorded grant.
"""
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone
from stores.db import transaction

from .benefits import issue
from .models import Coupon, UserCoupon, VipMonthlyCouponGrant


class MonthlyCouponEvidenceError(ValueError):
    pass


@transaction.atomic
def issue_monthly_coupons(account, tier_id, *, now=None, allow_new_period=False, assume_historic_zero=False):
    """Adopt known old coupons or execute one explicitly authorized decision.

    Historical periods with no coupons cannot prove whether a zero-quantity
    grant already occurred. The cutover caller may record the explicitly
    approved historic-zero policy; it must never infer a historical grant.
    """
    from engagement.models import ClubOperationConfig, VipTier
    from engagement.services import config
    from wallet.services import get_wallet

    if account.account_type != 'BOSS':
        raise MonthlyCouponEvidenceError('当前账号或门店状态不允许发放会员月券')
    now = now or timezone.now()
    if timezone.is_naive(now):
        raise MonthlyCouponEvidenceError('月券时间必须包含时区')
    local = timezone.localtime(now, ZoneInfo('Asia/Shanghai'))
    month = local.date().replace(day=1)
    next_month = (local.replace(day=28, hour=0, minute=0, second=0, microsecond=0)
                  + timedelta(days=4)).replace(day=1)
    get_wallet(account_id=account.pk, for_update=True)
    config()
    if not ClubOperationConfig.objects.select_for_update().get(pk=1).vip_enabled:
        raise MonthlyCouponEvidenceError('当前账号或门店状态不允许发放会员月券')
    account.refresh_from_db(fields=['boss_type', 'business_cycle', 'is_active'])
    if not account.is_active:
        raise MonthlyCouponEvidenceError('当前账号状态不允许发放会员月券')
    tier = VipTier.objects.select_for_update().filter(pk=tier_id, active=True,
        boss_type_id=account.boss_type_id, boss_type__is_active=True).first()
    if not tier:
        raise MonthlyCouponEvidenceError('会员等级已变化，请重新核对')
    if not 1 <= tier.monthly_discount <= 100:
        raise MonthlyCouponEvidenceError('门店月券折扣配置无效')
    from engagement.vip_policy import issuance_policy
    max_discount = issuance_policy(tier)['monthly_max_discount']
    prefix = f'vip-month:{account.pk}:{account.business_cycle}:{local:%Y-%m}'
    grant = VipMonthlyCouponGrant.objects.select_for_update().filter(
        account=account, business_cycle=account.business_cycle, month=month).first()
    if grant:
        if grant.source == 'SYSTEM' and grant.tier_id_snapshot != tier.pk and not grant.terms_frozen:
            issued = UserCoupon.objects.filter(account=account, business_cycle=account.business_cycle,
                coupon__grant_key__startswith=prefix + ':')
            coupon_ids = list(issued.order_by('coupon_id').values_list('coupon_id', flat=True).distinct())
            list(Coupon.objects.select_for_update().filter(pk__in=coupon_ids).order_by('pk').values_list('pk', flat=True))
            issued.update(vip_terms_locked_by_downgrade=True)
            grant.terms_frozen = True
            grant.save(update_fields=['terms_frozen'])
        if grant.source == 'SYSTEM' and not grant.terms_frozen and grant.tier_id_snapshot == tier.pk:
            changed = Coupon.objects.filter(grant_key__startswith=prefix + ':',
                user_coupons__account=account, user_coupons__business_cycle=account.business_cycle,
                user_coupons__status='UNUSED', user_coupons__vip_terms_locked_by_downgrade=False).exclude(
                user_coupons__vip_terms_locked_by_downgrade=True).exclude(
                discount_rate=tier.monthly_discount, max_discount=max_discount).update(
                discount_rate=tier.monthly_discount, max_discount=max_discount)
            if changed:
                from .notifier import notify_owner_coupon_changed
                transaction.on_commit(lambda: notify_owner_coupon_changed(account.pk))
        return grant

    old_rows = list(UserCoupon.objects.select_for_update().filter(account=account,
        business_cycle=account.business_cycle, coupon__grant_key__startswith=prefix + ':').order_by('pk'))
    if old_rows:
        UserCoupon.objects.filter(pk__in=[row.pk for row in old_rows]).update(vip_terms_locked_by_downgrade=True)
        return VipMonthlyCouponGrant.objects.create(account=account, business_cycle=account.business_cycle,
            month=month, source='LEGACY', issued_count=len(old_rows), terms_frozen=True)
    if Coupon.objects.filter(grant_key__startswith=prefix + ':').exists():
        raise MonthlyCouponEvidenceError('历史月券模板与领取记录不一致，请先核对')
    if assume_historic_zero:
        return VipMonthlyCouponGrant.objects.create(account=account, business_cycle=account.business_cycle,
            month=month, source='LEGACY', issued_count=0, terms_frozen=True)
    if not allow_new_period:
        raise MonthlyCouponEvidenceError('历史月券发放事实无法核实，请先核对')

    # Recheck even a zero-coupon decision after the wallet/config locks.  A
    # missing decision remains retryable if the license expires or becomes
    # unverifiable while this grant is waiting to run.
    from stores.license_benefits import LicenseFeatureDenied, check_new_benefits
    if check_new_benefits('VIP_GROWTH')[0] != 'ALLOW':
        raise LicenseFeatureDenied('门店授权暂不允许发放新的 VIP 权益，请联系平台')
    grant = VipMonthlyCouponGrant.objects.create(account=account, business_cycle=account.business_cycle,
        month=month, source='SYSTEM', tier_id_snapshot=tier.pk, tier_level_snapshot=tier.level,
        issued_count=tier.monthly_coupon_count, issued_discount=tier.monthly_discount)
    for index in range(tier.monthly_coupon_count):
        issue(account, f'{prefix}:{index}', name=f'{tier.boss_type.name} {local.month}月折扣券',
              rate=tier.monthly_discount, expires_at=next_month, max_discount=max_discount)
    return grant
