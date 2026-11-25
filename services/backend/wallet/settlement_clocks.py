"""Per-store settlement deadlines and their trusted database-clock evidence.

MySQL is the only production clock source.  SQLite is accepted solely through
an explicit test setting/factory; it is never a production fallback.  A clock
sample is an attested value, so callers cannot pass an arbitrary ``datetime``
to move a settlement deadline.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as dt_timezone
import json
import os
import time

from django.conf import settings
from django.utils import timezone

from orders.settlement import SettlementError
from stores.db import connection

from .models import SystemConfig

CONFIG_KEY = 'settlement_clock_rules'
DEFAULT_RULES = {'aftersale_hours': 72, 'income_freeze_hours': 72, 'version': 0}
MAX_HOURS = 8760
EVIDENCE_KEYS = frozenset({
    'database_identity', 'connection_id', 'process_id', 'before_ns', 'after_ns',
})
MAX_FUTURE_SKEW = timedelta(minutes=5)
_ATTESTATION = object()
_ACTIVE_CLOCK = ContextVar('settlement_trusted_clock', default=None)


class SettlementClockError(SettlementError):
    """Base class for fail-closed settlement clock errors."""


class ClockUnavailable(SettlementClockError):
    """The store database could not provide a verifiable clock."""


class ClockConflict(SettlementClockError):
    """Persisted clock evidence is absent, altered, or from another database."""


@dataclass(frozen=True)
class TrustedClock:
    """One attested UTC observation tied to the current store connection."""

    at: datetime
    driver: object = field(repr=False, compare=False)
    evidence: dict = field(repr=False)
    precision_us: int = 1
    _attestation: object = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if self._attestation is not _ATTESTATION:
            raise ClockUnavailable('结算时钟实例未通过可信采样')


def _utc(value):
    if not isinstance(value, datetime):
        raise ClockUnavailable('门店数据库返回了无效时间')
    if value.utcoffset() is None:
        return value.replace(tzinfo=dt_timezone.utc)
    return value.astimezone(dt_timezone.utc)


def _sqlite_test_enabled():
    return (connection.vendor == 'sqlite'
            and getattr(settings, 'SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY', False) is True)


def _new_clock(instant, driver, evidence):
    clock = TrustedClock(_utc(instant), driver, dict(evidence), 1, _ATTESTATION)
    if not valid_evidence(clock.evidence, clock.at):
        raise ClockUnavailable('结算时钟证据无效')
    return clock


def trusted_test_clock(instant=None):
    """Explicit SQLite-only fixture factory; never enabled by production settings."""
    if not _sqlite_test_enabled():
        raise ClockUnavailable('SQLite 结算时钟测试接口未显式启用')
    connection.ensure_connection()
    driver = connection.connection
    before = time.monotonic_ns()
    instant = _utc(instant if instant is not None else timezone.now())
    after = time.monotonic_ns()
    return _new_clock(instant, driver, {
        'database_identity': f'sqlite-test:{connection.alias}',
        'connection_id': id(driver),
        'process_id': os.getpid(),
        'before_ns': before,
        'after_ns': after,
    })


def sample(*, fresh=False):
    """Sample the current store DB, or reuse the invocation's attested clock.

    ``fresh=True`` is reserved for a caller that has just finished waiting for
    a lock and deliberately needs a new observation.  Nested planning calls
    reuse the same clock so preview/planning/posting cannot straddle a deadline.
    """
    active = None if fresh else _ACTIVE_CLOCK.get()
    if active is not None:
        if not same_connection(active):
            raise ClockUnavailable('结算事务的门店数据库连接已变化')
        return active
    try:
        connection.ensure_connection()
        driver = connection.connection
        if connection.vendor == 'mysql':
            before = time.monotonic_ns()
            with connection.cursor() as cursor:
                cursor.execute('SELECT UTC_TIMESTAMP(6), @@server_uuid, CONNECTION_ID()')
                instant, identity, connection_id = cursor.fetchone()
            after = time.monotonic_ns()
            if (driver is not connection.connection
                    or not isinstance(identity, str) or not identity or len(identity) > 64
                    or type(connection_id) is not int or connection_id < 1 or after < before):
                raise ClockUnavailable('门店数据库时钟连接或证据无效')
            instant = _utc(instant)
            # Host time is only an anomaly bound.  It never supplies the
            # business timestamp or a fallback when the database query fails.
            if instant > datetime.now(dt_timezone.utc) + MAX_FUTURE_SKEW:
                raise ClockUnavailable('门店数据库时钟异常超前')
            clock = _new_clock(instant, driver, {
                'database_identity': identity,
                'connection_id': connection_id,
                'process_id': os.getpid(),
                'before_ns': before,
                'after_ns': after,
            })
        elif _sqlite_test_enabled():
            clock = trusted_test_clock()
        else:
            raise ClockUnavailable('门店数据库未提供可信的结算时钟')
    except SettlementClockError:
        raise
    except Exception as exc:
        raise ClockUnavailable('门店数据库结算时钟采样失败') from exc
    if not same_connection(clock):
        raise ClockUnavailable('结算时钟采样后数据库连接发生变化')
    return clock


def valid_evidence(evidence, observed_at):
    if not isinstance(evidence, dict) or set(evidence) != EVIDENCE_KEYS:
        return False
    if not isinstance(observed_at, datetime) or observed_at.utcoffset() != timedelta(0):
        return False
    identity = evidence['database_identity']
    return bool(
        isinstance(identity, str) and identity and len(identity) <= 64
        and type(evidence['connection_id']) is int and evidence['connection_id'] >= 1
        and type(evidence['process_id']) is int and evidence['process_id'] >= 1
        and type(evidence['before_ns']) is int and evidence['before_ns'] >= 0
        and type(evidence['after_ns']) is int and evidence['after_ns'] >= evidence['before_ns']
    )


def same_connection(clock):
    try:
        return bool(
            isinstance(clock, TrustedClock) and clock._attestation is _ATTESTATION
            and valid_evidence(clock.evidence, clock.at)
            and connection.connection is clock.driver
            and type(clock.precision_us) is int and clock.precision_us >= 1
        )
    except Exception:
        return False


def require_clock(now=None, *, scoped_datetime=False):
    """Validate an attested clock; raw datetimes stay rejected by default.

    A few legacy internal helpers pass the already attested ``clock.at`` back
    through a datetime-shaped API. They may use ``scoped_datetime=True`` only
    inside ``clock_scope`` and only when the value is exactly that observation.
    This never turns an arbitrary application timestamp into a clock.
    """
    if now is None:
        return sample()
    if scoped_datetime and isinstance(now, datetime):
        active = _ACTIVE_CLOCK.get()
        if active is not None and same_connection(active) and now == active.at:
            return active
    if not same_connection(now):
        raise ClockUnavailable('结算必须使用当前门店数据库的可信时钟实例')
    return now


@contextmanager
def clock_scope(clock):
    """Reuse one observation through nested planning calls in this transaction."""
    clock = require_clock(clock)
    token = _ACTIVE_CLOCK.set(clock)
    try:
        yield clock
    finally:
        _ACTIVE_CLOCK.reset(token)


def validate_receipt(evidence, observed_at, current):
    """Verify an original receipt against a newly sampled store-DB identity."""
    current = require_clock(current)
    if not valid_evidence(evidence, observed_at):
        raise ClockConflict('结算时钟回执缺失或无效，需人工核对')
    if evidence['database_identity'] != current.evidence['database_identity']:
        raise ClockConflict('结算时钟回执属于其他门店数据库，需人工核对')
    if observed_at > current.at:
        raise ClockConflict('结算时钟回执时间晚于当前数据库时间，需人工核对')
    return current


def record(order, subject_type, subject_id, operation, clock, *, deadline_at=None):
    """Persist immutable clock evidence in the same tenant transaction.

    Repeating the exact original observation returns its receipt.  A changed
    observation or deadline for an already attested business fact is a conflict,
    never a replacement or a second receipt.
    """
    clock = require_clock(clock)
    from .models import SettlementClockEvidence
    existing = receipt(order, subject_type, subject_id, operation)
    if existing is not None:
        if (existing.observed_at != clock.at or existing.clock_evidence != clock.evidence
                or existing.deadline_at != deadline_at):
            raise ClockConflict('同一结算事实的时钟证据不一致，需人工核对')
        return existing
    return SettlementClockEvidence.objects.create(
        order=order, subject_type=subject_type, subject_id=subject_id,
        operation=operation, observed_at=clock.at, deadline_at=deadline_at,
        clock_evidence=dict(clock.evidence),
    )


def receipt(order, subject_type, subject_id, operation):
    from .models import SettlementClockEvidence
    return SettlementClockEvidence.objects.filter(
        order=order, subject_type=subject_type, subject_id=subject_id, operation=operation,
    ).first()


def verify(order, subject_type, subject_id, operation, observed_at, clock, *, deadline_at=None):
    row = receipt(order, subject_type, subject_id, operation)
    if row is None or row.observed_at != observed_at or row.deadline_at != deadline_at:
        raise ClockConflict('结算记录缺少可信时钟证据，需人工核对')
    return validate_receipt(row.clock_evidence, row.observed_at, clock)


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
