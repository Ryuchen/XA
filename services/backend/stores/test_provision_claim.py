"""Safe retry proof uses real temporary SQLite files and isolated identities."""
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core.management.base import CommandError
from django.db import DatabaseError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from .models import Store, StorePlatformGrant, StoreProvisioningClaim
from .provision_claim import (create_or_verify_mysql, create_or_verify_sqlite,
                              expected_marker, reserve_admin_intent, reserve_claim)
from .provisioning import _bootstrap_admin


class ProvisioningClaimTest(TestCase):
    def setUp(self):
        self.store = Store.objects.create(slug='claim-test', name='临时门店', database_alias='candidate')
        self.claim = StoreProvisioningClaim.objects.create(store=self.store, database_fingerprint='a' * 64)

    def test_real_file_creation_and_retry_preserves_business_data(self):
        with TemporaryDirectory(prefix='xa-claim-owned-') as folder:
            path = str(Path(folder) / 'new.sqlite3')
            create_or_verify_sqlite(path, self.claim)
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('CREATE TABLE test_record (value TEXT)')
                db.execute('INSERT INTO test_record VALUES (?)', ('preserved',))
            create_or_verify_sqlite(path, self.claim)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('SELECT value FROM test_record').fetchall(), [('preserved',)])
                self.assertEqual(db.execute('SELECT id, token, store_id, slug FROM xa_store_provision_identity').fetchall(),
                                 [expected_marker(self.claim)])

    def test_existing_unmarked_or_wrong_marker_never_modified(self):
        with TemporaryDirectory(prefix='xa-claim-unowned-') as folder:
            path = str(Path(folder) / 'other.sqlite3')
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('CREATE TABLE customer (name TEXT)')
                db.execute('INSERT INTO customer VALUES (?)', ('original',))
            with self.assertRaisesMessage(CommandError, '归属无法核实'):
                create_or_verify_sqlite(path, self.claim)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('SELECT name FROM customer').fetchall(), [('original',)])
                self.assertFalse(db.execute("SELECT name FROM sqlite_master WHERE name='xa_store_provision_identity'").fetchall())
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('CREATE VIEW xa_store_provision_identity AS SELECT 1 AS id, "wrong" AS token, 1 AS store_id, "wrong" AS slug')
            with self.assertRaisesMessage(CommandError, '归属无法核实'):
                create_or_verify_sqlite(path, self.claim)
            owned = str(Path(folder) / 'wrong.sqlite3')
            create_or_verify_sqlite(owned, self.claim)
            with closing(sqlite3.connect(owned)) as db, db:
                db.execute("UPDATE xa_store_provision_identity SET token='wrong'")
            with self.assertRaisesMessage(CommandError, '归属不匹配'):
                create_or_verify_sqlite(owned, self.claim)
            with closing(sqlite3.connect(owned)) as db:
                self.assertEqual(db.execute('SELECT token FROM xa_store_provision_identity').fetchone(), ('wrong',))

    def test_mysql_existing_schema_only_reads_marker(self):
        server, cursor = MagicMock(), MagicMock()
        cursor.execute.side_effect = [DatabaseError(1007, 'already exists'), None, None]
        cursor.fetchone.return_value = ('BASE TABLE',)
        cursor.fetchall.return_value = [expected_marker(self.claim)]
        create_or_verify_mysql(server, cursor, 'isolated_name', self.claim)
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertEqual(len(statements), 3)
        self.assertTrue(statements[0].startswith('CREATE DATABASE'))
        self.assertTrue(all(statement.startswith('SELECT') for statement in statements[1:]))
        server.commit.assert_not_called()
        cursor.reset_mock(); cursor.execute.side_effect = [DatabaseError(1007, 'already exists'), None, None]
        cursor.fetchall.return_value = [(1, 'wrong', self.store.pk, self.store.slug)]
        with self.assertRaisesMessage(CommandError, '归属不匹配'):
            create_or_verify_mysql(server, cursor, 'isolated_name', self.claim)
        self.assertEqual(len(cursor.execute.call_args_list), 3)

    def test_claim_binds_config_and_admin_intent(self):
        with TemporaryDirectory(prefix='xa-claim-config-') as folder:
            configs = {'default': settings.DATABASES['default'],
                'candidate': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(Path(folder) / 'new.sqlite3')}}
            self.claim.delete()
            with patch('stores.database_identity.settings', SimpleNamespace(DATABASES=configs)):
                claim = reserve_claim(self.store)
                self.assertEqual(reserve_claim(self.store).token, claim.token)
                self.assertEqual(reserve_admin_intent(self.store, 'first-admin').admin_username, 'first-admin')
                with self.assertRaisesMessage(CommandError, '原先登记的管理员'):
                    reserve_admin_intent(self.store, 'other-admin')
                configs['candidate']['NAME'] = str(Path(folder) / 'other.sqlite3')
                with self.assertRaisesMessage(CommandError, '目标数据库配置已变化'):
                    reserve_claim(self.store)

    def test_admin_bootstrap_retry_verifies_identity_without_overwriting(self):
        self.claim.admin_username = 'claim-admin'; self.claim.save(update_fields=['admin_username'])
        password = 'Temporary-Strong-Claim-Password-2026!'
        _bootstrap_admin('claim-admin', None, password=password, phone='13800001111', resume_claim=self.claim)
        account = ClubAccount.objects.get(username='claim-admin')
        password_hash, account_id = account.password, account.pk
        _bootstrap_admin('claim-admin', None, password=password, phone='13800001111', resume_claim=self.claim)
        account.refresh_from_db()
        self.assertEqual((account.pk, account.password), (account_id, password_hash))
        with self.assertRaisesMessage(CommandError, '不会覆盖'):
            _bootstrap_admin('claim-admin', None, password='wrong-password', phone='13800001111', resume_claim=self.claim)
        with self.assertRaisesMessage(CommandError, '不会覆盖'):
            _bootstrap_admin('claim-admin', None, password=password, phone='13800002222', resume_claim=self.claim)
        account.refresh_from_db(); self.assertEqual(account.password, password_hash)

    def test_recovery_api_requires_platform_grant_and_masks_password(self):
        from club_accounts.tokens import issue_account_tokens
        from console.models import AdminAuditLog
        from .tests_foundation import admin
        owner = admin(username='resume-platform-admin', super_admin=True)
        reader = admin(username='resume-no-grant', super_admin=True)
        StorePlatformGrant.objects.create(account_id=owner.pk)
        url = f'/api/stores/manage/{self.store.pk}/initialize/'
        payload = {'admin_username': 'claim-admin', 'admin_password': 'Unique-Recovery-Password-2026!',
                   'admin_phone': '13800001111'}
        denied = APIClient(HTTP_X_STORE_SLUG='shenglang')
        denied.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(reader)[0])
        self.assertEqual(denied.post(url, payload, format='json').status_code, 403)
        client = APIClient(HTTP_X_STORE_SLUG='shenglang')
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(owner)[0])
        with patch('stores.views.configured_database_aliases', return_value=['candidate']), \
             patch('stores.provisioning.create_database') as create, \
             patch('stores.provisioning.initialize_store') as initialize:
            self.assertEqual(client.post(url, {}, format='json').status_code, 400)
            def finish(store, **kwargs):
                store.initialized_at = timezone.now(); store.status = Store.Status.ACTIVE
                store.save(update_fields=['initialized_at', 'status', 'updated_at'])
            initialize.side_effect = finish
            response = client.post(url, payload, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(create.call_count, 1); self.assertEqual(initialize.call_count, 1)
            self.assertEqual(client.post(url, payload, format='json').status_code, 200)
            self.assertEqual(create.call_count, 1); self.assertEqual(initialize.call_count, 1)
        audit = AdminAuditLog.objects.filter(path=url, method='POST').order_by('pk').first()
        self.assertEqual(audit.request_body['admin_password'], '***')

    def test_failed_attempt_never_closes_a_store_already_initialized_by_another_attempt(self):
        from .views import _keep_uninitialized_store_closed
        Store.objects.filter(pk=self.store.pk).update(initialized_at=timezone.now(), status=Store.Status.ACTIVE)
        _keep_uninitialized_store_closed(self.store)
        self.store.refresh_from_db()
        self.assertEqual(self.store.status, Store.Status.ACTIVE)
