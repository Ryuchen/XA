from types import SimpleNamespace
from unittest import skipIf

from django.conf import settings
from django.db import DatabaseError, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from orders.tests.migration_isolation import IsolatedMigrationDatabaseMixin
from stores.platform_pass_refunds import _store_summary


@skipIf(bool(settings.MIGRATION_MODULES), 'Requires real migrations')
class PassFinanceMigrationTests(IsolatedMigrationDatabaseMixin, TransactionTestCase):
    def test_unmigrated_store_is_unavailable_not_an_empty_summary(self):
        old = [('users', '0042_voicerecording_client_request_id_and_more')]
        executor = MigrationExecutor(connection)
        executor.migrate(old)
        store = SimpleNamespace(database_alias='default')
        with self.assertRaises(DatabaseError):
            _store_summary(store)
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        self.assertEqual(_store_summary(store), {'count': 0, 'shortfall_amount': 0})
