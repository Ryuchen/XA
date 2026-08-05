"""订单拆账计算。

将一笔订单金额拆分为：陪玩实得 + 推荐人分佣 + 店铺留存。
口径（金额单位均为分，整数运算，向下取整后用余项兜底保证三者之和恒等于总额）：
  - 百分比方式：平台抽成 = amount * commission_rate / 100
  - 固定金额方式：平台抽成 = min(commission_fixed, amount)
  - 陪玩实得   = amount - 平台抽成
  - 推荐人分佣 = 平台抽成 * inviter_commission_rate / 100
  - 店铺留存   = 平台抽成 - 推荐人分佣

多打手（双陪）口径 —— 资金守恒的唯一正确算法：
  订单实付先在打手之间**均分**得到各自结算基数（余数给靠前打手），
  再由每个打手按**自己的**抽成规则从各自基数中计算实得。
  绝不允许每个打手都以订单全额为基数，否则总流出 > 实付，平台净亏。

    例：实付 1000，双陪，A 抽成 20%、B 抽成 30%
        基数 A=500 B=500
        实得 A=400 B=350，打手合计 750
        平台抽成 250 → 推荐人分佣 + 店铺留存
        合计 750 + 250 = 1000 ✅
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class OrderSplit:
    commission_rate: int       # 平台抽成率快照(%)
    provider_income: int       # 陪玩实得（内部账务单位）
    inviter_commission: int    # 推荐人分佣（内部账务单位）
    shop_income: int           # 店铺/平台留存（内部账务单位）


@dataclass(frozen=True)
class ProviderShare:
    """单个打手的结算份额快照，与 OrderProvider 行一一对应。"""

    settlement_base: int       # 分配给该打手的结算基数（内部账务单位）
    commission_type: str       # 'PERCENT' | 'FIXED'
    commission_rate: int       # 抽成率(%)，百分比方式生效
    commission_fixed: int      # 固定抽成额，固定金额方式生效
    provider_income: int       # 该打手实得（内部账务单位）


class SettlementError(Exception):
    """拆账口径异常：资金不守恒或入参非法。"""


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


def split_settlement_bases(amount, provider_count):
    """把订单实付均分成 ``provider_count`` 份结算基数，余数依次给靠前的打手。

    Returns:
        list[int]，长度等于 provider_count，且 sum(结果) == amount
    """
    amount = max(int(amount or 0), 0)
    provider_count = max(int(provider_count or 0), 0)
    if provider_count == 0:
        return []
    base, remainder = divmod(amount, provider_count)
    return [base + (1 if idx < remainder else 0) for idx in range(provider_count)]


def compute_provider_income(
    settlement_base,
    commission_type='PERCENT',
    commission_rate=0,
    commission_fixed=0,
):
    """按打手自己的抽成规则，从其结算基数中算出实得（向下取整，不超过基数）。"""
    settlement_base = max(int(settlement_base or 0), 0)
    if commission_type == 'FIXED':
        cut = min(max(int(commission_fixed or 0), 0), settlement_base)
    else:
        cut = settlement_base * _clamp_rate(commission_rate) // 100
    return settlement_base - cut


def build_provider_shares(amount, provider_specs):
    """为多打手订单生成每人的结算份额。

    Args:
        amount: 订单实付金额（内部账务单位）
        provider_specs: list[dict]，每项可含
            ``commission_type`` / ``commission_rate`` / ``commission_fixed``

    Returns:
        list[ProviderShare]，与入参顺序一一对应。
        保证 sum(settlement_base) == amount，且每人 provider_income <= 自己的基数。
    """
    bases = split_settlement_bases(amount, len(provider_specs))
    shares = []
    for base, spec in zip(bases, provider_specs):
        ctype = (spec.get('commission_type') or 'PERCENT').upper()
        if ctype == 'FIXED':
            fixed = min(max(int(spec.get('commission_fixed') or 0), 0), base)
            rate = 0
        else:
            ctype = 'PERCENT'
            fixed = 0
            rate = _clamp_rate(spec.get('commission_rate'))
        shares.append(
            ProviderShare(
                settlement_base=base,
                commission_type=ctype,
                commission_rate=rate,
                commission_fixed=fixed,
                provider_income=compute_provider_income(base, ctype, rate, fixed),
            )
        )
    return shares


def aggregate_order_split(amount, shares, inviter_commission_rate=0, primary_rate=0):
    """把多打手份额汇总成订单级拆账（写入 Order 的 4 个分账字段）。

    平台抽成 = 实付 − 打手实得合计，再按推荐分佣率切给推荐人，剩余归店铺。
    结果恒满足：打手实得合计 + 推荐人分佣 + 店铺留存 == amount
    """
    amount = max(int(amount or 0), 0)
    total_provider_income = sum(s.provider_income for s in shares)
    if total_provider_income > amount:
        raise SettlementError(
            f'打手实得合计 {total_provider_income} 超过订单实付 {amount}，拒绝拆账'
        )
    platform_cut = amount - total_provider_income
    inviter_commission = platform_cut * _clamp_rate(inviter_commission_rate) // 100
    return OrderSplit(
        commission_rate=_clamp_rate(
            shares[0].commission_rate if shares else primary_rate
        ),
        provider_income=total_provider_income,
        inviter_commission=inviter_commission,
        shop_income=platform_cut - inviter_commission,
    )


def assert_order_conserved(amount, provider_incomes, inviter_commission, shop_income):
    """结算前的最后一道闸门：校验实际将要流出的资金不超过订单实付。

    单条 CheckConstraint 只能看一行，拦不住「双陪两行各拿全额」这种跨行超付，
    所以在真正给钱包加钱之前必须做一次跨行汇总校验。

    Raises:
        SettlementError: 总流出 > 实付
    """
    amount = max(int(amount or 0), 0)
    total_out = (
        sum(max(int(x or 0), 0) for x in provider_incomes)
        + max(int(inviter_commission or 0), 0)
        + max(int(shop_income or 0), 0)
    )
    if total_out > amount:
        raise SettlementError(
            f'分账超额：待入账合计 {total_out} 超过订单实付 {amount}，已阻断结算'
        )
    return total_out


def _clamp_rate(rate):
    try:
        rate = int(rate)
    except (TypeError, ValueError):
        return 0
    return min(max(rate, 0), 100)
