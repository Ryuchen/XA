"""Versioned, tenant-local automatic grab window and frozen visibility rules."""
import json
from datetime import datetime

from stores.db import transaction
from wallet.models import (DEFAULT_PASS_DELAY_SECONDS, PASS_DELAY_SECONDS_KEY_PREFIX, PASS_TIER_NONE,
                           SystemConfig)

CONFIG_KEY = 'automatic_grab_window'
MAX_WINDOW_SECONDS = 86400


def parse_rules(raw):
    if raw is None:
        return {'window_seconds': None, 'version': 0}
    try:
        item = json.loads(raw)
        seconds, version = item['window_seconds'], item['version']
        if (type(seconds) is not int or not 1 <= seconds <= MAX_WINDOW_SECONDS
                or type(version) is not int or version < 1):
            raise ValueError()
        return {'window_seconds': seconds, 'version': version}
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        raise ValueError('本店抢单窗口配置异常，请联系管理员核对') from None


def current_rules():
    return parse_rules(SystemConfig.objects.filter(key=CONFIG_KEY).values_list('value', flat=True).first())


def snapshot_for_round(*, opened_at, original_total_deadline):
    """Freeze a configured window and all five delays under their writer locks.

    This only constructs data; it does not create a round or authorize a grab.
    Callers must persist it in the same transaction as the actual round.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('抢单规则快照必须在门店事务内生成')
    if (not isinstance(opened_at, datetime) or opened_at.utcoffset() is None
            or not isinstance(original_total_deadline, datetime) or original_total_deadline.utcoffset() is None):
        raise ValueError('抢单入池和总截止时间须包含时区')
    from users.pass_rules import lock_pass_config
    from .grab_window import first_round
    lock_pass_config()
    row = SystemConfig.objects.select_for_update().filter(key=CONFIG_KEY).first()
    rules = parse_rules(row.value if row else None)
    if rules['window_seconds'] is None:
        raise ValueError('本店尚未配置抢单窗口')
    keys = {tier: f'{PASS_DELAY_SECONDS_KEY_PREFIX}{tier or PASS_TIER_NONE}' for tier in DEFAULT_PASS_DELAY_SECONDS}
    # A plain SELECT could reuse an older MySQL REPEATABLE READ snapshot
    # established before the pass-configuration lock was obtained. Read the
    # delay rows as current locking reads, matching the locked writer.
    values = dict(SystemConfig.objects.select_for_update().filter(key__in=keys.values()).values_list('key', 'value'))
    delays = {}
    for tier, key in keys.items():
        raw = values.get(key)
        if raw is None:
            delays[tier] = DEFAULT_PASS_DELAY_SECONDS[tier]
        elif not isinstance(raw, str) or not raw.isascii() or not raw.isdigit() or int(raw) > MAX_WINDOW_SECONDS:
            raise ValueError('本店通行证可见延迟配置异常，请先核对')
        else:
            delays[tier] = int(raw)
    ordered = [delays[tier] for tier in ('BLACK', 'GOLD', 'SILVER', 'BRONZE', '')]
    if ordered != sorted(ordered):
        raise ValueError('本店通行证可见延迟顺序异常，请先核对')
    window = first_round(opened_at=opened_at, duration_seconds=rules['window_seconds'],
                         original_total_deadline=original_total_deadline)
    return {'window_seconds':rules['window_seconds'], 'config_version':rules['version'],
            'delays':delays, 'opened_at':window.opened_at, 'deadline':window.deadline,
            'total_deadline':window.total_deadline}
