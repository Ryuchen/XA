"""Exercise a real interrupted initialization in new, disposable SQLite files."""
import os
from pathlib import Path
import tempfile
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
folder = Path(tempfile.mkdtemp(prefix='xa-provision-check-')).resolve(strict=True)
os.environ['XA_PROVISION_TEST_FOLDER'] = str(folder)
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.provisioning_isolated_settings'

try:
    import django
    django.setup()
    from django.core.management import call_command
    from django.db import connections
    from club_accounts.models import ClubAccount
    from stores.context import store_context
    from stores.models import Store, StoreProvisioningClaim
    from stores.provisioning import create_database, initialize_store

    call_command('migrate', database='default', interactive=False, verbosity=0)
    store = Store.objects.create(slug='recovery-fixture', name='恢复测试', entity_prefix='ZZ',
                                 database_alias='store_b', status=Store.Status.PROVISIONING)
    args = dict(admin_username='recovery-fixture-admin', admin_password='Temporary-Fixture-Password-2026!',
                admin_phone='13800001111', new_database_created=True)
    create_database('store_b', admin_username=args['admin_username'])
    assert StoreProvisioningClaim.objects.get(store=store).admin_username == args['admin_username']
    try:
        with patch('stores.provisioning.has_usable_store_admin', side_effect=RuntimeError('synthetic interruption')):
            initialize_store(store, **args)
    except RuntimeError as exc:
        assert str(exc) == 'synthetic interruption'
    else:
        raise AssertionError('interruption was not reached')
    store.refresh_from_db()
    assert store.initialized_at is None and store.status == Store.Status.PROVISIONING
    claim = StoreProvisioningClaim.objects.get(store=store)
    assert claim.admin_username == args['admin_username']
    with store_context(store):
        account = ClubAccount.objects.get(username=args['admin_username'])
        original = (account.pk, account.password)
        with connections['store_b'].cursor() as cursor:
            cursor.execute('CREATE TABLE xa_recovery_fixture (value varchar(40))')
            cursor.execute("INSERT INTO xa_recovery_fixture VALUES ('preserved')")
    create_database('store_b')
    initialize_store(store, new_database_created=True)
    store.refresh_from_db()
    assert store.initialized_at is not None and store.status == Store.Status.CONFIGURING
    with store_context(store):
        account = ClubAccount.objects.get(username=args['admin_username'])
        assert (account.pk, account.password) == original
        assert ClubAccount.objects.filter(username=args['admin_username']).count() == 1
        with connections['store_b'].cursor() as cursor:
            cursor.execute('SELECT value FROM xa_recovery_fixture')
            assert cursor.fetchall() == [('preserved',)]
    print('PASS: interrupted initialization resumes; admin and existing data preserved')
finally:
    try:
        from django.db import connections
        connections.close_all()
    except Exception:
        pass
    # Only remove explicitly named files in the directory created above.
    for name in ('directory.sqlite3', 'store.sqlite3', 'directory.sqlite3-journal', 'store.sqlite3-journal',
                 'directory.sqlite3-wal', 'store.sqlite3-wal', 'directory.sqlite3-shm', 'store.sqlite3-shm'):
        (folder / name).unlink(missing_ok=True)
    folder.rmdir()
