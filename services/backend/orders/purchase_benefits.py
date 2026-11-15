"""Shared qualification and pricing contract for all paid service-order entry points.

Call lock_purchase after the buyer wallet lock and before any debit. Record payment
in the same transaction; refunded/cancelled activity use remains an audit record.
"""
from datetime import timedelta
from zoneinfo import ZoneInfo
from stores.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from coupons.models import Coupon, UserCoupon
from coupons.benefits import applies_to, effective_boss_rate, validate_qualification_coupon
from .models import ExperienceUse
from .pricing import compute_order_amount, resolve_active_promotion, promotion_available_for_new_order, PricingError

BEIJING = ZoneInfo('Asia/Shanghai')
VALUE_KINDS = ('NEW_VALUE', 'DAILY_VALUE')


def activity_scope(account, kind, platform, now=None):
    today = timezone.localtime(now or timezone.now(), BEIJING).date()
    return dict(account_id=account.pk, business_cycle=account.business_cycle, kind=kind,
                platform=platform if kind.startswith('DAILY') else '',
                period=today.isoformat() if kind.startswith('DAILY') else 'ONCE')


def is_experience(service):
    return bool(service.service_type == 'EXPERIENCE' or service.experience_kind or (service.gameplay_id and service.gameplay.theme.lower() == 'experience'))


def check_activity(account, service, units=1):
    if not is_experience(service):
        return
    if not service.experience_kind:
        raise ValidationError('体验套餐尚未配置资格类型，请联系客服')
    if units != 1 or service.fulfillment_provider_count != 1 or service.is_addon or service.platform not in ('PC', 'MOBILE'):
        raise ValidationError('体验单每次仅可购买一个平台的一份主套餐')
    if ExperienceUse.objects.filter(**activity_scope(account, service.experience_kind, service.platform)).exists():
        raise ValidationError('该项新客资格或今日该平台额度已使用')


def usable_coupons(account):
    now = timezone.now()
    return UserCoupon.objects.filter(account=account, business_cycle=account.business_cycle,
        status='UNUSED', coupon__is_active=True).filter(
        Q(expires_at__gt=now) | Q(expires_at__isnull=True, coupon__valid_to__gt=now)
        | Q(expires_at__isnull=True, coupon__valid_to__isnull=True)).select_related('coupon')


def _effective_owned_coupon(account, owned, *, locked=False):
    """Read the same live VIP terms used by the discount-option quote."""
    from coupons.pricing_terms import effective_coupon
    from engagement.models import ClubOperationConfig

    query = ClubOperationConfig.objects.filter(pk=1)
    enabled = (query.select_for_update() if locked else query).values_list('vip_enabled', flat=True).first()
    return effective_coupon(account, owned, vip_enabled=enabled is not False, locked=locked)


def vip_safe_box_subsidy_basis(account, service, units, user_coupon, pricing):
    """Identify the price basis for the explicitly issued VIP6 free service.

    This does not pay or change an order. Call only after purchase validation;
    the eventual funding snapshot must be taken from the locked coupon.
    """
    if not user_coupon or user_coupon.account_id != account.pk or user_coupon.business_cycle != account.business_cycle:
        return 0
    coupon = _effective_owned_coupon(account, user_coupon)
    prefix = f'vip-safe:{account.pk}:{account.business_cycle}:'
    if (coupon.scope != 'SAFE_BOX' or coupon.discount_type != 'RATE' or coupon.discount_rate != 0
            or not (coupon.grant_key or '').startswith(prefix) or units != 1
            or not applies_to(coupon, service) or pricing.amount != 0 or pricing.original_amount <= 0):
        return 0
    return pricing.original_amount


def vip_safe_box_subsidy_income(basis, *, commission_type, commission_rate, commission_fixed):
    """Use the same rounding and fixed/percentage rules as ordinary shares."""
    from .settlement import compute_provider_income
    return compute_provider_income(basis, commission_type, commission_rate, commission_fixed)


