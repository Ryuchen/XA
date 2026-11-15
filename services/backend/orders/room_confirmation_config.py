"""Store-local room confirmation timeout; missing rules are not guessed."""
import json
from wallet.models import SystemConfig

CONFIG_KEY = 'room_confirmation_deadline'
MAX_MINUTES = 30 * 24 * 60


def parse_rules(raw):
    if raw is None:
        return {'minutes': None, 'version': 0}
    try:
        data = json.loads(raw)
        if (not isinstance(data, dict) or set(data) != {'minutes', 'version'}
                or type(data['minutes']) is not int or not 1 <= data['minutes'] <= MAX_MINUTES
                or type(data['version']) is not int or not 1 <= data['version'] <= 2147483647):
            raise ValueError()
        return data
    except (ValueError, TypeError, KeyError):
        raise ValueError('本店房间确认时限配置异常，请联系管理员核对') from None


def current_rules(*, lock=False):
    query = SystemConfig.objects.filter(key=CONFIG_KEY)
    if lock:
        query = query.select_for_update()
    return parse_rules(query.values_list('value', flat=True).first())


def required_rules(*, lock=False):
    rules = current_rules(lock=lock)
    if rules['version'] == 0:
        raise ValueError('本店尚未配置房间确认时限')
    return rules
