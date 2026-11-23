"""Restricted-account observation for tests that already hold a known row lock."""
import re
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.db import connection


def require_disposable_mysql(connection):
    """Reject application databases before test-only imports or lock inspection."""
    database = connection.settings_dict
    configured = settings._wrapped
    module = getattr(configured, 'SETTINGS_MODULE', None)
    # Django's override_settings holder deliberately reports SETTINGS_MODULE
    # as None. Follow its parents to verify the actual configured module.
    while module is None and (configured := getattr(configured, 'default_settings', None)) is not None:
        module = getattr(configured, 'SETTINGS_MODULE', None)
    if (connection.vendor != 'mysql'
            or module not in {'config.mysql_test_settings', 'config.multitenant_mysql_test_settings',
                              'config.certificate_mysql_test_settings'}
            or not str(database.get('NAME', '')).startswith('test_xa_test_')
            or not str(database.get('USER', '')).startswith('xa_test')
            or database.get('HOST') not in {'127.0.0.1', 'localhost', 'db'}):
        raise AssertionError('Requires an explicitly isolated xa_test MySQL test database and account: '
                             f'vendor={connection.vendor!r}, settings={module!r}, '
                             f'database={database.get("NAME")!r}, user={database.get("USER")!r}, '
                             f'host={database.get("HOST")!r}')


def mysql_locking_statements(observer, table, *, session_id=None, include_shared=False):
    """Count this test account's active locking SQL for a known held-row table.

    Call only while the test holds the target row lock. SHOW FULL PROCESSLIST
    exposes only the connected account without PROCESS privilege; no other
    application's InnoDB transaction history is inspected. The test still waits
    for the real competing statements before releasing its own lock.
    """
    require_disposable_mysql(observer)
    if not re.fullmatch(r'[a-z][a-z0-9_]*', table):
        raise ValueError('Expected a literal test table name')
    with observer.cursor() as cursor:
        cursor.execute('SHOW FULL PROCESSLIST')
        processes = cursor.fetchall()
    count = 0
    for process in processes:
        if session_id is not None and process[0] != session_id:
            continue
        if process[3] != observer.settings_dict['NAME'] or process[4] != 'Query':
            continue
        query = str(process[7] or '').upper()
        if f'`{table.upper()}`' not in query:
            continue
        if ('FOR UPDATE' in query or re.match(r'\s*(?:UPDATE|INSERT|DELETE)\b', query)
                or (include_shared and 'LOCK IN SHARE MODE' in query)):
            count += 1
    return count


def call_test_catalog_import(**options):
    """Keep the real import, admitting only the independently guarded test DB."""
    if connection.vendor != 'mysql':
        return call_command('seed_club_catalog', **options)
    require_disposable_mysql(connection)
    # Only the environment predicate is replaced. Catalog parsing, validation,
    # ORM writes, transactions and the caller's financial assertions stay real.
    with patch('orders.management.commands.seed_club_catalog.validate_import_database'):
        return call_command('seed_club_catalog', **options)
