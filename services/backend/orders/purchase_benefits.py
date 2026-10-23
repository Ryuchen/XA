"""Shared qualification and pricing contract for all paid service-order entry points.

Call lock_purchase after the buyer wallet lock and before any debit. Record payment
in the same transaction; refunded/cancelled activity use remains an audit record.
"""
from datetime import timedelta
from zoneinfo import ZoneInfo
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from coupons.models import Coupon, UserCoupon
from coupons.benefits import applies_to, effective_boss_rate, validate_qualification_coupon
from .models import ExperienceUse
from .pricing import compute_order_amount, resolve_active_promotion, PricingError

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
    if units != 1 or service.billing_provider_count != 1 or service.is_addon or service.platform not in ('PC', 'MOBILE'):
        raise ValidationError('体验单每次仅可购买一个平台的一份主套餐')
    if ExperienceUse.objects.filter(**activity_scope(account, service.experience_kind, service.platform)).exists():
        raise ValidationError('该项新客资格或今日该平台额度已使用')


def usable_coupons(account):
    now = timezone.now()
    return UserCoupon.objects.filter(account=account, business_cycle=account.business_cycle,
        status='UNUSED', coupon__is_active=True).filter(
        Q(expires_at__gt=now) | Q(expires_at__isnull=True, coupon__valid_to__gt=now)
        | Q(expires_at__isnull=True, coupon__valid_to__isnull=True)).select_related('coupon')


def prepare_purchase(account, service, units=1, coupon_id=None):
    check_activity(account, service, units)
    uc = None
    if coupon_id:
        uc = usable_coupons(account).filter(pk=coupon_id).first()
        if not uc:
            raise ValidationError('优惠券不存在、已使用或已过期')
    elif service.experience_kind in VALUE_KINDS:
        uc = next((row for row in usable_coupons(account).filter(coupon__experience_kind=service.experience_kind)
                   if applies_to(row.coupon, service)), None)
    if service.experience_kind in VALUE_KINDS and not uc:
        raise ValidationError('请先到领券中心领取该平台的超值资格券')
    if uc:
        if not applies_to(uc.coupon, service):
            raise ValidationError('该优惠券不适用于此套餐')
        validate_qualification_coupon(uc.coupon, service, units)
        if service.experience_kind in VALUE_KINDS and uc.coupon.experience_kind != service.experience_kind:
            raise ValidationError('必须使用对应的超值资格券')
        if uc.coupon.scope == 'SAFE_BOX' and (units != 1 or service.billing_provider_count != 1):
            raise ValidationError('安全箱券仅可兑换一份单陪主套餐')
    experience = is_experience(service)
    if experience and uc and uc.coupon.discount_type != 'QUALIFICATION':
        raise ValidationError('体验单按海报专价执行，不参与优惠活动')
    promotion = None if experience else resolve_active_promotion(service)
    kwargs = {}
    if uc and uc.coupon.discount_type != 'QUALIFICATION':
        c = uc.coupon
        kwargs['coupon_threshold'] = c.threshold
        if c.discount_type == 'RATE':
            kwargs['coupon_rate'] = c.discount_rate
            limits = [c.max_discount] if c.max_discount else []
            if c.limit_units:
                limits.append(service.price * min(units, c.limit_units) * (100-c.discount_rate) // 100)
            if limits:
                kwargs['coupon_max_discount'] = min(limits)
        else:
            kwargs['coupon_amount'] = c.amount
    try:
        pricing = compute_order_amount(service.price * units * service.billing_provider_count,
            100 if experience else effective_boss_rate(account), promotion, **kwargs)
    except PricingError as exc:
        raise ValidationError(str(exc)) from exc
    if uc and not pricing.coupon_discount and uc.coupon.discount_type != 'QUALIFICATION':
        uc = None
    return pricing, promotion, uc


def lock_purchase(account, service, units, coupon, expected):
    # All callers already hold the buyer wallet lock. Refresh the cycle and VIP
    # under that lock, then reprice so a quote cannot cross retirement or expiry.
    quoted_cycle = account.business_cycle
    account.refresh_from_db(fields=['business_cycle', 'boss_type'])
    if quoted_cycle != account.business_cycle:
        raise ValidationError('账户业务周期已变化，请刷新报价')
    service.refresh_from_db()
    from .catalog import visible_service_q
    if not type(service).objects.filter(visible_service_q(), pk=service.pk).exists():
        raise ValidationError('服务已下架或不可用')
    if coupon:
        UserCoupon.objects.select_for_update().filter(pk=coupon.pk).first()
    result, promotion, current = prepare_purchase(account, service, units, coupon.pk if coupon else None)
    if (result != expected or (coupon is not None and (current is None or current.pk != coupon.pk))):
        raise ValidationError('资格或报价已变化，请刷新后重新确认')
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
        coupon.save(update_fields=['status', 'order', 'used_at', 'restore_on_refund'])


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
