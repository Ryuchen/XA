"""兴安电竞已确认的资金口径，金额单位为分（100 = 1 元）。"""
from .models import SystemConfig

DEPOSIT_PERCENT_KEY = 'provider_deposit_income_percent'


def club_income_rules_enabled():
    return SystemConfig.objects.filter(key='club_income_rules_enabled', value='1').exists()


def withdraw_balance_threshold():
    return 5000 if club_income_rules_enabled() else None


def order_hold_hours():
    return 72 if club_income_rules_enabled() else 0


def deposit_target(mode):
    cash = 20000
    return cash * 2 if mode == 'EARNINGS' else cash


def deposit_income_percent():
    raw = SystemConfig.objects.filter(key=DEPOSIT_PERCENT_KEY).values_list('value', flat=True).first()
    try:
        value = int(raw)
        return value if 0 <= value <= 100 else 60
    except (ValueError, TypeError):
        return 60


def deposit_rules():
    percent = deposit_income_percent()
    return {
        'cash_target': deposit_target('CASH'), 'earnings_target': deposit_target('EARNINGS'),
        'earnings_multiplier': 2, 'deposit_percent': percent, 'settlement_percent': 100 - percent,
        'hold_hours': order_hold_hours(),
    }
