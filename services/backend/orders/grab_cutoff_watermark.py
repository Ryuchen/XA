"""Test-only cutoff sealing; it does not establish a production clock contract.

Receipt registration and this seal take the same order lock. The immutable
OPEN receipt identities are frozen; acknowledgement state may still change.
The test clock assumption is deliberately not a production authorization.
"""
from datetime import timedelta
import hashlib
import json

from django.conf import settings
from stores.db import connection, transaction

from .models import Order, OrderGrabCutoffWatermark, OrderGrabReceiptProbe


def enabled():
    from .grab_clock_contract import production_enabled
    return bool((getattr(settings, 'GRAB_CUTOFF_WATERMARK_TESTING', False)
                 and getattr(settings, 'GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY', False)) or production_enabled())


def _server_id():
    if connection.vendor == 'sqlite':
        return 'sqlite-test:' + connection.alias
    if connection.vendor != 'mysql':
        raise ValueError('门店数据库不支持截止水位')
    with connection.cursor() as cursor:
        cursor.execute('SELECT @@server_uuid')
        value = cursor.fetchone()[0]
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError('门店数据库身份无法核验')
    return value


def _facts(rows):
    facts = []
    for row in rows:
        if row.round_phase != 'OPEN':
            continue
        facts.append([row.pk, row.actor_id, row.kind, row.request_key, row.payload, row.fingerprint,
            row.actor_business_cycle, row.actor_cycle_guarded, row.lower_at.isoformat(),
            row.cutoff_at.isoformat() if row.cutoff_at else None, row.precision_us])
    encoded = json.dumps(facts, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('ascii')
    return len(facts), hashlib.sha256(encoded).hexdigest()


def verify(round_, rows, *, observed_at, precision_us):
    """Caller holds the order lock; never infer trust from the test override."""
    mark = OrderGrabCutoffWatermark.objects.select_for_update().filter(round=round_).first()
    if not mark:
        return False
    from .grab_clock_contract import production_enabled, round_identity_valid
    if production_enabled() and not round_identity_valid(round_, identity=mark.server_id):
        return False
    if production_enabled():
        from .grab_round_opening import cutoff_bounds
        cutoff = cutoff_bounds(round_, 'OPEN')[1]
    else:
        cutoff = round_.deadline
    count, digest = _facts(rows)
    return (mark.cutoff_at == cutoff and mark.precision_us == precision_us
            and mark.clock_vendor == connection.vendor and mark.server_id == _server_id()
            and mark.observed_at - timedelta(microseconds=mark.precision_us) > mark.cutoff_at
            and observed_at + timedelta(microseconds=precision_us) >= mark.observed_at
            and mark.receipt_count == count and mark.receipt_digest == digest)


def seal(order_id, *, team, expected_round_id=None):
    """Freeze OPEN receipt identities after cutoff in an independent transaction."""
    if not enabled():
        return 'DISABLED'
    if transaction.get_connection().in_atomic_block:
        raise RuntimeError('截止水位必须使用独立最外层事务')
    from .grab_receipt_probe import _db_clock
    from .grab_round_facts import GrabRoundReviewRequired, latest_round_state, locked_latest_round_id
    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if not order or order.status != Order.Status.PENDING:
            return 'NO_OPEN_ROUND'
        if expected_round_id is not None and locked_latest_round_id(order.pk) != expected_round_id:
            return 'STALE_ROUND'
        seats = 2 if team else 1
        if order.required_provider_count != seats:
            return 'REVIEW'
        try:
            state = latest_round_state(order.pk, seats=seats, lock=True)
        except GrabRoundReviewRequired:
            return 'REVIEW'
        if not state or state.status not in ('OPEN', 'PARKED'):
            return 'NO_OPEN_ROUND'
        round_ = state.round
        from .grab_clock_contract import production_enabled, round_identity_valid
        if production_enabled() and not round_identity_valid(round_):
            return 'REVIEW'
        rows = list(OrderGrabReceiptProbe.objects.select_for_update().filter(round=round_).order_by('pk'))
        now, precision, _ = _db_clock()
        mark = OrderGrabCutoffWatermark.objects.select_for_update().filter(round=round_).first()
        if mark:
            return 'SEALED' if verify(round_, rows, observed_at=now, precision_us=precision) else 'REVIEW'
        if state.status != 'OPEN':
            return 'REVIEW'
        if production_enabled():
            from .grab_round_opening import cutoff_bounds
            cutoff = cutoff_bounds(round_, 'OPEN')[1]
        else:
            cutoff = round_.deadline
        if now - timedelta(microseconds=precision) <= cutoff:
            return 'EARLY'
        count, digest = _facts(rows)
        OrderGrabCutoffWatermark.objects.create(round=round_, cutoff_at=cutoff,
            observed_at=now, precision_us=precision, clock_vendor=connection.vendor, server_id=_server_id(),
            receipt_count=count, receipt_digest=digest)
        return 'SEALED'
