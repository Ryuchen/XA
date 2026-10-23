"""订单计价与抽成解析公共模块。

集中下单（C 端）与客服代派单（console）共用的两段逻辑，避免口径分叉：

1. 折扣链（决定老板实付 amount，单位均为内部账务单位整数，10 = 1 兴安币，向下取整）：
       original_amount    = service.price × game_rounds
       amount_after_boss  = original_amount × 老板VIP折扣率 // 100      ① BossType.discount_rate
       ┌─ 命中带折扣率的活动 → amount = amount_after_boss × 活动折扣率 // 100  ② Promotion（此时禁用券）
       └─ 否则若用券        → amount = amount_after_boss − min(券面额, amount_after_boss)  ③ 优惠券
   活动折扣与优惠券互斥：命中折扣率型活动时若仍传券 → PricingError。

2. 抽成率（优先级覆盖取其一，不叠加）：活动 > 陪玩等级 > 店铺(商品级→全局→settings)。
   注意：抽成率在下单时冻结。若下单时陪玩未指定（待接单），无法按等级计费，回落店铺。
"""

from dataclasses import dataclass

from django.utils import timezone

from wallet.models import get_commission_rate


class PricingError(Exception):
    """计价校验失败：由调用方转换为各自框架的错误响应。"""


@dataclass(frozen=True)
class DiscountResult:
    original_amount: int   # 原价
    boss_discount: int     # 老板VIP折扣额
    promo_discount: int    # 活动折扣额
    coupon_discount: int   # 优惠券抵扣额
    amount: int            # 最终实付


def _clamp_rate(rate) -> int:
    try:
        rate = int(rate)
    except (TypeError, ValueError):
        return 0
    return min(max(rate, 0), 100)


def resolve_active_promotion(service, now=None):
    """取当前对该商品生效的促销活动；并发多活动时取优先级最高的一条。"""
    from promotions.models import Promotion

    now = now or timezone.now()
    candidates = Promotion.objects.filter(
        is_active=True, start_at__lte=now, end_at__gte=now,
    )  # Meta 已按 -priority, -start_at 排序
    for promo in candidates:
        if promo.applies_to(service):
            return promo
    return None


def resolve_commission_rate(service, escort_profile, promotion) -> int:
    """解析平台抽成率(%)：活动 > 陪玩等级 > 店铺(商品级→全局)。"""
    rate, _ = resolve_commission_rate_with_source(service, escort_profile, promotion)
    return rate


def resolve_commission_rate_with_source(service, escort_profile, promotion):
    """同 resolve_commission_rate，但额外返回来源标识。

    Returns:
        (rate:int, source:str) —— source ∈ {'promotion','level','service','global'}
    """
    if getattr(service, 'experience_kind', ''):
        return (0 if service.experience_kind.startswith('NEW') else 10), 'club_experience'
    policy = getattr(service, 'commission_policy', 'LEGACY')
    if policy == 'EXPERIENCE':
        return 10, 'club_experience'
    from users.qualifications import service_requirement, commission_for
    scoped=service_requirement(service)
    if scoped and not scoped.get('invalid'):
        scoped_rate=scoped.get('commission_rate') if hasattr(service,'provider_requirement_snapshot') else commission_for(scoped)
        if scoped_rate is not None:return _clamp_rate(scoped_rate),'order_grade'
    if policy == 'COMPANION':
        return 20, 'club_companion'
    if policy in ('REGULAR', 'FUN'):
        level = getattr(escort_profile, 'level', None)
        return (_clamp_rate(level.commission_rate), 'level') if level and level.is_active else (_clamp_rate(get_commission_rate()), 'global')
    if promotion is not None and promotion.commission_rate is not None:
        return _clamp_rate(promotion.commission_rate), 'promotion'
    level = getattr(escort_profile, 'level', None)
    if level is not None and level.is_active:
        return _clamp_rate(level.commission_rate), 'level'
    rate = service.commission_rate
    if rate is None:
        return _clamp_rate(get_commission_rate()), 'global'
    return _clamp_rate(rate), 'service'


def club_provider_specs(service_or_order, profiles, amount):
    """固定3元为整单抽成，多陪份额只分配一次，不重复收取。"""
    from .commission import provider_specs
    custom = provider_specs(service_or_order,profiles,amount)
    if custom is not None:
        return custom
    policy = getattr(service_or_order, 'commission_policy', 'LEGACY')
    kind = getattr(service_or_order, 'experience_kind', '')
    if kind:
        return [{'commission_type': 'PERCENT', 'commission_rate': 0 if kind.startswith('NEW') else 10} for profile in profiles]
    from users.qualifications import service_requirement,commission_for
    requirement=service_requirement(service_or_order)
    if policy == 'LEGACY' and (not requirement or commission_for(requirement) is None):
        return None
    if policy == 'EXPERIENCE' and amount < 10000:
        from .settlement import split_settlement_bases
        fixed = split_settlement_bases(min(300, amount), len(profiles))
        return [{'commission_type': 'FIXED', 'commission_fixed': cut, 'commission_rate': 0} for cut in fixed]
    return [{'commission_type': 'PERCENT', 'commission_rate': resolve_commission_rate(service_or_order, profile, None)} for profile in profiles]


def compute_order_amount(
    original_amount,
    boss_rate,
    promotion=None,
    coupon_amount=None,
    coupon_threshold=0,
    coupon_rate=None,
    coupon_max_discount=None,
) -> DiscountResult:
    """计算折扣链并返回各项明细。

    Args:
        original_amount: 原价（内部账务单位，非负整数）
        boss_rate: 老板VIP折扣率(%)，100=原价
        promotion: 命中的 Promotion（可空）；其 discount_rate 非空时作用于实付
        coupon_amount: 优惠券面额（内部账务单位，可空）。传入即视为使用优惠券
        coupon_threshold: 优惠券使用门槛（内部账务单位），按老板折扣后金额判定

    Raises:
        PricingError: 活动折扣与优惠券互斥冲突，或未达优惠券门槛
    """
    original_amount = max(int(original_amount or 0), 0)
    boss_rate = _clamp_rate(boss_rate)

    amount_after_boss = original_amount * boss_rate // 100
    boss_discount = original_amount - amount_after_boss

    promo_rate = promotion.discount_rate if promotion is not None else None
    use_coupon = bool(coupon_amount) or coupon_rate is not None

    if promo_rate is not None and use_coupon:
        raise PricingError('该商品正在参加限时活动，暂不可叠加优惠券')

    promo_discount = 0
    coupon_discount = 0

    if promo_rate is not None:
        promo_rate = _clamp_rate(promo_rate)
        amount_after_promo = amount_after_boss * promo_rate // 100
        promo_discount = amount_after_boss - amount_after_promo
        amount = amount_after_promo
    elif use_coupon:
        if amount_after_boss < int(coupon_threshold or 0):
            raise PricingError('订单金额未达到优惠券使用门槛')
        if coupon_rate is not None:
            candidate = original_amount - original_amount * _clamp_rate(coupon_rate) // 100
            if coupon_max_discount is not None:
                candidate = min(candidate, coupon_max_discount)
            if candidate > boss_discount:
                coupon_discount = candidate
                boss_discount = 0
                amount = original_amount - coupon_discount
            else:
                amount = amount_after_boss
        else:
            coupon_discount = min(int(coupon_amount), amount_after_boss)
            amount = amount_after_boss - coupon_discount
    else:
        amount = amount_after_boss

    return DiscountResult(
        original_amount=original_amount,
        boss_discount=boss_discount,
        promo_discount=promo_discount,
        coupon_discount=coupon_discount,
        amount=amount,
    )