def prepare_purchase(account, service, units=1, coupon_id=None, reservation_minutes=None, *, locked=False):
    check_activity(account, service, units)
    if reservation_minutes is not None and is_experience(service) and reservation_minutes != 60:
        raise ValidationError('体验套餐预约时长尚需确认，请联系客服')
    uc = None
    if coupon_id:
        coupons = usable_coupons(account).filter(pk=coupon_id)
        uc = (coupons.select_for_update() if locked else coupons).first()
        if not uc:
            raise ValidationError('优惠券不存在、已使用或已过期')
        # VIP safe-box coupons follow the member's current tier configuration.
        # A checkout can remain open while staff changes that configuration, so
        # do not rely on an earlier visit to the coupon list to refresh it.
        if (uc.coupon.scope == 'SAFE_BOX' and
                (uc.coupon.grant_key or '').startswith(f'vip-safe:{account.pk}:{account.business_cycle}:')):
            from coupons.benefits import try_grant_periodic
            try_grant_periodic(account)
            coupons = usable_coupons(account).filter(pk=coupon_id)
            uc = (coupons.select_for_update() if locked else coupons).first()
            if not uc:
                raise ValidationError('优惠券不存在、已使用或已过期')
    elif service.experience_kind in VALUE_KINDS:
        uc = next((row for row in usable_coupons(account).filter(coupon__experience_kind=service.experience_kind)
                   if applies_to(row.coupon, service)), None)
    if service.experience_kind in VALUE_KINDS and not uc:
        raise ValidationError('请先到领券中心领取该平台的超值资格券')
    if uc:
        effective = _effective_owned_coupon(account, uc, locked=locked)
        if not applies_to(effective, service):
            raise ValidationError('该优惠券不适用于此套餐')
        validate_qualification_coupon(effective, service, units)
        if reservation_minutes is not None and effective.discount_type == 'QUALIFICATION' and reservation_minutes != 60:
            raise ValidationError('该资格券的预约时长尚需确认，请联系客服')
        if service.experience_kind in VALUE_KINDS and effective.experience_kind != service.experience_kind:
            raise ValidationError('必须使用对应的超值资格券')
        if effective.scope == 'SAFE_BOX' and (units != 1 or service.fulfillment_provider_count != 1):
            raise ValidationError('安全箱券仅可兑换一份单陪主套餐')
    experience = is_experience(service)
    if experience and uc and uc.coupon.discount_type != 'QUALIFICATION':
        raise ValidationError('体验单按海报专价执行，不参与优惠活动')
    if not experience and not (uc and uc.coupon.discount_type == 'QUALIFICATION'):
        # Without an explicit option token, use the best eligible percentage
        # for the requested voucher (or no voucher), just as reservations do.
        from .discount_recommendation import service_discount_options
        from .pricing import DiscountResult
        from promotions.models import Promotion
        options = service_discount_options(account, service, units, reservation_minutes,
                                           locked=locked, coupon_id=uc.pk if uc else None)
        selected = next((item for item in options if item.coupon_id == (uc.pk if uc else None)), None)
        if selected is None:
            raise ValidationError('优惠券不适用当前服务，请重新选择')
        promotion_query = Promotion.objects.filter(pk=selected.promotion_id)
        promotion = ((promotion_query.select_for_update() if locked else promotion_query).first()
                     if selected.promotion_id else None)
        return DiscountResult(selected.original_amount, selected.boss_discount, selected.promo_discount,
                              selected.coupon_discount, selected.amount), promotion, uc
    promotion = (resolve_active_promotion(service)
                 if not experience and promotion_available_for_new_order() else None)
    kwargs = {}
    if uc and uc.coupon.discount_type != 'QUALIFICATION':
        c = uc.coupon
        kwargs['coupon_threshold'] = c.threshold
        if c.discount_type == 'RATE':
            kwargs['coupon_rate'] = c.discount_rate
            limits = [c.max_discount] if c.max_discount else []
            if c.limit_units:
                from .reservation_pricing import limited_coupon_basis
                limits.append(limited_coupon_basis(service, units, c.limit_units, reservation_minutes) * (100-c.discount_rate) // 100)
            if limits:
                kwargs['coupon_max_discount'] = min(limits)
        else:
            kwargs['coupon_amount'] = c.amount
    try:
        from .reservation_pricing import original_amount
        pricing = compute_order_amount(original_amount(service, units, reservation_minutes),
            100 if experience else effective_boss_rate(account), promotion, **kwargs)
    except PricingError as exc:
        raise ValidationError(str(exc)) from exc
    if uc and not pricing.coupon_discount and uc.coupon.discount_type != 'QUALIFICATION':
        uc = None
    return pricing, promotion, uc


_PROMOTION_NOT_PROVIDED = object()


def lock_purchase(account, service, units, coupon, expected, reservation_minutes=None, *,
                  expected_promotion=_PROMOTION_NOT_PROVIDED):
    # All callers already hold the buyer wallet lock. Refresh the cycle and VIP
    # under that lock, then reprice so a quote cannot cross retirement or expiry.
    quoted_cycle = account.business_cycle
    from club_accounts.models import ClubAccount
    account = ClubAccount.objects.select_for_update().filter(pk=account.pk, is_active=True, can_login=True).first()
    if account is None:
        raise ValidationError('老板账号不可用，请重新核对')
    if quoted_cycle != account.business_cycle:
        raise ValidationError('账户业务周期已变化，请刷新报价')
    from .catalog import visible_service_q
    service = type(service).objects.select_for_update().filter(visible_service_q(), pk=service.pk).first()
    if service is None:
        raise ValidationError('服务已下架或不可用')
    operation = None
    if reservation_minutes is not None or (not is_experience(service)
            and not (coupon and coupon.coupon.discount_type == 'QUALIFICATION')):
        # Both ordinary and reservation fallback quotes use effective VIP
        # coupon terms. Lock their sources before the coupon rows.
        from engagement.models import ClubOperationConfig, VipTier
        from users.models import BossType
        operation = ClubOperationConfig.objects.select_for_update().filter(pk=1).first()
        if operation is None:
            raise ValidationError('门店会员规则尚未配置')
        if account.boss_type_id:
            boss_type = BossType.objects.select_for_update().filter(pk=account.boss_type_id).first()
            if boss_type:
                account.boss_type = boss_type
            VipTier.objects.select_for_update().filter(boss_type_id=account.boss_type_id).first()
    selected_promotion_id = None
    locked_promotion = None
    if operation:
        if not is_experience(service):
            # Promotion locks precede voucher locks in both checkout paths.
            # Read the candidates under lock before resolving the best one.
            from .discount_recommendation import service_discount_options
            service_discount_options(account, service, units, reservation_minutes, locked=True)
        _, tentative_promotion, _ = prepare_purchase(
            account, service, units, coupon.pk if coupon else None, reservation_minutes, locked=True)
        if tentative_promotion:
            from promotions.models import Promotion
            selected_promotion_id = tentative_promotion.pk
            locked_promotion = Promotion.objects.select_for_update().filter(pk=selected_promotion_id).first()
    if expected_promotion is not _PROMOTION_NOT_PROVIDED:
        expected_id = getattr(expected_promotion, 'pk', None)
        if expected_id != selected_promotion_id or (expected_id is not None and (
                locked_promotion is None or any(
                    getattr(locked_promotion, field) != getattr(expected_promotion, field)
                    for field in ('updated_at', 'discount_rate', 'commission_type', 'commission_rate',
                                  'commission_fixed', 'start_at', 'end_at', 'scope', 'category_id', 'is_active')))):
            raise ValidationError('活动或抽成规则已变化，请刷新后重新确认')
    if coupon:
        Coupon.objects.select_for_update().filter(pk=coupon.coupon_id).first()
        UserCoupon.objects.select_for_update().filter(pk=coupon.pk).first()
    result, promotion, current = prepare_purchase(account, service, units, coupon.pk if coupon else None,
                                                   reservation_minutes, locked=True)
    if operation and (promotion.pk if promotion else None) != selected_promotion_id:
        raise ValidationError('活动条件已变化，请刷新后重新确认')
    if (result != expected or (coupon is not None and (current is None or current.pk != coupon.pk))):
        raise ValidationError('资格或报价已变化，请刷新后重新确认')
    if operation and current:
        from coupons.pricing_terms import materialize_effective_coupon
        materialize_effective_coupon(account, current, vip_enabled=operation.vip_enabled)
    return current


def record_paid_benefits(order, coupon=None):
    if order.experience_kind:
        ExperienceUse.objects.create(order=order, **activity_scope(order.customer_account,
            order.experience_kind, order.service.platform))
    if coupon:
        coupon.status = 'USED'
        coupon.order = order
        coupon.used_at = timezone.now()
        coupon.restore_on_refund = coupon.coupon.discount_type != 'QUALIFICATION'
        fields = ['status', 'order', 'used_at', 'restore_on_refund']
        cycle = order.customer_account.business_cycle if order.customer_account_id else None
        if cycle is not None and any((coupon.coupon.grant_key or '').startswith(f'vip-{kind}:{order.customer_account_id}:{cycle}:')
                                     for kind in ('upgrade', 'month', 'safe', 'birthday')):
            coupon.vip_terms_locked_by_downgrade = True
            fields.append('vip_terms_locked_by_downgrade')
        coupon.save(update_fields=fields)
        from coupons.notifier import notify_coupon_claim_changed
        transaction.on_commit(lambda: notify_coupon_claim_changed(coupon.coupon_id))


def account_service_q(account, prefix=''):
    # Qualifying products are absent from anonymous/public catalog aggregates.
    from .models import ServiceItem
    base = ~Q(**{prefix+'experience_kind__in': VALUE_KINDS})
    if not account:
        return base
    ids = set()
    for row in usable_coupons(account).filter(coupon__experience_kind__in=VALUE_KINDS):
        c = row.coupon
        for service in ServiceItem.objects.filter(pk__in=c.service_ids or []):
            if applies_to(c, service) and not ExperienceUse.objects.filter(**activity_scope(account, service.experience_kind, service.platform)).exists():
                ids.add(service.pk)
    return base | Q(**{prefix+'pk__in': list(ids)})
