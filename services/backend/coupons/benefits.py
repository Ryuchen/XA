import calendar
from datetime import datetime, timedelta
from django.db import transaction
from django.utils import timezone
from .models import Coupon, UserCoupon


def applies_to(coupon, service):
    if service.service_category_id and service.service_category.is_gift:
        return False
    experience = bool(service.service_type == 'EXPERIENCE' or service.experience_kind or (service.gameplay_id and service.gameplay.theme.lower() == 'experience'))
    if experience and coupon.discount_type != 'QUALIFICATION':
        return False
    if coupon.discount_type == 'RATE' and coupon.discount_rate > 0 and not service.allows_discount_coupons:
        return False
    if coupon.experience_kind:
        if coupon.discount_type != 'QUALIFICATION' or coupon.experience_kind != service.experience_kind:
            return False
        if coupon.platform and coupon.platform != service.platform:
            return False
    if coupon.discount_type == 'QUALIFICATION' and not coupon.service_ids:
        return False
    if coupon.scope in ('COMPANION', 'SAFE_BOX'):
        kind = 'COMPANION_HOUR' if coupon.scope == 'COMPANION' else 'SAFEBOX_33'
        if (service.benefit_kind != kind or service.billing_provider_count != 1
                or service.purchase_mode != 'DIRECT' or service.platform not in ('PC', 'MOBILE')):
            return False
        if kind == 'COMPANION_HOUR' and service.price_unit not in ('小时', '时', 'hour'):
            return False
        if coupon.service_ids is None:
            return False
    if coupon.service_ids is not None:
        return service.pk in coupon.service_ids and not service.is_addon
    return coupon.scope == 'ALL'


def validate_qualification_coupon(coupon, service, units):
    from rest_framework.exceptions import ValidationError
    if coupon.discount_type != Coupon.DiscountType.QUALIFICATION:
        return
    if not applies_to(coupon, service):
        raise ValidationError('资格券未绑定此套餐，无法使用')
    if units != 1:
        raise ValidationError('资格券每次仅可购买一份套餐')


def restore_order_coupons(order):
    """在订单退款事务中调用；已核销的资格券保留使用时间和订单关联。"""
    return UserCoupon.objects.filter(
        order=order, status=UserCoupon.Status.USED, restore_on_refund=True,
    ).update(status=UserCoupon.Status.UNUSED, order=None, used_at=None)


@transaction.atomic
def issue(account, key, *, name, rate, quantity=1, scope='ALL', variant='', limit_units=0, expires_at=None, service_ids=None, max_discount=0):
    from club_accounts.models import LegacyAccountMap
    mapping = LegacyAccountMap.objects.filter(account=account).first()
    if not mapping:
        from rest_framework.exceptions import ValidationError
        raise ValidationError('账号资料尚未完成关联，无法发放优惠券，请联系客服核对')
    rows = []
    for index in range(quantity):
        coupon, _ = Coupon.objects.get_or_create(grant_key=f'{key}:{index}', defaults={
            'name': name, 'discount_type': 'RATE', 'discount_rate': rate, 'scope': scope,
            'scope_variant': variant, 'limit_units': limit_units, 'amount': 1, 'threshold': 0,
            'service_ids': service_ids, 'max_discount': max_discount,
            'is_public': False, 'total_qty': 1, 'claimed_qty': 1, 'valid_to': expires_at})
        row, _ = UserCoupon.objects.get_or_create(account=account, coupon=coupon,
            business_cycle=account.business_cycle, defaults={'user_id': mapping.legacy_user_id})
        rows.append(row)
    return rows


@transaction.atomic
def grant_periodic(account):
    from engagement.models import VipTier, MemberProfile
    from engagement.services import config, locked_profile
    if account.account_type != 'BOSS' or not config().vip_enabled:
        return
    from wallet.services import get_wallet
    get_wallet(account_id=account.pk, for_update=True)
    account.refresh_from_db(fields=['boss_type', 'business_cycle'])
    tier = VipTier.objects.select_related('boss_type').filter(active=True, boss_type_id=account.boss_type_id).first()
    if not tier:
        return
    profile = locked_profile(account)
    from zoneinfo import ZoneInfo
    now = timezone.localtime(timezone.now(), ZoneInfo('Asia/Shanghai'))
    next_month = (now.replace(day=28, hour=0, minute=0, second=0, microsecond=0) + timedelta(days=4)).replace(day=1)
    prefix = f'vip-month:{account.pk}:{account.business_cycle}:{now:%Y-%m}'
    count = {1: 0, 2: 1, 3: 2, 4: 4, 5: 10, 6: 10}.get(tier.level, 0)
    rate = 85 if tier.level == 6 else 90
    for index in range(count):
        issue(account, f'{prefix}:{index}', name=f'{tier.boss_type.name} {now.month}月折扣券',
              rate=rate, expires_at=next_month, max_discount=10000)
    Coupon.objects.filter(grant_key__startswith=prefix + ':', discount_rate__gt=rate,
        user_coupons__account=account, user_coupons__status='UNUSED').update(discount_rate=rate)
    if tier.level >= 4 and profile.birthday and (profile.birthday.month, profile.birthday.day) == (now.month, now.day):
        tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        issue(account, f'vip-birthday:{account.pk}:{account.business_cycle}:{now.year}',
            name='生日九折券', rate=90, max_discount=10000, expires_at=tomorrow)
        from engagement.models import RewardItem
        from engagement.services import grant_entitlement
        reward = RewardItem.objects.filter(vip_quota_kind='BIRTHDAY_GIFT', in_shop=False, is_active=True).first()
        if reward:
            grant_entitlement(account, reward, f'vip-birthday-gift:{account.pk}:{account.business_cycle}:{now.year}',
                'VIP', {'business_cycle': account.business_cycle})
    grant_safety_box(account, tier)


def grant_safety_box(account, tier):
    if not 1 <= tier.level <= 6:
        return
    prefix = f'vip-safe:{account.pk}:{account.business_cycle}'
    rows = list(UserCoupon.objects.filter(account=account, business_cycle=account.business_cycle,
        coupon__grant_key__startswith=prefix + ':').select_related('coupon', 'order').order_by('pk'))
    rate = {1:80, 2:80, 3:80, 4:70, 5:60, 6:0}[tier.level]
    unused = [row for row in rows if row.status == 'UNUSED']
    if unused:
        Coupon.objects.filter(pk=unused[0].coupon_id).update(discount_rate=rate,
            service_ids=tier.safe_box_service_ids or [], valid_to=None)
        return
    # A consumed low-tier voucher never refreshes. VIP6 replenishment follows
    # the last generation only after the same order's consumption confirmation.
    if rows and (tier.level < 6 or not rows[-1].order_id
                 or not rows[-1].order.consumption_confirmed_at
                 or rows[-1].order.payment_status != 'PAID'):
        return
    issue(account, f'{prefix}:{len(rows)}', name='33安全箱代肝券', rate=rate,
        scope='SAFE_BOX', limit_units=1, service_ids=tier.safe_box_service_ids or [])


def effective_boss_rate(account):
    tier = account.boss_type
    return tier.discount_rate if tier and tier.is_active else 100
