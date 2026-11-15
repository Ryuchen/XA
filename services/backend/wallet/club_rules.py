"""门店资金口径，金额使用内部单位（100 单位 = 人民币 1 元）。"""
import json

from .models import SystemConfig

DEPOSIT_PERCENT_KEY = 'provider_deposit_income_percent'
DEPOSIT_TARGET_KEY = 'provider_deposit_targets'
DEFAULT_DEPOSIT_TARGETS = {'cash_target': 40000, 'earnings_target': 80000, 'version': 0}


def club_income_rules_enabled():
    return SystemConfig.objects.filter(key='club_income_rules_enabled', value='1').exists()


def withdraw_balance_threshold():
    from .withdraw_rules import withdraw_rule_snapshot
    return withdraw_rule_snapshot()['balance_threshold']


def order_hold_hours():
    return 72 if club_income_rules_enabled() else 0


def service_clock_overview():
    """Current rules for new service orders; their two clocks run independently."""
    from .settlement_clocks import current_rules
    clocks = current_rules()
    return {'hold_hours': clocks['income_freeze_hours'], 'aftersale_hours': clocks['aftersale_hours'],
            'clock_version': clocks['version']}


def parse_deposit_targets(raw):
    if raw is None:
        return dict(DEFAULT_DEPOSIT_TARGETS)
    try:
        value = json.loads(raw)
        if (not isinstance(value, dict)
                or any(type(value.get(key)) is not int or not 1 <= value[key] <= 2147483647
                       for key in ('cash_target', 'earnings_target'))
                or type(value.get('version')) is not int or value['version'] < 0):
            raise ValueError
        return {key: value[key] for key in DEFAULT_DEPOSIT_TARGETS}
    except (ValueError, TypeError, json.JSONDecodeError):
        raise ValueError('门店押金目标配置无效，请联系管理员核对') from None


def deposit_targets():
    raw = SystemConfig.objects.filter(key=DEPOSIT_TARGET_KEY).values_list('value', flat=True).first()
    return parse_deposit_targets(raw)


def deposit_target(mode):
    targets = deposit_targets()
    return targets['earnings_target' if mode == 'EARNINGS' else 'cash_target']


def parse_deposit_income_percent(raw):
    try:
        value = int(raw)
        return value if 0 <= value <= 100 else 60
    except (ValueError, TypeError):
        return 60


def deposit_income_percent():
    raw = SystemConfig.objects.filter(key=DEPOSIT_PERCENT_KEY).values_list('value', flat=True).first()
    return parse_deposit_income_percent(raw)


def deposit_rules():
    percent = deposit_income_percent()
    targets = deposit_targets()
    return {
        **targets, 'earnings_multiplier': targets['earnings_target'] / targets['cash_target'],
        'deposit_percent': percent, 'settlement_percent': 100 - percent,
        **service_clock_overview(),
    }
