"""订单拆账计算。

将一笔订单金额拆分为：陪玩实得 + 推荐人分佣 + 店铺留存。
口径（金额单位均为分，整数运算，向下取整后用余项兜底保证三者之和恒等于总额）：
  - 百分比方式：平台抽成 = amount * commission_rate / 100
  - 固定金额方式：平台抽成 = min(commission_fixed, amount)
  - 陪玩实得   = amount - 平台抽成
  - 推荐人分佣 = 平台抽成 * inviter_commission_rate / 100
  - 店铺留存   = 平台抽成 - 推荐人分佣
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class OrderSplit:
    commission_rate: int       # 平台抽成率快照(%)
    provider_income: int       # 陪玩实得（内部账务单位）
    inviter_commission: int    # 推荐人分佣（内部账务单位）
    shop_income: int           # 店铺/平台留存（内部账务单位）


def compute_split(
    amount,
    commission_rate,
    inviter_commission_rate=0,
    commission_type='PERCENT',
    commission_fixed=0,
):
    """计算订单拆账。

    Args:
        amount: 订单实付金额（分，非负整数）
        commission_rate: 平台抽成率(%) 0-100，百分比方式生效
        inviter_commission_rate: 推荐人分佣率(%) 0-100，按平台抽成部分计提
        commission_type: 'PERCENT'(百分比) 或 'FIXED'(固定金额)
        commission_fixed: 固定抽成额（内部账务单位），固定金额方式生效

    Returns:
        OrderSplit，且 provider_income + inviter_commission + shop_income == amount
    """
    amount = max(int(amount or 0), 0)
    commission_rate = _clamp_rate(commission_rate)
    inviter_commission_rate = _clamp_rate(inviter_commission_rate)

    if commission_type == 'FIXED':
        platform_cut = min(max(int(commission_fixed or 0), 0), amount)
    else:
        platform_cut = amount * commission_rate // 100
    provider_income = amount - platform_cut
    inviter_commission = platform_cut * inviter_commission_rate // 100
    shop_income = platform_cut - inviter_commission

    return OrderSplit(
        commission_rate=commission_rate,
        provider_income=provider_income,
        inviter_commission=inviter_commission,
        shop_income=shop_income,
    )


def _clamp_rate(rate):
    try:
        rate = int(rate)
    except (TypeError, ValueError):
        return 0
    return min(max(rate, 0), 100)
