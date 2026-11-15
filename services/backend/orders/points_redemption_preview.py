"""Read-only points estimate for an already validated boss service quote.

This deliberately does not authorize payment. The experimental receipt and
points-switch components do not by themselves integrate service checkout,
refunds or settlement.
"""
from engagement.models import ClubOperationConfig, MemberProfile

from .points_redemption_config import current_rules
from .points_redemption_math import quote_points_redemption
from .purchase_benefits import is_experience


def preview_service_points(*, account, service, deal_amount, selected_points):
    unavailable = {'preview_only': True, 'payment_available': False}
    if is_experience(service) or service.is_addon or (service.service_category_id and service.service_category.is_gift):
        return {**unavailable, 'applicable': False, 'reason': '此服务不支持积分抵扣'}
    rules = current_rules()
    if rules['version'] == 0:
        return {**unavailable, 'applicable': False, 'reason': '本店尚未配置积分抵扣'}
    operation = ClubOperationConfig.objects.filter(pk=1).first()
    if operation is None or not operation.points_enabled:
        return {**unavailable, 'applicable': False, 'reason': '本店积分规则未启用'}
    from engagement.points_usage import FIELDS, current_usage, enabled
    if enabled():
        issuance, switches = current_usage()
        if not issuance or not all(switches[field] for field in FIELDS):
            return {**unavailable, 'applicable': False, 'reason': '本店积分使用暂时暂停'}
    # No profile is created, no balance is reserved, and the caller cannot
    # substitute another account or a client-supplied balance.
    balance = MemberProfile.objects.filter(account_id=account.pk).values_list('points_balance', flat=True).first()
    balance = 0 if balance is None else balance
    quote = quote_points_redemption(payable_before_points_units=deal_amount,
        available_point_hundredths=balance, selected_point_hundredths=selected_points,
        group_point_hundredths=rules['group_point_hundredths'], group_coin_units=rules['group_coin_units'],
        max_ratio_basis_points=rules['max_ratio_basis_points'])
    return {**unavailable, 'applicable': True, 'balance_point_hundredths': balance,
            'selected_point_hundredths': selected_points, 'max_groups': quote.max_groups,
            'selected_groups': quote.selected_groups, 'used_point_hundredths': quote.points_hundredths,
            'discount_units': quote.discount_units, 'estimated_payable_units': quote.payable_units,
            'config': rules, 'notice': ('仅供试算，积分支付尚未开放；积分使用开关已联动'
                                      if enabled() else '仅供试算，积分支付尚未开放；积分使用开关尚未联动')}
