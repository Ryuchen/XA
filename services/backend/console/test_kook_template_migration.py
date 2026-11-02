"""Only unchanged default dispatch templates receive the corrected wording."""
from importlib import import_module
from unittest import skipIf

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from orders.tests.migration_isolation import IsolatedMigrationDatabaseMixin


@skipIf(bool(settings.MIGRATION_MODULES), 'Requires real migrations')
class KookTemplateMigrationTest(IsolatedMigrationDatabaseMixin, TransactionTestCase):
    def test_default_is_updated_while_custom_template_is_preserved(self):
        old, new = [('console', '0016_withdraw_release_permission')], [('console', '0017_update_default_dispatch_settlement_text')]
        executor = MigrationExecutor(connection)
        executor.migrate(old)
        Config = executor.loader.project_state(old).apps.get_model('console', 'KookDispatchConfig')
        migration = import_module('console.migrations.0017_update_default_dispatch_settlement_text')
        custom = migration.OLD + '\n自定义说明'
        Config.objects.create(id=1, revision=4, routes=[{'id': 'default', 'template': migration.OLD},
                                                        {'id': 'custom', 'template': custom}])
        executor = MigrationExecutor(connection)
        executor.migrate(new)
        Config = executor.loader.project_state(new).apps.get_model('console', 'KookDispatchConfig')
        row = Config.objects.get(pk=1)
        self.assertEqual(row.revision, 5)
        self.assertEqual(row.routes[0]['template'], migration.NEW)
        self.assertEqual(row.routes[1]['template'], custom)
