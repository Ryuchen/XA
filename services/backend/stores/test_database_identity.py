"""New stores must not silently claim another store's configured database."""
from pathlib import Path
import sqlite3
from contextlib import closing
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.management.base import CommandError
from django.core.management import call_command
from django.conf import settings
from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from .database_identity import assert_distinct_store_database, database_identity
from .models import Store, StorePlatformGrant
from .provisioning import create_database, has_usable_store_admin, initialize_store
from .serializers import StoreCreateSerializer


class DatabaseIdentityTest(SimpleTestCase):
    def test_localhost_and_default_ports_normalize_without_credentials(self):
        configs = {'first': {'ENGINE': 'django.db.backends.mysql', 'NAME': 'ClubA', 'HOST': 'localhost', 'PORT': ''},
                   'same': {'ENGINE': 'django.db.backends.mysql', 'NAME': 'cluba', 'HOST': '127.0.0.1', 'PORT': 3306},
                   'different': {'ENGINE': 'django.db.backends.mysql', 'NAME': 'club_b', 'HOST': '::1', 'PORT': '3306'}}
        with patch('stores.database_identity.settings', SimpleNamespace(DATABASES=configs)):
            self.assertEqual(database_identity('first'), database_identity('same'))
            self.assertNotEqual(database_identity('first'), database_identity('different'))

    def test_sqlite_memory_and_file_identity(self):
        with TemporaryDirectory(prefix='xa-db-identity-') as folder:
            path = str(Path(folder) / 'example.sqlite3')
            configs = {'a': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'},
                       'b': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'},
                       'c': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': path},
                       'd': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(Path(folder) / '.' / 'example.sqlite3')},
                       'uri': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': 'file:existing.sqlite3?mode=rwc'}}
            with patch('stores.database_identity.settings', SimpleNamespace(DATABASES=configs)):
                self.assertNotEqual(database_identity('a'), database_identity('b'))
                self.assertEqual(database_identity('c'), database_identity('d'))
                with self.assertRaisesMessage(ValueError, 'SQLite 文件 URI'):
                    database_identity('uri')


