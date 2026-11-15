"""MySQL receipt clock evidence for committed grab windows.

Database time is the common clock; the same worker brackets each sample with
its monotonic clock. A changed connection, database identity, or inconsistent
elapsed time cannot establish which side of a deadline the commit occurred.
"""
from datetime import datetime, timezone
import os
import time

from django.conf import settings
from stores.db import connection

TOLERANCE_NS = 50_000_000


def production_enabled():
    return bool(connection.vendor == 'mysql' and getattr(settings, 'GRAB_COMMITTED_OPENING_ENABLED', False)
        and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
        and (getattr(settings, 'GRAB_SINGLE_WINDOW_ENABLED', False)
             or getattr(settings, 'GRAB_TEAM_WINDOW_ENABLED', False)))


def database_id():
    with connection.cursor() as cursor:
        cursor.execute('SELECT @@server_uuid')
        value = cursor.fetchone()[0]
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError('门店数据库身份无法核验')
    return value


def round_identity_valid(round_, *, identity=None, allow_prepared=False):
    if not production_enabled():
        return False
    from .models import OrderGrabPoolOpeningIntent
    marker = OrderGrabPoolOpeningIntent.objects.filter(order_id=round_.pool.order_id, state='OBSERVED').first()
    if not (marker and marker.database_identity and marker.commit_observed_at
                and marker.commit_observed_at <= round_.opened_at
                and marker.database_identity == (identity or database_id())):
        return False
    from .grab_round_opening import opening_bounds, provisional_opening_lower
    try:
        opening_bounds(round_)
    except ValueError:
        if not allow_prepared:
            return False
        try:
            provisional_opening_lower(round_)
        except ValueError:
            return False
    return True


def sample():
    if not production_enabled():
        raise ValueError('正式抢单时钟未开放')
    connection.ensure_connection()
    driver = connection.connection
    before = time.monotonic_ns()
    with connection.cursor() as cursor:
        cursor.execute('SELECT UTC_TIMESTAMP(6), @@server_uuid, CONNECTION_ID()')
        instant, identity, connection_id = cursor.fetchone()
    after = time.monotonic_ns()
    if (driver is not connection.connection or not isinstance(instant, datetime)
            or not isinstance(identity, str) or not identity or len(identity) > 64
            or type(connection_id) is not int or connection_id < 1 or after < before):
        raise ValueError('门店数据库时钟观察失败')
    instant = instant.replace(tzinfo=timezone.utc) if instant.utcoffset() is None else instant.astimezone(timezone.utc)
    return instant, driver, {'database_identity': identity, 'connection_id': connection_id,
                            'process_id': os.getpid(), 'before_ns': before, 'after_ns': after}


def valid_bounds(lower, upper, evidence, *, identity):
    if not isinstance(evidence, dict) or set(evidence) != {'lower', 'upper'}:
        return False
    first, second = evidence['lower'], evidence['upper']
    keys = {'database_identity', 'connection_id', 'process_id', 'before_ns', 'after_ns'}
    if (not isinstance(first, dict) or not isinstance(second, dict)
            or set(first) != keys or set(second) != keys
            or any(type(sample.get(key)) is not int or sample[key] < 0
                   for sample in (first, second) for key in ('connection_id', 'process_id', 'before_ns', 'after_ns'))
            or not first['connection_id'] or not first['process_id']
            or first['database_identity'] != identity or second['database_identity'] != identity
            or first['connection_id'] != second['connection_id'] or first['process_id'] != second['process_id']
            or first['before_ns'] > first['after_ns'] or first['after_ns'] > second['before_ns']
            or second['before_ns'] > second['after_ns'] or upper < lower):
        return False
    difference = upper - lower
    elapsed_ns = ((difference.days * 86400 + difference.seconds) * 1_000_000_000 + difference.microseconds * 1000)
    return (second['before_ns'] - first['after_ns'] - TOLERANCE_NS <= elapsed_ns
            <= second['after_ns'] - first['before_ns'] + TOLERANCE_NS)
