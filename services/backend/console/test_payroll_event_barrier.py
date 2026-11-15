"""MySQL tests for preseeded per-store payroll event locks."""
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from unittest.mock import Mock
from threading import Event
from types import SimpleNamespace
from unittest import skipUnless

from django.db import close_old_connections, connection, connections, router
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from common.mysql_test_support import require_disposable_mysql
from console.payroll_event_barrier import (PayrollEventBarrierError, lock_payment_refund_events,
                                            lock_payroll_events)
from console.payroll_models import PayrollEventBarrier
from stores.context import store_context
from stores.db import transaction


class PayrollEventBarrierGuardTest(SimpleTestCase):
    @override_settings(ISOLATED_CONTROL_PLANE=True, DATABASE_ROUTERS=['stores.router.StoreRouter'])
    def test_control_database_skips_table_and_data_migration(self):
        migration = import_module('console.migrations.0030_payrolleventbarrier').Migration
        self.assertFalse(router.allow_migrate('default', 'console', model_name='payrolleventbarrier'))
        self.assertTrue(router.allow_migrate('store_b', 'console', model_name='payrolleventbarrier'))
        state = Mock()
        editor = SimpleNamespace(connection=SimpleNamespace(alias='default'))
        migration.operations[-1].database_forwards('console', editor, state, state)
        state.apps.get_model.assert_not_called()

    @override_settings(ISOLATED_CONTROL_PLANE=True)
    def test_control_database_cannot_be_used_as_store(self):
        with store_context(SimpleNamespace(database_alias='default')), self.assertRaises(PayrollEventBarrierError):
            lock_payroll_events()

    def test_requires_store_context(self):
        with store_context(None), self.assertRaises(PayrollEventBarrierError):
            lock_payroll_events()


@skipUnless(connection.vendor == 'mysql' and 'store_b' in connections, 'Requires two disposable MySQL stores')
class PayrollEventBarrierMySQLTest(TransactionTestCase):
    databases = '__all__'

    def setUp(self):
        require_disposable_mysql(connection)
        other = connections['store_b'].settings_dict
        if (connections['store_b'].vendor != 'mysql' or not other['NAME'].startswith('test_xa_test_')
                or other['NAME'] == connection.settings_dict['NAME']
                or not other['USER'].startswith('xa_test') or other['HOST'] not in ('127.0.0.1', 'localhost', 'db')):
            self.fail('Second store must be an independent disposable MySQL test schema')
        self.a = SimpleNamespace(database_alias='default')
        self.b = SimpleNamespace(database_alias='store_b')
        for store in (self.a, self.b):
            with store_context(store):
                self.assertEqual(list(PayrollEventBarrier.objects.values_list('pk', flat=True)), [1])

    def hold(self, store, lock, acquired, release):
        close_old_connections()
        try:
            with store_context(store), transaction.atomic():
                lock()
                acquired.set()
                if not release.wait(15):
                    raise TimeoutError('Timed out waiting to release the barrier')
        finally:
            close_old_connections()

    def _shared_writers_coexist_exclusive_waits_and_other_store_proceeds(self):
        writer_one, writer_two, exclusive, other_store = (Event() for _ in range(4))
        release_writers, release_exclusive, release_other = (Event() for _ in range(3))
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                first = pool.submit(self.hold, self.a, lock_payment_refund_events, writer_one, release_writers)
                self.assertTrue(writer_one.wait(10))
                second = pool.submit(self.hold, self.a, lock_payment_refund_events, writer_two, release_writers)
                self.assertTrue(writer_two.wait(10), 'Two payment/refund writers must share the barrier')
                payroll = pool.submit(self.hold, self.a, lock_payroll_events, exclusive, release_exclusive)
                self.assertFalse(exclusive.wait(0.5), 'Payroll must wait for both writers')
                unrelated = pool.submit(self.hold, self.b, lock_payroll_events, other_store, release_other)
                self.assertTrue(other_store.wait(10), 'Another store must not wait on this store')
                release_writers.set()
                first.result(timeout=10); second.result(timeout=10)
                self.assertTrue(exclusive.wait(10), 'Payroll must proceed after writer commits')
                release_exclusive.set(); release_other.set()
                payroll.result(timeout=10); unrelated.result(timeout=10)
        finally:
            release_writers.set(); release_exclusive.set(); release_other.set()

    def _exclusive_blocks_writer_and_failure_never_creates_a_row(self):
        payroll_acquired, writer_acquired, release_payroll, release_writer = (Event() for _ in range(4))
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                payroll = pool.submit(self.hold, self.a, lock_payroll_events, payroll_acquired, release_payroll)
                self.assertTrue(payroll_acquired.wait(10))
                writer = pool.submit(self.hold, self.a, lock_payment_refund_events, writer_acquired, release_writer)
                self.assertFalse(writer_acquired.wait(0.5), 'Writer must wait for payroll')
                release_payroll.set(); payroll.result(timeout=10)
                self.assertTrue(writer_acquired.wait(10))
                release_writer.set(); writer.result(timeout=10)
        finally:
            release_payroll.set(); release_writer.set()
        with store_context(self.a):
            with self.assertRaises(PayrollEventBarrierError):
                lock_payroll_events()
            PayrollEventBarrier.objects.filter(pk=1).delete()
            with transaction.atomic():
                with self.assertRaises(PayrollEventBarrierError):
                    lock_payment_refund_events()
            self.assertFalse(PayrollEventBarrier.objects.exists())
        with self.assertRaises(PayrollEventBarrierError):
            with transaction.atomic():
                lock_payroll_events()

    def _control_database_migration_emits_no_sql(self):
        migration = import_module('console.migrations.0030_payrolleventbarrier').Migration
        previous = MigrationExecutor(connection).loader.project_state([('console', '0029_payrollrefunddeduction_addon_refund_and_more')])
        after_create = previous.clone()
        create, seed = migration.operations
        create.state_forwards('console', after_create)
        statements = []

        def record(execute, sql, params, many, context):
            statements.append(sql)
            return execute(sql, params, many, context)

        with override_settings(ISOLATED_CONTROL_PLANE=True), connection.schema_editor() as editor:
            with connection.execute_wrapper(record):
                create.database_forwards('console', editor, previous, after_create)
                seed.database_forwards('console', editor, after_create, after_create)
        self.assertEqual(statements, [], 'Control database must not create or seed the tenant barrier')

    def test_barrier_isolation_and_fail_closed(self):
        self._control_database_migration_emits_no_sql()
        self._shared_writers_coexist_exclusive_waits_and_other_store_proceeds()
        self._exclusive_blocks_writer_and_failure_never_creates_a_row()
