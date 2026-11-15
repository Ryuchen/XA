from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.db import DatabaseError, connection, connections
from django.test import SimpleTestCase, TransactionTestCase

from orders import grab_clock_diagnostic as diagnostic
from orders.models import GrabClockDiagnosticObservation, GrabClockDiagnosticState
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.tests_hosts import xinan_store
from wallet.test_deposit_lifecycle_mysql import require_isolated


class GrabClockIntervalTest(SimpleTestCase):
    def setUp(self):
        self.base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.old = SimpleNamespace(process_epoch='old', process_id=12, database_uuid='db-a', connection_id=8,
            server_uptime_seconds=20, db_time=self.base, monotonic_before_ns=1_000_000_000, monotonic_after_ns=1_001_000_000)
        self.good = diagnostic.Sample('old', 12, 'db-a', 8, 21, self.base + timedelta(seconds=1),
                                     2_000_000_000, 2_001_000_000)

    def test_only_detectable_discontinuities_are_reported(self):
        self.assertEqual(diagnostic._anomaly(self.old, self.good), '')
        for sample, expected in [
            (replace(self.good, process_epoch='new'), 'PROCESS_EPOCH_CHANGED'),
            (replace(self.good, database_uuid='db-b'), 'DATABASE_ID_CHANGED'),
            (replace(self.good, connection_id=9), 'CONNECTION_CHANGED'),
            (replace(self.good, server_uptime_seconds=1), 'SERVER_EPOCH_CHANGED'),
            (replace(self.good, before_ns=1, after_ns=2), 'MONOTONIC_REGRESSION'),
            (replace(self.good, db_time=self.base - timedelta(seconds=1)), 'CLOCK_JUMP'),
            (replace(self.good, db_time=self.base + timedelta(seconds=10)), 'CLOCK_JUMP'),
            (replace(self.good, after_ns=self.good.before_ns + diagnostic.MAX_SAMPLE_NS + 1), 'SAMPLE_TOO_SLOW'),
        ]:
            with self.subTest(expected=expected):
                self.assertEqual(diagnostic._anomaly(self.old, sample), expected)


