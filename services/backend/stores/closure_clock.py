"""Auditable control-database UTC clock for closure notice deadlines.

SQLite is only an explicit test seam. Production closure decisions require the
control MySQL server's UTC clock; application wall time never sets a deadline.
"""
from dataclasses import dataclass
from datetime import datetime, timezone as datetime_timezone

from django.conf import settings
from django.db import connections

CLOCK_SOURCE = 'CONTROL_DB_UTC_V1'


class ControlClockUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class ControlClockEvidence:
    instant: datetime
    source: str
    identity: str


def _aware_utc(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ControlClockUnavailable('控制库时钟格式无效') from exc
    if not isinstance(value, datetime):
        raise ControlClockUnavailable('控制库时钟格式无效')
    if value.utcoffset() is None:
        return value.replace(tzinfo=datetime_timezone.utc)
    return value.astimezone(datetime_timezone.utc)


def read_control_clock():
    connection = connections['default']
    try:
        if connection.vendor == 'mysql':
            with connection.cursor() as cursor:
                cursor.execute('SELECT UTC_TIMESTAMP(6), @@server_uuid, CONNECTION_ID()')
                raw, server_uuid, connection_id = cursor.fetchone()
            if not isinstance(server_uuid, str) or not server_uuid or type(connection_id) is not int:
                raise ControlClockUnavailable('控制库时钟身份证据无效')
            identity = f'mysql:{server_uuid}:{connection_id}'
        elif connection.vendor == 'sqlite' and getattr(
                settings, 'CLOSURE_CONTROL_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY', False) is True:
            with connection.cursor() as cursor:
                cursor.execute('SELECT CURRENT_TIMESTAMP')
                raw = cursor.fetchone()[0]
            identity = f'sqlite-test:{connection.alias}'
        else:
            raise ControlClockUnavailable('控制库未提供可信闭店时钟')
        evidence = ControlClockEvidence(_aware_utc(raw), CLOCK_SOURCE, identity)
        if not evidence.identity or len(evidence.identity) > 80:
            raise ControlClockUnavailable('控制库时钟身份证据无效')
        return evidence
    except ControlClockUnavailable:
        raise
    except Exception as exc:
        raise ControlClockUnavailable('控制库时钟采样失败') from exc
