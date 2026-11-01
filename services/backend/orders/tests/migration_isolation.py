"""Run historical migrations on a fresh connection without rewinding the suite DB."""

from copy import deepcopy

from django.db import DEFAULT_DB_ALIAS, connections
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.db.migrations.executor import MigrationExecutor


class IsolatedMigrationDatabaseMixin:
    def setUp(self):
        super().setUp()
        original = connections[DEFAULT_DB_ALIAS]
        config = deepcopy(original.settings_dict)
        config.update(ENGINE='django.db.backends.sqlite3', NAME=':memory:', OPTIONS={})
        isolated = DatabaseWrapper(config, DEFAULT_DB_ALIAS)
        setattr(connections._connections, DEFAULT_DB_ALIAS, isolated)

        def restore_database():
            try:
                # Exercise the real remaining migrations even after an assertion fails.
                executor = MigrationExecutor(isolated)
                executor.migrate(executor.loader.graph.leaf_nodes())
            finally:
                isolated.close()
                setattr(connections._connections, DEFAULT_DB_ALIAS, original)

        self.addCleanup(restore_database)
