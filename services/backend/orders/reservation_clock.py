"""Tenant-database clock and conservative reservation deadline receipts.

Production must sample MySQL itself.  The SQLite branch is deliberately guarded
by a test-only setting and exists so historical tests can control time without
pretending that SQLite is a production clock source.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from hashlib import sha256
import json
import os
import time
import uuid

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError
from django.utils import timezone
from rest_framework.exceptions import APIException

from stores.db import connection as store_connection, transaction


EVIDENCE_TOLERANCE_NS = 50_000_000
_MONEY_EVIDENCE_KEYS = {
    'database_identity', 'connection_id', 'process_id', 'before_ns', 'after_ns',
}


class ClockUnavailable(APIException):
    status_code = 503
    default_detail = '门店数据库时钟不可用，预约操作未确认'
    default_code = 'reservation_clock_unavailable'


class ClockConflict(APIException):
    status_code = 409
    default_detail = '预约截止时间或时钟证据需核对'
    default_code = 'reservation_clock_review'


class ClockAfter(ClockConflict):
    default_detail = '预约已到达截止时间'
    default_code = 'reservation_clock_after'


@dataclass(frozen=True)
class ClockSample:
    instant: datetime
    driver: object
    evidence: dict
    precision_us: int = 1


def _aware_utc(value):
    if not isinstance(value, datetime):
        raise ClockUnavailable('门店数据库返回了无效时间')
    if value.utcoffset() is None:
        return value.replace(tzinfo=dt_timezone.utc)
    return value.astimezone(dt_timezone.utc)


def sample():
    """Return aware UTC time and evidence from the current store connection."""
    try:
        store_connection.ensure_connection()
        driver = store_connection.connection
        if store_connection.vendor == 'mysql':
            precision = 1
            before = time.monotonic_ns()
            with store_connection.cursor() as cursor:
                cursor.execute('SELECT UTC_TIMESTAMP(6), @@server_uuid, CONNECTION_ID()')
                instant, identity, connection_id = cursor.fetchone()
            after = time.monotonic_ns()
            if (driver is not store_connection.connection
                    or not isinstance(identity, str) or not identity or len(identity) > 64
                    or type(connection_id) is not int or connection_id < 1 or after < before):
                raise ClockUnavailable('门店数据库时钟连接或证据无效')
        elif store_connection.vendor == 'sqlite' and getattr(
                settings, 'RESERVATION_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY', False) is True:
            # Explicit test seam only: it preserves the established patchable
            # application-clock fixture while retaining connection identity.
            precision = 1
            before = time.monotonic_ns()
            instant = timezone.now()
            after = time.monotonic_ns()
            identity = f'sqlite-test:{store_connection.alias}'
            connection_id = id(driver)
        else:
            raise ClockUnavailable('门店数据库未提供可信的预约时钟')
        return ClockSample(
            _aware_utc(instant), driver,
            {
                'database_identity': identity,
                'connection_id': connection_id,
                'process_id': os.getpid(),
                'before_ns': before,
                'after_ns': after,
            },
            precision,
        )
    except ClockUnavailable:
        raise
    except Exception as exc:
        raise ClockUnavailable('门店数据库时钟采样失败') from exc


def same_connection(observed):
    return (observed is not None and store_connection.connection is observed.driver
            and type(observed.evidence.get('connection_id')) is int
            and observed.evidence['connection_id'] >= 1)


def _valid_evidence(observed):
    evidence = observed.evidence
    if not isinstance(evidence, dict) or set(evidence) != _MONEY_EVIDENCE_KEYS:
        return False
    return (
        isinstance(evidence['database_identity'], str)
        and bool(evidence['database_identity'])
        and len(evidence['database_identity']) <= 64
        and type(evidence['connection_id']) is int and evidence['connection_id'] >= 1
        and type(evidence['process_id']) is int and evidence['process_id'] >= 1
        and type(evidence['before_ns']) is int and evidence['before_ns'] >= 0
        and type(evidence['after_ns']) is int and evidence['after_ns'] >= evidence['before_ns']
        and isinstance(observed.instant, datetime) and observed.instant.utcoffset() is not None
        and type(observed.precision_us) is int and observed.precision_us >= 1
    )


def _elapsed_ns(start, end):
    delta = end - start
    return ((delta.days * 86400 + delta.seconds) * 1_000_000
            + delta.microseconds) * 1000


def valid_bounds(lower, upper):
    """Validate same-process/connection, monotonic order and elapsed evidence."""
    if not isinstance(lower, ClockSample) or not isinstance(upper, ClockSample):
        return False
    if not _valid_evidence(lower) or not _valid_evidence(upper):
        return False
    if lower.driver is not upper.driver:
        return False
    if any(lower.evidence[key] != upper.evidence[key]
           for key in ('database_identity', 'connection_id', 'process_id')):
        return False
    if upper.instant < lower.instant:
        return False
    if upper.evidence['before_ns'] < lower.evidence['after_ns']:
        return False
    db_elapsed = _elapsed_ns(lower.instant, upper.instant)
    monotonic_elapsed = upper.evidence['before_ns'] - lower.evidence['after_ns']
    return abs(db_elapsed - monotonic_elapsed) <= EVIDENCE_TOLERANCE_NS


def classify_bounds(*, lower, upper, precision_us, deadline, cancellation=False):
    """Classify an interval without guessing on either side of the deadline."""
    if upper is None:
        return 'REVIEW', 'ACK_MISSING'
    if upper < lower:
        return 'REVIEW', 'CLOCK_BACKWARDS'
    if type(precision_us) is not int or precision_us < 1:
        return 'REVIEW', 'PRECISION_INVALID'
    quantum = timedelta(microseconds=precision_us)
    if (upper + quantum <= deadline if cancellation else upper + quantum < deadline):
        return 'BEFORE', ''
    if (lower - quantum > deadline if cancellation else lower >= deadline):
        return 'AFTER', ''
    return 'REVIEW', 'CROSSED_CUTOFF'


def _classify_samples(lower, upper, deadline):
    if not valid_bounds(lower, upper):
        if lower.driver is not upper.driver or any(
                lower.evidence.get(k) != upper.evidence.get(k)
                for k in ('database_identity', 'connection_id', 'process_id')):
            return 'REVIEW', 'CONNECTION_CHANGED'
        if upper.instant < lower.instant:
            return 'REVIEW', 'CLOCK_BACKWARDS'
        return 'REVIEW', 'MONOTONIC_INCONSISTENT'
    return classify_bounds(
        lower=lower.instant, upper=upper.instant,
        precision_us=max(lower.precision_us, upper.precision_us), deadline=deadline,
    )


def new_decision_key(action):
    return f'{action.lower()[:12]}-{uuid.uuid4().hex}'


def validate_decision_key(value):
    if value is None or value == '':
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        raise ClockConflict('decision_key 无效')
    return value


def deadline_for(reservation):
    if reservation.status == reservation.Status.PENDING:
        return reservation.confirmation_deadline or reservation.end_time
    if reservation.status == reservation.Status.CONFIRMED:
        return reservation.payment_deadline or reservation.end_time
    return None


def _snapshot_value(value):
    # JSONField has no implicit datetime encoder.  Store an ISO string and
    # restore through the actual model field so compensation never writes a
    # string as a timestamp or drops the original timezone evidence.
    return value.isoformat() if isinstance(value, datetime) else value


def reservation_snapshot(reservation):
    names = (
        'status', 'confirmed_at', 'confirmation_deadline', 'confirmation_timeout_minutes',
        'confirmation_config_version', 'payment_deadline', 'payment_timeout_minutes',
        'payment_config_version', 'rejected_at', 'reject_reason', 'cancel_reason',
        'cancelled_at', 'expired_at', 'provider_account_id', 'provider_business_cycle',
        'provider_id', 'provider_name_snapshot',
    )
    return {name: _snapshot_value(getattr(reservation, name)) for name in names}


def seat_snapshot(seat):
    names = (
        'number', 'status', 'provider_account_id', 'provider_business_cycle',
        'provider_id', 'provider_name_snapshot', 'confirmation_deadline', 'confirmed_at',
    )
    return {name: _snapshot_value(getattr(seat, name)) for name in names}


def fingerprint_request(value):
    """Stable digest for idempotent decision content; never persist raw secrets."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), cls=DjangoJSONEncoder)
    return sha256(encoded.encode()).hexdigest()


