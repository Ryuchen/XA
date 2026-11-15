"""Persist observable clock anomalies; never grants production clock trust.

One process epoch is intentionally conservative: another worker or process
restart freezes this store until a future, separately authorized recovery
protocol exists. Interval consistency cannot prove UTC accuracy, prevent a
VM snapshot replay, or attest the database clock source.
"""
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
import os
import threading
import time
import uuid

from django.conf import settings
from django.db import DatabaseError
from stores.context import current_database, current_store
from stores.db import connection, transaction

from .models import GrabClockDiagnosticObservation, GrabClockDiagnosticState


# Diagnostic tolerances only. Passing them never authorizes a transaction.
MAX_SAMPLE_NS = 2_000_000_000
INTERVAL_TOLERANCE_NS = 5_001_000
_guard = threading.Lock()
_pid = None
_epoch = None
_unpersisted = {}


@dataclass(frozen=True)
class Result:
    status: str
    reason: str = ''
    persisted: bool = True
    production_authorized: bool = False


@dataclass(frozen=True)
class Sample:
    process_epoch: str
    process_id: int
    database_uuid: str
    connection_id: int
    server_uptime_seconds: int
    db_time: datetime
    before_ns: int
    after_ns: int


def _process_identity():
    global _pid, _epoch, _unpersisted
    pid = os.getpid()
    with _guard:
        if _pid != pid:
            _pid, _epoch, _unpersisted = pid, uuid.uuid4().hex, {}
        return _epoch, pid


def _key():
    store = current_store()
    alias = current_database()
    if (store is None or store.database_alias != alias
            or (getattr(settings, 'ISOLATED_CONTROL_PLANE', False) and alias == 'default')):
        raise ValueError('时钟诊断必须绑定门店业务库')
    return store.slug, alias


def _capture(epoch, pid):
    if connection.vendor != 'mysql':
        raise ValueError('当前数据库不支持时钟诊断')
    connection.ensure_connection()
    driver = connection.connection
    before = time.monotonic_ns()
    with connection.cursor() as cursor:
        cursor.execute('SELECT @@server_uuid, CONNECTION_ID(), UTC_TIMESTAMP(6)')
        server_id, connection_id, db_time = cursor.fetchone()
        cursor.execute("SHOW GLOBAL STATUS LIKE 'Uptime'")
        uptime_row = cursor.fetchone()
    after = time.monotonic_ns()
    uptime = int(uptime_row[1]) if uptime_row and len(uptime_row) == 2 else -1
    if (connection.connection is not driver or not isinstance(server_id, str) or not server_id
            or len(server_id) > 64 or type(connection_id) is not int or connection_id < 1
            or uptime < 0 or not isinstance(db_time, datetime) or after < before):
        raise ValueError('数据库连接或时钟身份无法验证')
    if db_time.utcoffset() is None:
        db_time = db_time.replace(tzinfo=dt_timezone.utc)
    else:
        db_time = db_time.astimezone(dt_timezone.utc)
    return Sample(epoch, pid, server_id, connection_id, uptime, db_time, before, after)


