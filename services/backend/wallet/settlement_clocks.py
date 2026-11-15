"""Per-store settlement clock settings; gift orders snapshot a single version."""
import json

from .models import SystemConfig

CONFIG_KEY = 'settlement_clock_rules'
DEFAULT_RULES = {'aftersale_hours': 72, 'income_freeze_hours': 72, 'version': 0}
MAX_HOURS = 8760


def parse_rules(raw):
    if raw is None:
        return dict(DEFAULT_RULES)
    try:
        value = json.loads(raw)
        if (not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] < 0
                or any(type(value.get(k)) is not int or not 0 <= value[k] <= MAX_HOURS
                       for k in ('aftersale_hours', 'income_freeze_hours'))):
            raise ValueError()
        return {key: value[key] for key in DEFAULT_RULES}
    except (ValueError, TypeError, json.JSONDecodeError):
        raise ValueError('门店结算时限配置无效，请联系管理员核对') from None


def current_rules():
    raw = SystemConfig.objects.filter(key=CONFIG_KEY).values_list('value', flat=True).first()
    return parse_rules(raw)