def _fingerprint(reservation_id, action, seat_number, actor_account_id, deadline,
                 state_before, request_fingerprint=''):
    payload = [reservation_id, action, seat_number, actor_account_id,
               deadline.astimezone(dt_timezone.utc).isoformat(), state_before,
               request_fingerprint]
    return sha256(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def get_receipt(decision_key):
    key = validate_decision_key(decision_key)
    if not key:
        return None
    from .models import ReservationClockReceipt
    return ReservationClockReceipt.objects.filter(decision_key=key).first()


def assert_replay(receipt, *, reservation, action, seat_number=None,
                  actor_account=None, deadline=None, request_fingerprint=None):
    expected = _fingerprint(
        receipt.reservation_id if reservation is None else reservation.pk,
        action, seat_number, getattr(actor_account, 'pk', actor_account),
        deadline if deadline is not None else receipt.deadline,
        receipt.state_before, receipt.request_fingerprint,
    )
    if (receipt.action != action or receipt.seat_number != seat_number
            or receipt.actor_account_id != getattr(actor_account, 'pk', actor_account)
            or (deadline is not None and receipt.deadline != deadline)
            or (request_fingerprint is not None
                and receipt.request_fingerprint != request_fingerprint)
            or receipt.fingerprint != expected):
        raise ClockConflict('原 decision_key 对应的预约决策内容不同')
    if receipt.state == receipt.State.BEFORE:
        return
    if receipt.state == receipt.State.AFTER:
        raise ClockAfter()
    if receipt.state == receipt.State.PENDING:
        raise ClockUnavailable('原预约决策回执仍待取上界')
    raise ClockConflict('原预约决策回执需人工核对')


def assert_clock_clear(reservation, *, exclude_key=None, allow_review_expiration=False):
    from .models import ReservationClockReceipt
    unresolved = reservation.clock_receipts.filter(
        state__in=[ReservationClockReceipt.State.PENDING, ReservationClockReceipt.State.REVIEW],
    )
    if exclude_key:
        unresolved = unresolved.exclude(decision_key=exclude_key)
    if allow_review_expiration:
        # Expiry never resolves the original REVIEW.  It may only add a new,
        # independently sampled AFTER fact; an ACK-lost PENDING still blocks.
        if unresolved.filter(state=ReservationClockReceipt.State.PENDING).exists():
            raise ClockUnavailable('预约存在待取上界的时钟回执')
        return
    row = unresolved.first()
    if row is None:
        return
    if row.state == row.State.PENDING:
        raise ClockUnavailable('预约存在待取上界的时钟回执')
    raise ClockConflict('预约存在需人工核对的时钟回执')


@dataclass
class ClockDecision:
    receipt: object
    lower_sample: object = None
    replayed: bool = False

    @property
    def state(self):
        return self.receipt.state


def _receipt_values(*, reservation, action, actor_account, deadline, key, sample_value,
                    fingerprint, state, upper_sample=None, review_reason='',
                    state_before='', state_after='', before_snapshot=None,
                    request_fingerprint=''):
    evidence = sample_value.evidence
    upper = upper_sample.evidence if upper_sample is not None else None
    return {
        'reservation': reservation,
        'seat_number': None if action not in {'TEAM_CONFIRM', 'TEAM_REJECT', 'REPLACE', 'SEAT_TIMEOUT'}
        else before_snapshot.get('seat_number') if before_snapshot else None,
        'action': action,
        'actor_account': actor_account,
        'decision_key': key,
        'deadline': deadline,
        'lower_at': sample_value.instant,
        'upper_at': upper_sample.instant if upper_sample is not None else None,
        'precision_us': sample_value.precision_us,
        'database_identity': evidence['database_identity'],
        'connection_id': evidence['connection_id'],
        'process_id': evidence['process_id'],
        'monotonic_before_ns': evidence['before_ns'],
        'monotonic_after_ns': evidence['after_ns'],
        'upper_database_identity': upper['database_identity'] if upper else '',
        'upper_connection_id': upper['connection_id'] if upper else None,
        'upper_process_id': upper['process_id'] if upper else None,
        'upper_monotonic_before_ns': upper['before_ns'] if upper else None,
        'upper_monotonic_after_ns': upper['after_ns'] if upper else None,
        'state': state,
        'review_reason': review_reason,
        'state_before': state_before,
        'state_after': state_after,
        'before_snapshot': before_snapshot or {},
        'request_fingerprint': request_fingerprint,
        'fingerprint': fingerprint,
    }


def prepare_decision(*, reservation, action, deadline, actor_account=None,
                     seat_number=None, decision_key=None, state_before='',
                     before_snapshot=None, request_fingerprint=''):
    """Insert the lower-bound PENDING receipt inside the caller's transaction."""
    from .models import ReservationClockReceipt
    key = validate_decision_key(decision_key) or new_decision_key(action)
    assert_clock_clear(reservation, exclude_key=key)
    existing = get_receipt(key)
    if existing is not None:
        assert_replay(existing, reservation=reservation, action=action,
                      seat_number=seat_number, actor_account=actor_account,
                      deadline=deadline, request_fingerprint=request_fingerprint)
        return ClockDecision(existing, replayed=True)
    lower = sample()
    if not same_connection(lower):
        raise ClockUnavailable('预约写前数据库连接发生变化')
    fingerprint = _fingerprint(reservation.pk, action, seat_number,
                               getattr(actor_account, 'pk', None), deadline, state_before,
                               request_fingerprint)
    snapshot = dict(before_snapshot or {})
    if seat_number is not None:
        snapshot['seat_number'] = seat_number
    if lower.instant >= deadline:
        values = _receipt_values(
            reservation=reservation, action=action, actor_account=actor_account,
            deadline=deadline, key=key, sample_value=lower, fingerprint=fingerprint,
            state=ReservationClockReceipt.State.AFTER, upper_sample=lower,
            state_before=state_before, before_snapshot=snapshot,
            request_fingerprint=request_fingerprint,
        )
        with transaction.atomic():
            receipt = ReservationClockReceipt.objects.create(**values)
        return ClockDecision(receipt, lower)
    values = _receipt_values(
        reservation=reservation, action=action, actor_account=actor_account,
        deadline=deadline, key=key, sample_value=lower, fingerprint=fingerprint,
        state=ReservationClockReceipt.State.PENDING,
        state_before=state_before, before_snapshot=snapshot,
        request_fingerprint=request_fingerprint,
    )
    try:
        with transaction.atomic():
            receipt = ReservationClockReceipt.objects.create(**values)
    except IntegrityError:
        existing = get_receipt(key)
        if existing is None:
            raise
        assert_replay(existing, reservation=reservation, action=action,
                      seat_number=seat_number, actor_account=actor_account,
                      deadline=deadline, request_fingerprint=request_fingerprint)
        return ClockDecision(existing, replayed=True)
    return ClockDecision(receipt, lower)


def record_after(*, reservation, action, deadline, actor_account=None,
                 seat_number=None, decision_key=None, state_before='', observed=None,
                 request_fingerprint=''):
    """Persist direct AFTER evidence when the locked lower bound is already due."""
    from .models import ReservationClockReceipt
    observed = observed or sample()
    if not same_connection(observed):
        raise ClockUnavailable('预约到期判断期间数据库连接发生变化')
    key = validate_decision_key(decision_key) or new_decision_key(action)
    existing = get_receipt(key)
    if existing is not None:
        assert_replay(existing, reservation=reservation, action=action,
                      seat_number=seat_number, actor_account=actor_account,
                      deadline=deadline, request_fingerprint=request_fingerprint)
        return ClockDecision(existing, replayed=True)
    fingerprint = _fingerprint(reservation.pk, action, seat_number,
                               getattr(actor_account, 'pk', None), deadline, state_before,
                               request_fingerprint)
    values = _receipt_values(
        reservation=reservation, action=action, actor_account=actor_account,
        deadline=deadline, key=key, sample_value=observed, fingerprint=fingerprint,
        state=ReservationClockReceipt.State.AFTER, upper_sample=observed,
        state_before=state_before, before_snapshot={'seat_number': seat_number} if seat_number else {},
        request_fingerprint=request_fingerprint,
    )
    receipt = ReservationClockReceipt.objects.create(**values)
    return ClockDecision(receipt, observed)


def record_probe(*, reservation, action, deadline, actor_account=None,
                 seat_number=None, decision_key=None, state_before='',
                 request_fingerprint=''):
    """Two-sample read/pre-debit probe; caller already owns the row lock."""
    from .models import ReservationClockReceipt
    key = validate_decision_key(decision_key) or new_decision_key(action)
    assert_clock_clear(reservation, exclude_key=key)
    existing = get_receipt(key)
    if existing is not None:
        assert_replay(existing, reservation=reservation, action=action,
                      seat_number=seat_number, actor_account=actor_account,
                      deadline=deadline, request_fingerprint=request_fingerprint)
        return ClockDecision(existing, replayed=True)
    lower = sample()
    if not same_connection(lower):
        raise ClockUnavailable('预约报价或扣款前数据库连接发生变化')
    if lower.instant >= deadline:
        return record_after(reservation=reservation, action=action, deadline=deadline,
                            actor_account=actor_account, seat_number=seat_number,
                            decision_key=key, state_before=state_before, observed=lower,
                            request_fingerprint=request_fingerprint)
    upper = sample()
    if not same_connection(upper):
        state, reason = 'REVIEW', 'CONNECTION_CHANGED'
    else:
        state, reason = _classify_samples(lower, upper, deadline)
    fingerprint = _fingerprint(reservation.pk, action, seat_number,
                               getattr(actor_account, 'pk', None), deadline, state_before,
                               request_fingerprint)
    values = _receipt_values(
        reservation=reservation, action=action, actor_account=actor_account,
        deadline=deadline, key=key, sample_value=lower, fingerprint=fingerprint,
        state=state, upper_sample=upper, review_reason=reason,
        state_before=state_before, state_after=state_before,
        before_snapshot={'seat_number': seat_number} if seat_number else {},
        request_fingerprint=request_fingerprint,
    )
    created = False
    with transaction.atomic():
        try:
            receipt = ReservationClockReceipt.objects.create(**values)
            created = True
        except IntegrityError:
            receipt = get_receipt(key)
    if receipt is None:
        raise ClockUnavailable('预约时钟回执写入结果未知')
    if not created:
        assert_replay(receipt, reservation=reservation, action=action,
                      seat_number=seat_number, actor_account=actor_account,
                      deadline=deadline, request_fingerprint=request_fingerprint)
    return ClockDecision(receipt, lower)


def require_before(decision):
    if decision.state == 'BEFORE':
        return decision.receipt.upper_at or decision.receipt.lower_at
    if decision.state == 'AFTER':
        raise ClockAfter()
    if decision.state == 'PENDING':
        raise ClockUnavailable('预约决策仍待取上界')
    raise ClockConflict('预约时钟回执需人工核对')


def _restore_value(instance, name, value):
    field = type(instance)._meta.get_field(name)
    if isinstance(value, str) and field.get_internal_type() == 'DateTimeField':
        return field.to_python(value)
    return value


def _restore_reservation(receipt, reservation, observed_at, outcome):
    snapshot = receipt.before_snapshot.get('reservation') or {}
    if not snapshot and receipt.action != 'CREATE' and outcome != 'AFTER':
        return
    names = (
        'status', 'confirmed_at', 'confirmation_deadline', 'confirmation_timeout_minutes',
        'confirmation_config_version', 'payment_deadline', 'payment_timeout_minutes',
        'payment_config_version', 'rejected_at', 'reject_reason', 'cancel_reason',
        'cancelled_at', 'expired_at', 'provider_account_id', 'provider_business_cycle',
        'provider_id', 'provider_name_snapshot',
    )
    for name in names:
        if name in snapshot:
            setattr(reservation, name, _restore_value(reservation, name, snapshot[name]))
    fields = [name for name in names if name in snapshot]
    if outcome == 'AFTER':
        reservation.status = reservation.Status.EXPIRED
        reservation.expired_at = observed_at
        reservation.confirmed_at = None
        reservation.payment_deadline = None
        reservation.payment_timeout_minutes = None
        reservation.payment_config_version = None
        fields.extend(['status', 'expired_at', 'confirmed_at', 'payment_deadline',
                       'payment_timeout_minutes', 'payment_config_version'])
    elif receipt.action == 'CREATE' and outcome == 'REVIEW':
        # Creation cannot be replayed as a success while its commit interval is
        # unknown. Cancellation frees the slot without guessing expiry.
        reservation.status = reservation.Status.CANCELLED
        reservation.cancelled_at = observed_at
        reservation.cancel_reason = '预约创建时钟回执待核对，已撤销本次创建'
        fields.extend(['status', 'cancelled_at', 'cancel_reason'])
    fields.append('updated_at')
    reservation.save(update_fields=list(dict.fromkeys(fields)))
    type(reservation).objects.filter(pk=reservation.pk).update(updated_at=observed_at)


def compensate_receipt(receipt, *, outcome, observed_at=None):
    """Idempotently undo only the uncertain action, preserving all evidence."""
    from .models import Reservation, ReservationSeat, ReservationStatusLog
    if receipt.action in {'QUOTE', 'CONVERT_CHECK'}:
        return  # Read/pre-debit probes have no business write to compensate.
    observed_at = observed_at or receipt.upper_at or receipt.lower_at
    with transaction.atomic():
        reservation = Reservation.objects.select_for_update().get(pk=receipt.reservation_id)
        if receipt.state_after and reservation.status != receipt.state_after:
            return
        _restore_reservation(receipt, reservation, observed_at, outcome)
        seat_data = receipt.before_snapshot.get('seat') or {}
        if receipt.seat_number is not None:
            seat = ReservationSeat.objects.select_for_update().filter(
                reservation=reservation, number=receipt.seat_number,
            ).first()
            if seat is not None:
                for name, value in seat_data.items():
                    setattr(seat, name, _restore_value(seat, name, value))
                seat.updated_at = observed_at
                seat.save()
                ReservationSeat.objects.filter(pk=seat.pk).update(updated_at=observed_at)
        if outcome == 'AFTER':
            log = ReservationStatusLog.objects.create(
                reservation=reservation, action=ReservationStatusLog.Action.EXPIRE,
                from_status=receipt.state_before, to_status=Reservation.Status.EXPIRED,
                operator_account=receipt.actor_account,
                reason='预约决策上界越过截止，按库时钟前向过期',
            )
        elif receipt.action == 'CREATE':
            log = ReservationStatusLog.objects.create(
                reservation=reservation, action=ReservationStatusLog.Action.CANCEL,
                from_status=receipt.state_before, to_status=Reservation.Status.CANCELLED,
                operator_account=receipt.actor_account,
                reason='预约创建时钟回执待核对，已撤销本次创建',
            )
        else:
            log = None
        if log is not None:
            ReservationStatusLog.objects.filter(pk=log.pk).update(created_at=observed_at)


def finalize_decision(decision, *, state_after=None, compensate=True, raise_on_review=True):
    """Sample and persist the upper bound after the caller's write commits."""
    from .models import ReservationClockReceipt
    receipt = decision.receipt
    if receipt.state != receipt.State.PENDING:
        if receipt.state == receipt.State.BEFORE:
            return receipt
        if receipt.state == receipt.State.AFTER:
            if compensate:
                compensate_receipt(receipt, outcome='AFTER',
                                   observed_at=receipt.upper_at or receipt.lower_at)
            raise ClockAfter()
        raise ClockConflict('预约时钟回执需人工核对')
    upper = sample()
    if not same_connection(upper) or decision.lower_sample is None:
        state, reason = 'REVIEW', 'CONNECTION_CHANGED'
    else:
        state, reason = _classify_samples(decision.lower_sample, upper, receipt.deadline)
    from .models import Reservation
    if state_after is None:
        state_after = Reservation.objects.filter(pk=receipt.reservation_id).values_list(
            'status', flat=True).first() or receipt.state_before
    final = ReservationClockReceipt.finalize(
        pk=receipt.pk, upper_at=upper.instant, upper_sample=upper,
        state=state, review_reason=reason, state_after=state_after,
    )
    receipt = final or ReservationClockReceipt.objects.filter(pk=receipt.pk).first()
    if receipt is None:
        raise ClockUnavailable('预约时钟回执 finalize 结果未知')
    decision.receipt = receipt
    # A recovery worker may have finalized the same row first.  Always follow
    # the persisted terminal state, not this thread's now-stale local verdict.
    state = receipt.state
    if state == 'BEFORE':
        return receipt
    if compensate:
        compensate_receipt(receipt, outcome=state, observed_at=receipt.upper_at or upper.instant)
    if raise_on_review:
        if state == 'AFTER':
            raise ClockAfter()
        raise ClockConflict('预约提交跨过截止或证据无效，已登记 REVIEW')
    return receipt


def recover_receipts(*, reservation=None):
    """Recover ACK-lost receipts without guessing which side of the deadline."""
    import logging
    from .models import ReservationClockReceipt
    rows = ReservationClockReceipt.objects.filter(
        state__in=[ReservationClockReceipt.State.PENDING, ReservationClockReceipt.State.REVIEW],
    )
    if reservation is not None:
        rows = rows.filter(reservation=reservation)
    recovered = 0
    for receipt in list(rows.order_by('id')):
        try:
            if receipt.state == receipt.State.PENDING:
                lower = ClockSample(receipt.lower_at, store_connection.connection, {
                    'database_identity': receipt.database_identity,
                    'connection_id': receipt.connection_id,
                    'process_id': receipt.process_id,
                    'before_ns': receipt.monotonic_before_ns,
                    'after_ns': receipt.monotonic_after_ns,
                }, receipt.precision_us)
                finalize_decision(ClockDecision(receipt, lower), raise_on_review=False)
            else:
                if receipt.action in {'QUOTE', 'CONVERT_CHECK'}:
                    continue
                compensate_receipt(receipt, outcome=receipt.state,
                                   observed_at=receipt.upper_at or receipt.lower_at)
            recovered += 1
        except ClockUnavailable:
            continue
        except Exception:
            logging.getLogger(__name__).exception(
                '预约时钟回执恢复失败，保留原回执等待重试 receipt_id=%s', receipt.pk)
    return recovered
