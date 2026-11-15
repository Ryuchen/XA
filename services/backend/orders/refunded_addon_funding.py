"""Combine a negotiated main-order refund with verified paid add-ons.

This calculation does not authorize a refund, release funding, or post an
income. Callers supply the locked main-refund plan and the verified add-on
plan for the same point in time; posting must verify those facts again.
"""
from dataclasses import replace

from .addon_funding import CombinedAddonFunding
from .points_funding_plan import PointsFundingPlanError
from .points_negotiation_preview import RefundedFunding


def combine_refunded_funding(main, addon, *, main_retained_by_account=None):
    if not isinstance(main, RefundedFunding) or not isinstance(addon, CombinedAddonFunding) or addon.main != main:
        raise PointsFundingPlanError('主单退款与加购资金依据不一致')
    return _combine(main, paid=addon.addon_paid_units, principal=addon.addon_principal_units,
        gift=addon.addon_gift_units, compensation=addon.addon_compensation_units,
        provider=addon.addon_provider_income_units, recovery=addon.addon_provider_recovery_units,
        inviter=addon.addon_inviter_income_units, deductions=addon.main_deductions,
        expected_required=addon.additional_store_funding_units, expected_store=addon.store_income_units,
        main_retained_by_account=main_retained_by_account)


def combine_refunded_history(main, history, *, main_retained_by_account=None):
    """Use the independently verified add-on events at a past boundary."""
    if not isinstance(main, RefundedFunding) or not isinstance(history, dict):
        raise PointsFundingPlanError('主单退款与加购历史资金依据不一致')
    facts = history.get('addon_history')
    if not isinstance(facts, list) or not facts:
        raise PointsFundingPlanError('加购历史资金依据缺失')
    keys = ('paid_units', 'principal_units', 'gift_units', 'compensation_units',
            'provider_units', 'recovery_units', 'inviter_units')
    if any(not isinstance(row, dict) or any(type(row.get(key)) is not int or row[key] < 0 for key in keys)
           for row in facts):
        raise PointsFundingPlanError('加购历史资金金额异常')
    totals = {key: sum(row[key] for row in facts) for key in keys}
    return _combine(main, paid=totals['paid_units'], principal=totals['principal_units'],
        gift=totals['gift_units'], compensation=totals['compensation_units'],
        provider=totals['provider_units'], recovery=totals['recovery_units'], inviter=totals['inviter_units'],
        deductions=history.get('main_deductions', ()),
        expected_required=history.get('additional_store_funding_units'), expected_store=history.get('store_income_units'),
        main_retained_by_account=main_retained_by_account)


def _combine(main, *, paid, principal, gift, compensation, provider, recovery, inviter, deductions,
             expected_required, expected_store, main_retained_by_account):
    deducted = sum(amount for _, amount in deductions)
    if deducted and (main_retained_by_account is None or any(
            account not in main_retained_by_account or amount > main_retained_by_account[account]
            for account, amount in deductions)):
        raise PointsFundingPlanError('主单与加购退款的个人收益分摊尚待核对')
    paid += main.net_coin_units
    principal += main.net_principal_units
    gift += main.net_gift_units
    compensation += main.net_compensation_units
    provider += main.provider_income_units - deducted
    recovery += main.planned_provider_external_units
    if inviter or paid != principal + gift + compensation:
        raise PointsFundingPlanError('加购支付来源或推荐佣金不一致')
    margin = principal + recovery - provider - main.inviter_income_units
    required = max(main.rounding_reserved_units - margin, 0)
    store = margin + required
    if (required != expected_required or store != expected_store
            or principal + recovery + required != provider + main.inviter_income_units + store):
        raise PointsFundingPlanError('主单退款与加购资金不守恒')
    return replace(main, net_coin_units=paid, net_principal_units=principal, net_gift_units=gift,
        net_compensation_units=compensation, provider_income_units=provider,
        planned_provider_external_units=recovery, store_income_units=store, required_units=required,
        release_units=max(main.allocated_units - required, 0),
        remaining_units=max(required - main.allocated_units, 0))
