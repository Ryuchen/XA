"""Current store's provider capacity rule; never cache across tenants."""
import json

from stores.db import connection, transaction
from wallet.models import SystemConfig

CONFIG_KEY = 'provider_capacity_rules'
MAX_CAPACITY = 2_147_483_647
UNCONFIGURED = '__UNCONFIGURED__'


def parse_rules(raw):
    if raw is None or raw == UNCONFIGURED:
        from .models import Order
        return {'max_concurrent_orders': Order.MAX_CONCURRENT_ORDERS, 'version': 0}
    try:
        value = json.loads(raw)
        limit = value['max_concurrent_orders']
        version = value['version']
        if (type(limit) is not int or not 1 <= limit <= MAX_CAPACITY
                or type(version) is not int or version < 0):
            raise ValueError()
        return {'max_concurrent_orders': limit, 'version': version}
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise ValueError('本店接单名额配置异常，请联系管理员核对') from None


def current_capacity_rules():
    locked = getattr(connection, '_xa_capacity_rule_snapshot', None)
    if connection.in_atomic_block and locked and locked[0] in connection.atomic_blocks:
        # A locking read sees the latest committed value even under MySQL
        # REPEATABLE READ; a later ordinary SELECT could use an older snapshot.
        return parse_rules(locked[1])
    raw = SystemConfig.objects.filter(key=CONFIG_KEY).values_list('value', flat=True).first()
    return parse_rules(raw)


def current_capacity_limit():
    return current_capacity_rules()['max_concurrent_orders']


def ensure_capacity_rule():
    """Create only a sentinel lock row; it is not an operator configuration."""
    SystemConfig.objects.get_or_create(key=CONFIG_KEY, defaults={
        'value': UNCONFIGURED, 'remark': '本店陪玩同时接单名额（尚未配置）'})


def lock_capacity_rule_for_admission():
    """Hold the rule stable until the current admission transaction commits.

    MySQL/PostgreSQL shared row locks allow unrelated admissions to proceed
    together while an administrator's SELECT FOR UPDATE waits. SQLite does
    not support row locks; its tests exercise behavior, not this concurrency.
    The sentinel must have been created before acquiring business locks.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('接单规则锁必须在事务内获取')
    if connection.vendor in ('mysql', 'postgresql'):
        table = connection.ops.quote_name(SystemConfig._meta.db_table)
        key = connection.ops.quote_name('key')
        value = connection.ops.quote_name('value')
        clause = 'LOCK IN SHARE MODE' if connection.vendor == 'mysql' else 'FOR SHARE'
        with connection.cursor() as cursor:
            # `key` is a unique secondary index in InnoDB. Selecting only it
            # can use a covering index and fail to lock the clustered row
            # targeted by the editor's SELECT FOR UPDATE. Fetch the non-indexed
            # value so both paths lock the same actual configuration row.
            cursor.execute(f'SELECT {key}, {value} FROM {table} WHERE {key} = %s {clause}', [CONFIG_KEY])
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError('接单规则锁未初始化')
            raw = row[1]
    else:
        raw = SystemConfig.objects.select_for_update().filter(key=CONFIG_KEY).values_list('value', flat=True).first()
        if raw is None:
            raise RuntimeError('接单规则锁未初始化')
    # Atomic-block identity prevents a rolled-back or completed transaction
    # from reusing another transaction's captured value on this connection.
    connection._xa_capacity_rule_snapshot = (connection.atomic_blocks[-1], raw)
