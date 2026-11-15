"""Versioned points-redemption terms in the current store's dictionary.

Having terms does not enable redemption. Service checkout, refund and
settlement must not be inferred from the existence of this configuration.
"""
import json

from wallet.models import SystemConfig

CONFIG_KEY = 'points_redemption_terms'
MAX_STORED_AMOUNT = 2_147_483_647


def parse_rules(raw):
    if raw is None:
        return {'group_point_hundredths': None, 'group_coin_units': None,
                'max_ratio_basis_points': None, 'version': 0}
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {
                'group_point_hundredths', 'group_coin_units', 'max_ratio_basis_points', 'version'}:
            raise ValueError()
        points = data['group_point_hundredths']
        coins = data['group_coin_units']
        ratio = data['max_ratio_basis_points']
        version = data['version']
        if (type(points) is not int or not 1 <= points <= MAX_STORED_AMOUNT
                or type(coins) is not int or not 10 <= coins <= MAX_STORED_AMOUNT or coins % 10
                or type(ratio) is not int or not 0 <= ratio < 10000
                or type(version) is not int or not 1 <= version <= MAX_STORED_AMOUNT):
            raise ValueError()
        return data
    except (ValueError, TypeError, KeyError):
        raise ValueError('本店积分抵扣配置异常，请联系管理员核对') from None


def current_rules():
    return parse_rules(SystemConfig.objects.filter(key=CONFIG_KEY).values_list('value', flat=True).first())
