"""Read-only adapter from store data to explicit discount combinations.

The ordinary-order payment integration is experimental and default-off.
Callers must revalidate and lock the selected benefit at payment time; these
rows alone confer no rights.
"""
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from coupons.benefits import applies_to
from coupons.pricing_terms import effective_coupon
from engagement.models import ClubOperationConfig
from promotions.models import Promotion
from .discount_options import DiscountOptionError, PercentageOption, VoucherOption, discount_options
from .purchase_benefits import is_experience, usable_coupons

_ALL_PROMOTIONS = object()


def service_discount_options(account, service, units, reservation_minutes=None, *, locked=False, coupon_id=None,
                             promotion_id=_ALL_PROMOTIONS):
    if type(units) is not int or units < 1 or not account or account.account_type != 'BOSS':
        raise ValidationError('订单账号或数量无效')
    if is_experience(service) or (service.service_category_id and service.service_category.is_gift) or service.is_addon:
        raise ValidationError('此商品使用专属计价，不适用普通优惠组合')
    operation_query = ClubOperationConfig.objects.filter(pk=1)
    operation = (operation_query.select_for_update() if locked else operation_query).first()
    if operation is None:
        raise ValidationError('门店会员规则尚未配置')
    from .reservation_pricing import original_amount, limited_coupon_basis
    original = original_amount(service, units, reservation_minutes)
    percentages = [PercentageOption('none', '原价', 100, 'NONE')]
    boss_type = account.boss_type
    if operation.vip_enabled and boss_type and boss_type.is_active:
        percentages.append(PercentageOption('vip', boss_type.name, boss_type.discount_rate, 'VIP'))
    now = timezone.now()
    from .pricing import promotion_available_for_new_order
    promotions = (Promotion.objects.filter(is_active=True, start_at__lte=now, end_at__gte=now,
                  discount_rate__isnull=False).order_by('pk' if locked else '-priority', '-start_at', 'id')
                  if promotion_available_for_new_order() else Promotion.objects.none())
    if promotion_id is not _ALL_PROMOTIONS:
        promotions = promotions.filter(pk=promotion_id) if promotion_id else promotions.none()
    if locked:
        promotions = promotions.select_for_update()
    for promo in promotions:
        if locked and promo.scope == Promotion.Scope.ITEMS:
            applicable = promo.items.through.objects.select_for_update().filter(
                promotion_id=promo.pk, serviceitem_id=service.pk).exists()
        else:
            applicable = promo.applies_to(service)
        if applicable:
            percentages.append(PercentageOption(f'promotion:{promo.pk}', promo.title,
                                                promo.discount_rate, 'PROMOTION', promo.pk))

    vouchers = []
    owned_rows = usable_coupons(account)
    if locked:
        owned_rows = owned_rows.filter(pk=coupon_id).select_for_update() if coupon_id else owned_rows.none()
    for owned in owned_rows:
        coupon = effective_coupon(account, owned, vip_enabled=operation.vip_enabled, locked=locked)
        if not applies_to(coupon, service) or coupon.discount_type == 'QUALIFICATION':
            continue
        if coupon.scope == 'SAFE_BOX' and (units != 1 or service.fulfillment_provider_count != 1):
            continue
        if coupon.discount_type == 'RATE':
            caps = [coupon.max_discount] if coupon.max_discount else []
            if coupon.limit_units:
                caps.append(limited_coupon_basis(service, units, coupon.limit_units, reservation_minutes) * (100 - coupon.discount_rate) // 100)
            vouchers.append(VoucherOption(owned.pk, coupon.name, 'RATE', threshold=coupon.threshold,
                rate=coupon.discount_rate, max_discount=min(caps) if caps else None))
        elif coupon.discount_type in ('DIRECT', 'THRESHOLD'):
            vouchers.append(VoucherOption(owned.pk, coupon.name, 'AMOUNT',
                                          threshold=coupon.threshold, amount=coupon.amount))
    try:
        return discount_options(original, percentages, vouchers, amount_threshold_basis='AFTER_RATE',
                                rate_threshold_basis='ORIGINAL')
    except DiscountOptionError as exc:
        raise ValidationError(str(exc)) from exc