class StoreDatabaseGuardTest(TestCase):
    def settings_for(self, candidate_host='127.0.0.1'):
        return SimpleNamespace(DATABASES={
            'default': {'ENGINE': 'django.db.backends.mysql', 'NAME': 'registered_store', 'HOST': 'localhost', 'PORT': 3306},
            'shadow': {'ENGINE': 'django.db.backends.mysql', 'NAME': 'REGISTERED_STORE', 'HOST': candidate_host, 'PORT': 3306},
        })

    def test_distinct_alias_and_remote_proxy_with_same_database_name_are_rejected(self):
        for host in ('127.0.0.1', 'a-different-proxy.example'):
            with self.subTest(host=host), patch('stores.database_identity.settings', self.settings_for(host)):
                with self.assertRaises(ValueError):
                    assert_distinct_store_database('shadow')
                with patch('stores.serializers.configured_database_aliases', return_value=['shadow']):
                    serializer = StoreCreateSerializer(data={'slug': 'new-shop', 'name': '隔离测试店', 'database_alias': 'shadow'})
                    self.assertFalse(serializer.is_valid(), serializer.errors)
                    self.assertIn('database_alias', serializer.errors)

    @override_settings(ISOLATED_CONTROL_PLANE=True)
    def test_registry_lock_does_not_require_a_store_in_control_database(self):
        from django.db import transaction
        from .database_identity import lock_store_registration
        from .models import StoreRegistryLock
        Store.objects.filter(database_alias='default').update(database_alias='legacy-independent-store')
        with transaction.atomic(using='default'):
            row = lock_store_registration()
            self.assertIsInstance(row, StoreRegistryLock)
            self.assertEqual(row.pk, 1)
            self.assertEqual(row._state.db, 'default')

    def test_control_database_cannot_be_assigned_even_without_default_store(self):
        Store.objects.filter(database_alias='default').update(database_alias='legacy-independent-store')
        with patch('stores.database_identity.settings', self.settings_for('other-proxy.example')):
            with self.assertRaisesMessage(ValueError, '平台控制库'):
                assert_distinct_store_database('shadow')

    def test_missing_other_store_config_fails_closed_before_migration(self):
        Store.objects.create(slug='missing-config', name='配置缺失测试', database_alias='not_configured')
        candidate = Store.objects.create(slug='candidate', name='新店测试', database_alias='shadow')
        fake = self.settings_for(); fake.DATABASES['shadow']['NAME'] = 'new_store'
        with patch('stores.database_identity.settings', fake), patch('stores.provisioning.call_command') as migrate:
            with self.assertRaisesMessage(CommandError, '数据库配置缺失'):
                initialize_store(candidate)
        migrate.assert_not_called()

    def test_unproven_new_database_never_runs_migrations(self):
        candidate = Store.objects.create(slug='unproven-db', name='未确认目标库', database_alias='shadow')
        fake = self.settings_for(); fake.DATABASES['shadow']['NAME'] = 'unrelated_database'
        with patch('stores.database_identity.settings', fake), patch('stores.provisioning.call_command') as migrate:
            with self.assertRaisesMessage(CommandError, '目标库的归属尚未确认'):
                initialize_store(candidate)
        migrate.assert_not_called()

    def test_web_and_cli_reject_conflicting_alias_before_registration(self):
        from club_accounts.tokens import issue_account_tokens
        from .tests_foundation import admin
        owner = admin(username='identity-guard-platform', super_admin=True)
        StorePlatformGrant.objects.create(account_id=owner.pk)
        client = APIClient(HTTP_X_STORE_SLUG='shenglang')
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(owner)[0])
        with patch('stores.database_identity.settings', self.settings_for()), \
             patch('stores.serializers.configured_database_aliases', return_value=['shadow']), \
             patch('stores.views.configured_database_aliases', return_value=['shadow']), \
             patch('stores.management.commands.provision_store.configured_database_aliases', return_value=['shadow']), \
             patch('stores.provisioning.call_command') as migrate:
            directory = client.get('/api/stores/manage/')
            self.assertEqual(directory.status_code, 200, directory.data)
            self.assertNotIn('shadow', directory.data['data']['available_database_aliases'])
            response = client.post('/api/stores/manage/', {'slug': 'web-shadow', 'name': '隔离测试',
                                   'database_alias': 'shadow'}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
            with self.assertRaises(CommandError):
                call_command('provision_store', slug='cli-shadow', name='命令测试', database='shadow')
        self.assertFalse(Store.objects.filter(slug__in=['web-shadow', 'cli-shadow']).exists())
        migrate.assert_not_called()

    def test_web_reports_existing_unverified_database_and_keeps_store_closed(self):
        from club_accounts.tokens import issue_account_tokens
        from .tests_foundation import admin
        owner = admin(username='existing-db-platform', super_admin=True)
        StorePlatformGrant.objects.create(account_id=owner.pk)
        client = APIClient(HTTP_X_STORE_SLUG='shenglang')
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(owner)[0])
        fake = self.settings_for(); fake.DATABASES['shadow']['NAME'] = 'unowned_other_database'
        with patch('stores.database_identity.settings', fake), \
             patch('stores.serializers.configured_database_aliases', return_value=['shadow']), \
             patch('stores.provisioning.create_database', side_effect=CommandError('目标数据库已存在，不能自动接管')), \
             patch('stores.provisioning.initialize_store') as initialize:
            response = client.post('/api/stores/manage/', {'slug': 'unowned-target', 'name': '隔离测试',
                'database_alias': 'shadow', 'admin_username': 'new-owner',
                'admin_password': 'Fixture-Strong-2026!', 'admin_phone': '13800001234',
                'admin_nickname': '测试管理员'}, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['errorCode'], 'STORE_INITIALIZATION_REQUIRES_REVIEW')
        registered = Store.objects.get(slug='unowned-target')
        self.assertEqual(registered.status, Store.Status.PROVISIONING)
        self.assertIsNone(registered.initialized_at)
        initialize.assert_not_called()

    def test_successful_web_registration_masks_admin_password_in_audit(self):
        import json
        from club_accounts.tokens import issue_account_tokens
        from console.models import AdminAuditLog
        from .tests_foundation import admin
        owner = admin(username='audit-password-platform', super_admin=True)
        StorePlatformGrant.objects.create(account_id=owner.pk)
        client = APIClient(HTTP_X_STORE_SLUG='shenglang')
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(owner)[0])
        fake = self.settings_for(); fake.DATABASES['shadow']['NAME'] = 'new_safe_database'
        password = 'Unique-Audit-Fixture-Password-2026!'
        with patch('stores.database_identity.settings', fake), \
             patch('stores.serializers.configured_database_aliases', return_value=['shadow']), \
             patch('stores.provisioning.create_database'), patch('stores.provisioning.initialize_store'):
            response = client.post('/api/stores/manage/', {'slug': 'audit-test-store', 'name': '隔离测试',
                'database_alias': 'shadow', 'admin_username': 'new-owner', 'admin_password': password,
                'admin_phone': '13800001234', 'admin_nickname': '测试管理员'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        audit = AdminAuditLog.objects.get(resource='stores', method='POST')
        self.assertEqual(audit.request_body['admin_password'], '***')
        self.assertNotIn(password, json.dumps(audit.request_body, ensure_ascii=False))

    def test_existing_sqlite_file_is_not_opened_or_changed(self):
        with TemporaryDirectory(prefix='xa-existing-db-') as folder:
            path = Path(folder) / 'unowned.sqlite3'; path.write_bytes(b'unowned database')
            Store.objects.create(slug='sqlite-target', name='目标测试', database_alias='candidate')
            connection = MagicMock(settings_dict={'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(path)})
            configs = {'default': settings.DATABASES['default'], 'candidate': connection.settings_dict}
            with patch('stores.database_identity.settings', SimpleNamespace(DATABASES=configs)), \
                 patch('stores.provisioning.connections', {'candidate': connection}):
                with self.assertRaisesMessage(CommandError, '归属无法核实'):
                    create_database('candidate')
            connection.ensure_connection.assert_not_called()
            self.assertEqual(path.read_bytes(), b'unowned database')

    def test_admin_intent_is_recorded_before_database_creation(self):
        from .models import StoreProvisioningClaim
        with TemporaryDirectory(prefix='xa-owned-intent-') as folder:
            path = Path(folder) / 'new.sqlite3'
            store = Store.objects.create(slug='intent-target', name='目标测试', database_alias='candidate')
            connection = MagicMock(settings_dict={'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(path)})
            configs = {'default': settings.DATABASES['default'], 'candidate': connection.settings_dict}
            with patch('stores.database_identity.settings', SimpleNamespace(DATABASES=configs)), \
                 patch('stores.provisioning.connections', {'candidate': connection}), \
                 patch('stores.provision_claim.create_or_verify_sqlite', side_effect=OSError('test interruption')):
                with self.assertRaises(OSError):
                    create_database('candidate', admin_username='first-admin')
            self.assertEqual(StoreProvisioningClaim.objects.get(store=store).admin_username, 'first-admin')
            self.assertFalse(path.exists())

    def test_owned_sqlite_database_can_retry_without_changing_its_data(self):
        with TemporaryDirectory(prefix='xa-owned-db-') as folder:
            path = Path(folder) / 'owned.sqlite3'
            Store.objects.create(slug='owned-target', name='目标测试', database_alias='candidate')
            connection = MagicMock(settings_dict={'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(path)})
            configs = {'default': settings.DATABASES['default'], 'candidate': connection.settings_dict}
            with patch('stores.database_identity.settings', SimpleNamespace(DATABASES=configs)), \
                 patch('stores.provisioning.connections', {'candidate': connection}):
                create_database('candidate')
                with closing(sqlite3.connect(path)) as db, db:
                    db.execute('CREATE TABLE test_record (value TEXT)')
                    db.execute('INSERT INTO test_record VALUES (?)', ('retain me',))
                create_database('candidate')
                with closing(sqlite3.connect(path)) as db:
                    self.assertEqual(db.execute('SELECT value FROM test_record').fetchall(), [('retain me',)])
                from .models import StoreProvisioningClaim
                self.assertEqual(StoreProvisioningClaim.objects.filter(store__slug='owned-target').count(), 1)
                changed = Path(folder) / 'other.sqlite3'
                connection.settings_dict['NAME'] = str(changed)
                with self.assertRaisesMessage(CommandError, '目标数据库配置已变化'):
                    create_database('candidate')
                self.assertFalse(changed.exists())

    def test_cached_connection_config_mismatch_is_rejected_before_file_creation(self):
        with TemporaryDirectory(prefix='xa-cached-db-') as folder:
            Store.objects.create(slug='cached-target', name='目标测试', database_alias='candidate')
            configured = str(Path(folder) / 'configured.sqlite3')
            stale = str(Path(folder) / 'stale.sqlite3')
            connection = MagicMock(settings_dict={'ENGINE': 'django.db.backends.sqlite3', 'NAME': stale})
            configs = {'default': settings.DATABASES['default'], 'candidate':
                {'ENGINE': 'django.db.backends.sqlite3', 'NAME': configured}}
            with patch('stores.database_identity.settings', SimpleNamespace(DATABASES=configs)), \
                 patch('stores.provisioning.connections', {'candidate': connection}):
                with self.assertRaisesMessage(CommandError, '连接配置已变化'):
                    create_database('candidate')
            self.assertFalse(Path(configured).exists()); self.assertFalse(Path(stale).exists())

    def test_cli_requires_phone_before_creating_new_store_record(self):
        with self.assertRaisesMessage(CommandError, '新管理员需要可接收短信的手机号'):
            call_command('provision_store', slug='without-phone', name='测试店', database='candidate',
                         admin_username='new-admin', password_env='NOT_A_REAL_PASSWORD')
        self.assertFalse(Store.objects.filter(slug='without-phone').exists())

    def test_new_store_admin_requires_a_usable_login_factor(self):
        from .tests_foundation import admin
        account = admin(username='factor-guard-admin')
        self.assertFalse(has_usable_store_admin())
        account.phone = '13800001111'; account.save(update_fields=['phone'])
        self.assertTrue(has_usable_store_admin())
        account.set_unusable_password(); account.save(update_fields=['password'])
        self.assertFalse(has_usable_store_admin())
        from club_accounts.models import OtpCredential
        from club_accounts.otp import encrypt_secret
        account.phone = ''; account.set_password('temporary-test-password')
        account.save(update_fields=['phone', 'password'])
        credential = OtpCredential.objects.create(account=account, secret_ciphertext=encrypt_secret('JBSWY3DPEHPK3PXP'))
        self.assertTrue(has_usable_store_admin())
        credential.delete()
        account.is_openid_bound = True; account.openid = 'synthetic-openid'
        account.save(update_fields=['is_openid_bound', 'openid'])
        self.assertTrue(has_usable_store_admin())
        account.can_login = False; account.save(update_fields=['can_login'])
        self.assertFalse(has_usable_store_admin())

    def test_mysql_existing_database_is_not_silently_adopted(self):
        Store.objects.create(slug='mysql-target', name='目标测试', database_alias='candidate')
        connection = MagicMock(settings_dict={'ENGINE': 'django.db.backends.mysql', 'NAME': 'new_store'})
        server = MagicMock()
        cursor = server.cursor.return_value.__enter__.return_value
        cursor.execute.side_effect = DatabaseError('database already exists')
        backend = SimpleNamespace(DatabaseWrapper=MagicMock(return_value=server))
        configs = {'default': settings.DATABASES['default'], 'candidate': connection.settings_dict}
        with patch('stores.database_identity.settings', SimpleNamespace(DATABASES=configs)), \
             patch('stores.provisioning.connections', {'candidate': connection}), \
             patch('stores.provisioning.load_backend', return_value=backend):
            with self.assertRaisesMessage(CommandError, '不会自动接管'):
                create_database('candidate')
        sql = cursor.execute.call_args.args[0]
        self.assertIn('CREATE DATABASE `new_store`', sql)
        self.assertNotIn('IF NOT EXISTS', sql)
        server.close.assert_called_once()

    def test_missing_database_name_never_opens_or_creates_a_target(self):
        Store.objects.create(slug='nameless-target', name='目标测试', database_alias='candidate')
        for engine in ('django.db.backends.sqlite3', 'django.db.backends.mysql'):
            connection = MagicMock(settings_dict={'ENGINE': engine, 'NAME': None})
            with self.subTest(engine=engine), patch('stores.database_identity.assert_distinct_store_database'), \
                 patch('stores.provisioning.connections', {'candidate': connection}), \
                 patch('stores.provisioning.load_backend') as backend:
                with self.assertRaises(CommandError):
                    create_database('candidate')
                connection.ensure_connection.assert_not_called()
                backend.assert_not_called()