def _anomaly(state, sample):
    if sample.after_ns - sample.before_ns > MAX_SAMPLE_NS:
        return 'SAMPLE_TOO_SLOW'
    if not state.db_time:
        return ''
    if state.process_epoch != sample.process_epoch or state.process_id != sample.process_id:
        return 'PROCESS_EPOCH_CHANGED'
    if state.database_uuid != sample.database_uuid:
        return 'DATABASE_ID_CHANGED'
    if state.connection_id != sample.connection_id:
        return 'CONNECTION_CHANGED'
    if state.server_uptime_seconds is None:
        return 'SERVER_EPOCH_UNVERIFIABLE'
    if sample.server_uptime_seconds < state.server_uptime_seconds:
        return 'SERVER_EPOCH_CHANGED'
    if (state.monotonic_before_ns is None or state.monotonic_after_ns is None
            or sample.before_ns < state.monotonic_after_ns):
        return 'MONOTONIC_REGRESSION'
    delta = sample.db_time - state.db_time
    db_delta_ns = ((delta.days * 86400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000)
    lower = sample.before_ns - state.monotonic_after_ns - INTERVAL_TOLERANCE_NS
    upper = sample.after_ns - state.monotonic_before_ns + INTERVAL_TOLERANCE_NS
    uptime_delta_ns = (sample.server_uptime_seconds - state.server_uptime_seconds) * 1_000_000_000
    # MySQL reports integer seconds; two seconds allow quantization and query
    # bracketing. Agreement does not prove that no restart occurred.
    if not lower - 2_000_000_000 <= uptime_delta_ns <= upper + 2_000_000_000:
        return 'SERVER_EPOCH_CHANGED'
    if not lower <= db_delta_ns <= upper:
        return 'CLOCK_JUMP'
    return ''


def _latch(key, reason):
    with _guard:
        _unpersisted.setdefault(key, reason)


def _pending(key):
    with _guard:
        return _unpersisted.get(key, '')


def _clear(key):
    with _guard:
        _unpersisted.pop(key, None)


def _persist_failure(reason):
    """Persist a prior unavailable observation without inventing a sample."""
    with transaction.atomic(durable=True):
        GrabClockDiagnosticState.objects.get_or_create(pk=1, defaults={'status': 'FROZEN', 'frozen_reason': reason})
        state = GrabClockDiagnosticState.objects.select_for_update().get(pk=1)
        if state.status != 'FROZEN':
            state.status, state.frozen_reason = 'FROZEN', reason
            state.save(update_fields=['status', 'frozen_reason', 'updated_at'])
        return Result(state.status, state.frozen_reason)


def observe():
    """Collect one diagnostic observation; all outcomes deny production use."""
    try:
        key = _key()
    except ValueError:
        return Result('FROZEN', 'STORE_CONTEXT_UNAVAILABLE', persisted=False)
    epoch, pid = _process_identity()
    try:
        if transaction.get_connection().in_atomic_block or not transaction.get_autocommit():
            _latch(key, 'NON_DURABLE_CONTEXT')
            return Result('FROZEN', 'NON_DURABLE_CONTEXT', persisted=False)
        pending = _pending(key)
        if pending:
            result = _persist_failure(pending)
            _clear(key)
            return result
        with transaction.atomic(durable=True):
            _, created = GrabClockDiagnosticState.objects.get_or_create(pk=1, defaults={'status': 'BASELINE'})
            state = GrabClockDiagnosticState.objects.select_for_update().get(pk=1)
            if state.status == 'FROZEN':
                return Result(state.status, state.frozen_reason)
            if state.status not in ('BASELINE', 'OBSERVING') or (not created and not state.db_time):
                state.status, state.frozen_reason = 'FROZEN', 'STATE_UNVERIFIABLE'
                state.save(update_fields=['status', 'frozen_reason', 'updated_at'])
                return Result(state.status, state.frozen_reason)
            sample = _capture(epoch, pid)
            reason = _anomaly(state, sample)
            verdict = 'FROZEN' if reason else ('OBSERVING' if state.db_time else 'BASELINE')
            GrabClockDiagnosticObservation.objects.create(state=state, process_epoch=sample.process_epoch,
                process_id=sample.process_id, database_uuid=sample.database_uuid, connection_id=sample.connection_id,
                server_uptime_seconds=sample.server_uptime_seconds, db_time=sample.db_time,
                monotonic_before_ns=sample.before_ns, monotonic_after_ns=sample.after_ns,
                verdict=verdict, reason=reason)
            state.status = verdict
            if reason:
                state.frozen_reason = reason
            else:
                state.process_epoch, state.process_id = sample.process_epoch, sample.process_id
                state.database_uuid, state.connection_id = sample.database_uuid, sample.connection_id
                state.server_uptime_seconds = sample.server_uptime_seconds
                state.db_time = sample.db_time
                state.monotonic_before_ns, state.monotonic_after_ns = sample.before_ns, sample.after_ns
            state.save()
            return Result(verdict, reason)
    except (DatabaseError, ValueError, TypeError, OverflowError):
        # If the store DB is unreachable, the sticky in-process latch prevents
        # an immediate healthy-looking sample; persist it on the next call.
        _latch(key, 'OBSERVATION_UNVERIFIABLE')
        try:
            result = _persist_failure('OBSERVATION_UNVERIFIABLE')
        except (DatabaseError, ValueError, TypeError, OverflowError):
            return Result('FROZEN', 'OBSERVATION_UNVERIFIABLE', persisted=False)
        _clear(key)
        return result
