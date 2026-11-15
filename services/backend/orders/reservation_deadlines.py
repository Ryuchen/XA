"""Per-store reservation deadlines; no implicit durations are assumed."""
import json
from datetime import timedelta

from wallet.models import SystemConfig

CONFIG_KEY = 'reservation_deadlines'
MAX_MINUTES = 30 * 24 * 60
WINDOW_DEFAULTS = {'slot_minutes': 15, 'min_duration_minutes': 15,
                   'max_duration_minutes': 720, 'max_advance_days': 30}
WINDOW_KEYS = frozenset(WINDOW_DEFAULTS)
CONFIG_FIELDS = WINDOW_KEYS | {'confirmation_minutes', 'payment_minutes', 'version'}


def valid_window(data):
    try:
        slot, minimum, maximum, advance = (data[key] for key in
            ('slot_minutes', 'min_duration_minutes', 'max_duration_minutes', 'max_advance_days'))
        return (all(type(value) is int for value in (slot, minimum, maximum, advance))
                and slot in (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30, 60)
                and slot <= minimum <= maximum <= MAX_MINUTES
                and minimum % slot == maximum % slot == 0 and 1 <= advance <= 365)
    except KeyError:
        return False


def parse_rules(raw):
    if raw is None:
        return {'confirmation_minutes': None, 'payment_minutes': None, 'version': 0, **WINDOW_DEFAULTS}
    try:
        data = json.loads(raw)
        if (not isinstance(data, dict) or set(data) != CONFIG_FIELDS
                or any(type(data[k]) is not int or not 1 <= data[k] <= MAX_MINUTES
                       for k in ('confirmation_minutes', 'payment_minutes'))
                or type(data['version']) is not int or data['version'] < 1
                or not valid_window(data)):
            raise ValueError()
        return data
    except (ValueError, TypeError, KeyError):
        raise ValueError('本店预约时限配置异常，请联系管理员核对') from None


def current_rules(*, lock=False):
    query = SystemConfig.objects.filter(key=CONFIG_KEY)
    if lock:
        query = query.select_for_update()
    return parse_rules(query.values_list('value', flat=True).first())


def required_rules(*, lock=False):
    rules = current_rules(lock=lock)
    if rules['version'] == 0:
        raise ValueError('本店尚未配置预约确认和付款时限')
    return rules


def due(reservation, *, now):
    """Legacy rows without snapshots keep their former end-time behavior."""
    if reservation.status == reservation.Status.PENDING:
        deadline = reservation.confirmation_deadline
    elif reservation.status == reservation.Status.CONFIRMED:
        deadline = reservation.payment_deadline
    else:
        return False
    return (deadline if deadline is not None else reservation.end_time) <= now


def confirmation_snapshot(start, now, rules):
    deadline = now + timedelta(minutes=rules['confirmation_minutes'])
    if start < deadline:
        raise ValueError('距预约开始不足本店确认时限，请选择更晚的时间')
    return deadline


def payment_snapshot(start, confirmed_at, rules):
    return min(start, confirmed_at + timedelta(minutes=rules['payment_minutes']))
