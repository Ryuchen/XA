"""Trusted store-database clocks and immutable settlement receipts."""
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from orders.tests.factories import make_order, make_user
from wallet.models import SettlementClockEvidence
from wallet.settlement_clocks import (
    ClockConflict,
    ClockUnavailable,
    record,
    require_clock,
    sample,
    trusted_test_clock,
    verify,
)


class _Cursor:
    def __init__(self, row=None, error=None):
        self.row = row
        self.error = error
        self.sql = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql):
        self.sql = sql
        if self.error:
            raise self.error

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, vendor, *, row=None, error=None):
        self.vendor = vendor
        self.alias = 'default'
        self.connection = object()
        self._cursor = _Cursor(row, error)

    def ensure_connection(self):
        return None

    def cursor(self):
        return self._cursor


class SettlementClockSourceTest(SimpleTestCase):
    @override_settings(SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False)
    def test_sqlite_is_rejected_by_default_without_application_clock_fallback(self):
        fake = _Connection('sqlite')
        with (patch('wallet.settlement_clocks.connection', fake),
              patch('wallet.settlement_clocks.timezone.now', side_effect=AssertionError('application clock fallback'))):
            with self.assertRaises(ClockUnavailable):
                sample()

    @override_settings(SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True)
    def test_raw_datetime_is_not_a_trusted_clock_even_in_the_sqlite_test_seam(self):
        fake = _Connection('sqlite')
        with patch('wallet.settlement_clocks.connection', fake):
            with self.assertRaises(ClockUnavailable):
                require_clock(timezone.now())

    def test_mysql_timestamp_identity_and_connection_are_the_clock_evidence(self):
        instant = datetime.now(dt_timezone.utc).replace(tzinfo=None, microsecond=123456)
        identity = '01234567-89ab-cdef-8123-456789abcdef'
        fake = _Connection('mysql', row=(instant, identity, 42))
        with (patch('wallet.settlement_clocks.connection', fake),
              patch('wallet.settlement_clocks.timezone.now', side_effect=AssertionError('application clock fallback'))):
            observed = sample()
        self.assertEqual(observed.at, instant.replace(tzinfo=dt_timezone.utc))
        self.assertEqual(fake._cursor.sql, 'SELECT UTC_TIMESTAMP(6), @@server_uuid, CONNECTION_ID()')
        self.assertEqual(observed.evidence['database_identity'], identity)
        self.assertEqual(observed.evidence['connection_id'], 42)
        self.assertGreaterEqual(observed.evidence['after_ns'], observed.evidence['before_ns'])

    def test_mysql_failure_never_falls_back_to_application_or_sqlite_time(self):
        fake = _Connection('mysql', error=RuntimeError('store DB disconnected'))
        with (patch('wallet.settlement_clocks.connection', fake),
              patch('wallet.settlement_clocks.timezone.now', side_effect=AssertionError('application clock fallback')),
              patch('wallet.settlement_clocks.trusted_test_clock', side_effect=AssertionError('SQLite fallback'))):
            with self.assertRaises(ClockUnavailable):
                sample()


@override_settings(SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True)
class SettlementClockReceiptTest(TestCase):
    def setUp(self):
        self.order = make_order(make_user())
        self.clock = trusted_test_clock(timezone.now())
        self.subject = ('SUBMISSION', 1, 'SUBMIT')

    def test_exact_replay_returns_original_receipt_and_model_rows_are_append_only(self):
        row = record(self.order, *self.subject, self.clock)
        replay = record(self.order, *self.subject, self.clock)
        self.assertEqual(replay.pk, row.pk)
        self.assertEqual(SettlementClockEvidence.objects.count(), 1)

        row.clock_evidence = {}
        with self.assertRaisesMessage(ValueError, '不可修改'):
            row.save(update_fields=['clock_evidence'])
        with self.assertRaisesMessage(ValueError, '不可删除'):
            row.delete()
        row.refresh_from_db()
        self.assertEqual(row.observed_at, self.clock.at)

        later = trusted_test_clock(self.clock.at + timedelta(seconds=1))
        with self.assertRaises(ClockConflict):
            record(self.order, *self.subject, later)
        self.assertEqual(SettlementClockEvidence.objects.count(), 1)

    def test_missing_altered_or_cross_database_evidence_fails_closed(self):
        row = record(self.order, *self.subject, self.clock)
        self.assertEqual(verify(self.order, *self.subject, row.observed_at, self.clock), self.clock)
        with self.assertRaises(ClockConflict):
            verify(self.order, *self.subject, row.observed_at + timedelta(seconds=1), self.clock)

        SettlementClockEvidence.objects.filter(pk=row.pk).update(
            clock_evidence={**row.clock_evidence, 'database_identity': 'another-store-database'})
        with self.assertRaises(ClockConflict):
            verify(self.order, *self.subject, row.observed_at, self.clock)

        SettlementClockEvidence.objects.filter(pk=row.pk).update(clock_evidence={})
        with self.assertRaises(ClockConflict):
            verify(self.order, *self.subject, row.observed_at, self.clock)

    def test_business_record_rejects_a_raw_datetime_without_writing_evidence(self):
        with self.assertRaises(ClockUnavailable):
            record(self.order, *self.subject, timezone.now())
        self.assertFalse(SettlementClockEvidence.objects.exists())