@skipUnless(connection.vendor == 'mysql' and 'store_b' in connections, 'Requires two isolated MySQL stores')
class GrabClockDiagnosticMySQLTest(TransactionTestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        require_isolated(self)
        self.first, self.second = resolve_store(), xinan_store()

    def test_actual_clock_observations_never_grant_authorization(self):
        with store_context(self.second):
            first, second = diagnostic.observe(), diagnostic.observe()
            self.assertEqual((first.status, second.status), ('BASELINE', 'OBSERVING'))
            self.assertFalse(first.production_authorized)
            self.assertFalse(second.production_authorized)
            state = GrabClockDiagnosticState.objects.get(pk=1)
            from stores.db import connection as store_connection
            with store_connection.cursor() as cursor:
                cursor.execute('SELECT @@server_uuid, CONNECTION_ID()')
                expected_uuid, expected_connection = cursor.fetchone()
            self.assertEqual((state.database_uuid, state.connection_id), (expected_uuid, expected_connection))
            self.assertGreaterEqual(state.server_uptime_seconds, 0)
            self.assertEqual(GrabClockDiagnosticObservation.objects.count(), 2)

    def test_new_process_epoch_freezes_and_does_not_auto_recover(self):
        with store_context(self.second):
            self.assertEqual(diagnostic.observe().status, 'BASELINE')
            run = os.environ['XA_FOLLOWUP_MYSQL_RUN_ID']
            child = '''
import django
from django.conf import settings
from os import environ
run = environ['XA_FOLLOWUP_MYSQL_RUN_ID']
settings.DATABASES['default']['NAME'] = 'test_xa_test_followup_' + run + '_a'
settings.DATABASES['store_b']['NAME'] = 'test_xa_test_followup_' + run + '_b'
django.setup()
from stores.context import store_context
from stores.models import Store
from orders.grab_clock_diagnostic import observe
with store_context(Store.objects.get(slug='xinan')):
    result = observe()
    print(result.status + '|' + result.reason)
'''
            self.assertEqual(connections['store_b'].settings_dict['NAME'], f'test_xa_test_followup_{run}_b')
            result = subprocess.run([sys.executable, '-c', child], capture_output=True, text=True, timeout=40)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('FROZEN|PROCESS_EPOCH_CHANGED', result.stdout)
            self.assertEqual(diagnostic.observe().status, 'FROZEN')
            self.assertEqual(GrabClockDiagnosticObservation.objects.count(), 2)

    def test_real_connection_replacement_freezes(self):
        from stores.db import connection as store_connection
        with store_context(self.second):
            self.assertEqual(diagnostic.observe().status, 'BASELINE')
            store_connection.close()
            result = diagnostic.observe()
            self.assertEqual((result.status, result.reason), ('FROZEN', 'CONNECTION_CHANGED'))

    def test_database_identity_change_and_clock_jump_freeze(self):
        for store, change, reason in [
            (self.first, {'database_uuid':'simulated-other-server'}, 'DATABASE_ID_CHANGED'),
            (self.second, {'seconds':30}, 'CLOCK_JUMP'),
        ]:
            with self.subTest(reason=reason), store_context(store):
                self.assertEqual(diagnostic.observe().status, 'BASELINE')
                actual = diagnostic._capture
                def altered(epoch, pid):
                    sample = actual(epoch, pid)
                    if 'seconds' in change:
                        return replace(sample, db_time=sample.db_time + timedelta(seconds=change['seconds']))
                    return replace(sample, **change)
                with patch('orders.grab_clock_diagnostic._capture', side_effect=altered):
                    result = diagnostic.observe()
                self.assertEqual((result.status, result.reason), ('FROZEN', reason))

    def test_unverifiable_sample_is_persistently_frozen_without_fabrication(self):
        with store_context(self.second):
            self.assertEqual(diagnostic.observe().status, 'BASELINE')
            with patch('orders.grab_clock_diagnostic._capture', side_effect=DatabaseError('test outage')):
                result = diagnostic.observe()
            self.assertEqual((result.status, result.reason, result.persisted),
                             ('FROZEN', 'OBSERVATION_UNVERIFIABLE', True))
            self.assertEqual(GrabClockDiagnosticObservation.objects.count(), 1)
            self.assertEqual(diagnostic.observe().status, 'FROZEN')

    def test_unavailable_write_latches_failure_until_persistence_recovers(self):
        with store_context(self.second):
            self.assertEqual(diagnostic.observe().status, 'BASELINE')
            with patch('orders.grab_clock_diagnostic._capture', side_effect=DatabaseError('test outage')), \
                 patch('orders.grab_clock_diagnostic._persist_failure', side_effect=DatabaseError('test outage')):
                result = diagnostic.observe()
            self.assertEqual((result.status, result.persisted), ('FROZEN', False))
            recovered = diagnostic.observe()
            self.assertEqual((recovered.status, recovered.reason, recovered.persisted),
                             ('FROZEN', 'OBSERVATION_UNVERIFIABLE', True))
            self.assertEqual(GrabClockDiagnosticObservation.objects.count(), 1)

    def test_nested_transaction_does_not_claim_durable_observation(self):
        with store_context(self.second):
            with transaction.atomic():
                result = diagnostic.observe()
                self.assertEqual((result.status, result.reason, result.persisted),
                                 ('FROZEN', 'NON_DURABLE_CONTEXT', False))
                self.assertFalse(GrabClockDiagnosticState.objects.exists())
            stored = diagnostic.observe()
            self.assertEqual((stored.status, stored.reason, stored.persisted),
                             ('FROZEN', 'NON_DURABLE_CONTEXT', True))

    def test_observed_server_uptime_reset_freezes(self):
        with store_context(self.second):
            self.assertEqual(diagnostic.observe().status, 'BASELINE')
            state = GrabClockDiagnosticState.objects.get(pk=1)
            self.assertGreater(state.server_uptime_seconds, 0)
            actual = diagnostic._capture
            def restarted(epoch, pid):
                return replace(actual(epoch, pid), server_uptime_seconds=0)
            with patch('orders.grab_clock_diagnostic._capture', side_effect=restarted):
                result = diagnostic.observe()
            self.assertEqual((result.status, result.reason), ('FROZEN', 'SERVER_EPOCH_CHANGED'))

    def test_store_freeze_is_isolated(self):
        with store_context(self.first):
            self.assertEqual(diagnostic.observe().status, 'BASELINE')
        with store_context(self.second):
            self.assertEqual(diagnostic.observe().status, 'BASELINE')
            _, pid = diagnostic._process_identity()
            with patch('orders.grab_clock_diagnostic._process_identity', return_value=('e' * 32, pid)):
                self.assertEqual(diagnostic.observe().status, 'FROZEN')
        with store_context(self.first):
            self.assertEqual(diagnostic.observe().status, 'OBSERVING')
