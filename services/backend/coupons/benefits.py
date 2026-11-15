import calendar
from datetime import datetime, timedelta
from stores.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.license_benefits import LicenseFeatureDenied, skip_denied_benefit
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
        if (service.benefit_kind != kind or service.fulfillment_provider_count != 1 or service.billing_provider_count != 1
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
    if coupon.discount_type != Coupon.DiscountType.QUALIFICATION:
        return
    if not applies_to(coupon, service):
        raise ValidationError('资格券未绑定此套餐，无法使用')
    if units != 1:
        raise ValidationError('资格券每次仅可购买一份套餐')


def restore_order_coupons(order):
    """在订单退款事务中调用；已核销的资格券保留使用时间和订单关联。"""
    coupons = UserCoupon.objects.filter(
        order=order, status=UserCoupon.Status.USED, restore_on_refund=True,
    )
    coupon_ids = list(coupons.values_list('coupon_id', flat=True).distinct())
    restored = coupons.update(status=UserCoupon.Status.UNUSED, order=None, used_at=None)
    if restored:
        from .notifier import notify_coupon_claim_changed
        for coupon_id in coupon_ids:
            transaction.on_commit(lambda coupon_id=coupon_id: notify_coupon_claim_changed(coupon_id))
    return restored


@transaction.atomic
def issue(account, key, *, name, rate, quantity=1, scope='ALL', variant='', limit_units=0, expires_at=None, service_ids=None, max_discount=0):
    from club_accounts.models import LegacyAccountMap
    mapping = LegacyAccountMap.objects.filter(account=account).first()
    rows = []
    issued = False
    admission_checked = False
    for index in range(quantity):
        grant_key = f'{key}:{index}'
        coupon = Coupon.objects.filter(grant_key=grant_key).first()
        existing = (UserCoupon.objects.filter(account=account, coupon=coupon,
                    business_cycle=account.business_cycle).first() if coupon else None)
        if existing:
            rows.append(existing)
            continue
        if not admission_checked:
            from stores.license_benefits import check_new_benefits
            if check_new_benefits('VIP_GROWTH')[0] != 'ALLOW':
                raise LicenseFeatureDenied('门店授权暂不允许发放新的 VIP 权益，请联系平台')
            admission_checked = True
        coupon, _ = Coupon.objects.get_or_create(grant_key=grant_key, defaults={
            'name': name, 'discount_type': 'RATE', 'discount_rate': rate, 'scope': scope,
            'scope_variant': variant, 'limit_units': limit_units, 'amount': 0, 'threshold': 0,
            'service_ids': service_ids, 'max_discount': max_discount,
            'is_public': False, 'total_qty': 1, 'claimed_qty': 1, 'valid_to': expires_at})
        row, created = UserCoupon.objects.get_or_create(account=account, coupon=coupon,
            business_cycle=account.business_cycle, defaults={'user_id': mapping.legacy_user_id if mapping else None})
        issued = issued or created
        rows.append(row)
    if issued:
        from .notifier import notify_owner_coupon_changed
        transaction.on_commit(lambda: notify_owner_coupon_changed(account.pk))
    return rows


@transaction.atomic
@skip_denied_benefit
def grant_periodic(account):
    from engagement.models import ClubOperationConfig, VipTier, MemberProfile
    from engagement.services import config, locked_profile
    if account.account_type != 'BOSS' or not config().vip_enabled:
        return
    expected_cycle = account.business_cycle
    from stores.license_benefits import check_new_benefits
    if check_new_benefits('VIP_GROWTH')[0] != 'ALLOW':
        return
    from wallet.services import get_wallet
    get_wallet(account_id=account.pk, for_update=True)
    if not ClubOperationConfig.objects.select_for_update().get(pk=1).vip_enabled:
        return
    from club_accounts.models import ClubAccount
    if not ClubAccount.objects.filter(pk=account.pk, account_type='BOSS',
            business_cycle=expected_cycle, license_membership_active=True).exists():
        return
    account.refresh_from_db(fields=['boss_type', 'business_cycle'])
    tier = VipTier.objects.select_for_update().filter(active=True, boss_type_id=account.boss_type_id).first()
    if not tier:
        return
    profile = locked_profile(account)
    if check_new_benefits('VIP_GROWTH')[0] != 'ALLOW':
        raise LicenseFeatureDenied('门店授权暂不允许发放新的 VIP 权益，请联系平台')
    from zoneinfo import ZoneInfo
    now = timezone.localtime(timezone.now(), ZoneInfo('Asia/Shanghai'))
    from .models import VipMonthlyCutover
    switched = VipMonthlyCutover.objects.filter(pk=1).exists()
    if switched:
        from .monthly_cutover import grant_current_month
        grant_current_month(account, tier, now=now)
    else:
        # The first-edition store uses its actual tier configuration and an
        # auditable monthly decision, including an explicitly zero quantity.
        from .monthly import issue_monthly_coupons
        issue_monthly_coupons(account, tier.pk, now=now, allow_new_period=True)
    from engagement.models import VipUpgradeReview
    reviews = VipUpgradeReview.objects.filter(account=account, tier=tier, business_cycle=account.business_cycle)
    pending_review = reviews.filter(status='PENDING').exists()
    confirmed_without_history = reviews.filter(status='RESOLVED', resolution='ALREADY_ISSUED').exists() and not UserCoupon.objects.filter(
        account=account, business_cycle=account.business_cycle,
        coupon__grant_key__startswith=f'vip-safe:{account.pk}:{account.business_cycle}:').exists()
    from engagement.vip_policy import issuance_policy
    policy = issuance_policy(tier)
    if not pending_review and profile.birthday and (profile.birthday.month, profile.birthday.day) == (now.month, now.day):
        if policy['birthday']:
            expiry = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=policy['birthday_valid_days'])
            issue(account, f'vip-birthday:{account.pk}:{account.business_cycle}:{now.year}',
                name='生日折扣券', rate=policy['birthday_discount'],
                max_discount=policy['birthday_max_discount'], expires_at=expiry)
        from engagement.models import RewardClaim, RewardItem
        from engagement.services import grant_entitlement
        rewards = RewardItem.objects.filter(vip_quota_kind='BIRTHDAY_GIFT', in_shop=False, is_active=True)
        reward = (rewards.filter(pk=policy['birthday_reward_id']).first() if policy['custom']
                  else rewards.first())
        reward_key = f'vip-birthday-gift:{account.pk}:{account.business_cycle}:{now.year}'
        existing_reward = RewardClaim.objects.filter(account=account, event_key=reward_key).exists()
        if reward and not existing_reward:
            for index in range(policy['birthday_reward_quantity']):
                grant_entitlement(account, reward, reward_key if not index else f'{reward_key}:{index}',
                    'VIP', {'business_cycle': account.business_cycle, 'tier_id': tier.pk})
    if check_new_benefits('VIP_GROWTH')[0] != 'ALLOW':
        raise LicenseFeatureDenied('门店授权暂不允许发放新的 VIP 权益，请联系平台')
    grant_safety_box(account, tier, allow_issue=not pending_review and not confirmed_without_history)


