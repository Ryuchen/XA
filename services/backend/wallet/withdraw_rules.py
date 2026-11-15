"""One store's withdrawal rule snapshot and serialization with edits."""
import hashlib
import json

from django.conf import settings
from stores.db import transaction

from .models import MIN_WITHDRAW_AMOUNT_KEY, WITHDRAW_TAX_RATE_KEY, SystemConfig

BALANCE_THRESHOLD_KEY = 'withdraw_balance_threshold'
LOCK_KEY = 'withdraw_config_lock'
MAX_AMOUNT = 2147483647


def lock_withdraw_rules():
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('提现配置锁须在事务内使用')
    SystemConfig.objects.get_or_create(key=LOCK_KEY, defaults={'value': '', 'remark': '提现配置并发锁'})
    return SystemConfig.objects.select_for_update().get(key=LOCK_KEY)


def _valid_int(raw, fallback, maximum=MAX_AMOUNT):
    try:
        if not isinstance(raw, str) or not raw.isascii() or not raw.isdigit():
            raise ValueError()
        value = int(raw)
        if value > maximum:
            raise ValueError()
        return value
    except ValueError:
        return fallback


def withdraw_rule_snapshot(*, lock=False):
    """Read a store rule snapshot; writers use current reads after the mutex."""
    keys = (MIN_WITHDRAW_AMOUNT_KEY, WITHDRAW_TAX_RATE_KEY, BALANCE_THRESHOLD_KEY,
            'club_income_rules_enabled')
    rows = SystemConfig.objects.filter(key__in=keys).order_by('key')
    if lock:
        if not transaction.get_connection().in_atomic_block:
            raise RuntimeError('提现配置当前读须在持锁事务内执行')
        rows = rows.select_for_update()
    config = dict(rows.values_list('key', 'value'))
    club = config.get('club_income_rules_enabled') == '1'
    # Unconfigured stores preserve their former effective defaults until setup.
    old_min = 0 if club else getattr(settings, 'MIN_WITHDRAW_AMOUNT', 10000)
    minimum = _valid_int(config.get(MIN_WITHDRAW_AMOUNT_KEY), old_min)
    threshold = _valid_int(config.get(BALANCE_THRESHOLD_KEY), 5000 if club else None)
    rate = _valid_int(config.get(WITHDRAW_TAX_RATE_KEY), getattr(settings, 'WITHDRAW_TAX_RATE', 2), 100)
    values = {'min_amount': minimum, 'balance_threshold': threshold, 'tax_rate': rate}
    values['version'] = hashlib.sha256(json.dumps(values, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return values


def withdraw_config_fingerprint(*, lock=False):
    keys = (MIN_WITHDRAW_AMOUNT_KEY, WITHDRAW_TAX_RATE_KEY, BALANCE_THRESHOLD_KEY, 'club_income_rules_enabled')
    rows = SystemConfig.objects.filter(key__in=keys).order_by('key')
    if lock:
        if not transaction.get_connection().in_atomic_block:
            raise RuntimeError('提现配置当前读须在持锁事务内执行')
        rows = rows.select_for_update()
    rows = rows.values('id', 'key', 'value', 'updated_at')
    identity = [{'id': row['id'], 'key': row['key'], 'value': row['value'],
                 'updated_at': row['updated_at'].isoformat()} for row in rows]
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def withdrawal_setup_snapshot():
    """Do not pair rules read before an edit with the fingerprint after it."""
    for _ in range(3):
        before = withdraw_config_fingerprint()
        rules = withdraw_rule_snapshot()
        after = withdraw_config_fingerprint()
        if before == after:
            return {'rules': rules, 'fingerprint': after}
    raise ValueError('提现配置正在变化，请重新读取')


class WithdrawConfigChanged(ValueError):
    pass


def save_withdraw_rules(min_amount, tax_rate, threshold, *, version=None, fingerprint=None, audit=None):
    """Serialize configuration writes with withdrawal applications and confirmation."""
    with transaction.atomic():
        lock_withdraw_rules()
        previous = withdraw_rule_snapshot(lock=True)
        if ((version is not None and version != previous['version'])
                or (fingerprint is not None and fingerprint != withdraw_config_fingerprint(lock=True))):
            raise WithdrawConfigChanged('提现规则已被修改，请刷新后重新编辑')
        SystemConfig.objects.update_or_create(key=MIN_WITHDRAW_AMOUNT_KEY,
            defaults={'value': str(min_amount), 'remark': '最低提现店币（账务单位）'})
        SystemConfig.objects.update_or_create(key=WITHDRAW_TAX_RATE_KEY,
            defaults={'value': str(tax_rate), 'remark': '提现税率（百分比，0-100）'})
        if threshold is not None:
            SystemConfig.objects.update_or_create(key=BALANCE_THRESHOLD_KEY,
                defaults={'value': str(threshold), 'remark': '提现可用余额门槛（账务单位，须严格大于）'})
        current = withdraw_rule_snapshot(lock=True)
        if audit:
            audit(previous, current)
        if previous['version'] != current['version']:
            from .notifier import notify_withdraw_config_update
            transaction.on_commit(notify_withdraw_config_update)
        return current
