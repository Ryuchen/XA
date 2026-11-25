"""Real MySQL-only proof for the reservation tenant-database clock.

The default SQLite suite skips this module's test.  It never treats the explicit
SQLite test seam as production evidence and never creates or clears a database.
"""
from unittest import skipUnless
from unittest.mock import patch

from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from orders.reservation_clock import sample, same_connection, valid_bounds
from stores.db import connection as store_connection


@skipUnless(store_connection.vendor == 'mysql', 'Requires the registered isolated MySQL store')
@override_settings(RESERVATION_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True)
class ReservationClockMySQLTest(TransactionTestCase):
    def test_samples_real_server_time_uuid_and_connection_identity(self):
        # Even if the SQLite-only flag is accidentally left enabled, MySQL must
        # execute UTC_TIMESTAMP(6), not use the patchable application clock.
        with patch('django.utils.timezone.now', side_effect=AssertionError('application clock used')):
            with CaptureQueriesContext(store_connection) as queries:
                lower = sample()
                upper = sample()
        self.assertTrue(any(
            'SELECT UTC_TIMESTAMP(6), @@server_uuid, CONNECTION_ID()' in item['sql']
            for item in queries.captured_queries
        ))
        self.assertTrue(same_connection(lower))
        self.assertTrue(same_connection(upper))
        self.assertTrue(valid_bounds(lower, upper))
        self.assertEqual(lower.evidence['database_identity'], upper.evidence['database_identity'])
        self.assertEqual(lower.evidence['connection_id'], upper.evidence['connection_id'])
        self.assertGreater(len(lower.evidence['database_identity']), 0)
        self.assertGreater(lower.evidence['connection_id'], 0)
        self.assertGreaterEqual(upper.instant, lower.instant)