def try_grant_periodic(account):
    """Keep a failed periodic grant from blocking a separate business action."""
    from .monthly import MonthlyCouponEvidenceError
    try:
        grant_periodic(account)
    except (ValidationError, MonthlyCouponEvidenceError):
        # grant_periodic owns a savepoint, so an incomplete issuance is rolled
        # back before the caller continues. The scheduled grant retries.
        import logging
        logging.getLogger(__name__).exception(
            'Periodic VIP grant needs review for account %s; the scheduled grant will retry', account.pk)


def grant_safety_box(account, tier, *, allow_issue=True):
    from engagement.vip_policy import issuance_policy
    policy = issuance_policy(tier)
    if not policy['safety_box']:
        return
    prefix = f'vip-safe:{account.pk}:{account.business_cycle}'
    rows = list(UserCoupon.objects.filter(account=account, business_cycle=account.business_cycle,
        coupon__grant_key__startswith=prefix + ':').select_related('coupon', 'order').order_by('pk'))
    from .pricing_terms import safety_box_rate
    rate = safety_box_rate(tier)
    unused = [row for row in rows if row.status == 'UNUSED']
    if unused:
        coupon_id = unused[0].coupon_id
        Coupon.objects.select_for_update().get(pk=coupon_id)
        selected = UserCoupon.objects.select_for_update().select_related('coupon').get(pk=unused[0].pk)
        if selected.status != 'UNUSED' or selected.coupon_id != coupon_id:
            return
        if selected.vip_terms_locked_by_downgrade:
            return
        coupon = selected.coupon
        service_ids = tier.safe_box_service_ids or []
        if coupon.discount_rate != rate or coupon.service_ids != service_ids:
            Coupon.objects.filter(pk=coupon.pk).update(discount_rate=rate, service_ids=service_ids)
            from .notifier import notify_owner_coupon_changed
            transaction.on_commit(lambda: notify_owner_coupon_changed(account.pk))
        return
    if not allow_issue:
        return
    # VIP6 refreshes after ordinary consumption confirmation, or after a
    # negotiated termination has fully settled its agreed provider shares.
    if rows:
        previous = rows[-1].order
        normal = bool(previous and previous.consumption_confirmed_at and previous.payment_status == 'PAID')
        from django.conf import settings
        negotiated = bool(getattr(settings, 'VIP_SAFE_BOX_SUBSIDY_ENABLED', False) and previous
            and previous.status == 'CANCELLED' and previous.payment_status == 'REFUNDED' and previous.settled_at
            and previous.refund_records.filter(benefit_effects__subsidy_negotiated=True).exists())
        if not policy['safety_box_refresh'] or not (normal or negotiated):
            return
    issue(account, f'{prefix}:{len(rows)}', name='33安全箱代肝券', rate=rate,
        scope='SAFE_BOX', limit_units=1, service_ids=tier.safe_box_service_ids or [])


def effective_boss_rate(account):
    from engagement.services import config
    if not config().vip_enabled:
        return 100
    tier = account.boss_type
    return tier.discount_rate if tier and tier.is_active else 100
