from contextlib import nullcontext
from copy import deepcopy
from hashlib import sha256
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.conf import settings
from django.core.management import call_command
from django.db import DatabaseError
from django.test import TestCase
from django.utils import timezone

from . import context, database_identity, routing_snapshot
from . import dynamic_database as dynamic
from .admin_entry import login_context
from .models import Store, StoreProvisioningClaim


class DynamicDatabaseConfigurationTests(TestCase):
    def setUp(self):
        self.original_registered = set(dynamic._registered)
        dynamic._registered.clear()
        self.addCleanup(self.restore_registered)
        template = {'ENGINE': 'django.db.backends.mysql', 'NAME': 'template', 'HOST': 'db.internal',
            'PORT': '3306', 'USER': 'internal-user', 'PASSWORD': 'not-for-logs'}
        self.config = SimpleNamespace(DYNAMIC_STORE_DATABASE_ENABLED=True,
            DYNAMIC_STORE_DATABASE_TEMPLATE_ALIAS='template',
            DATABASES={'default': settings.DATABASES['default'], 'template': template},
            STORE_DATABASE_ALIASES=('template',), SECRET_KEY=settings.SECRET_KEY)
        self.connections = SimpleNamespace(databases=self.config.DATABASES)
        self.enterContext(patch.object(dynamic, 'settings', self.config))
        self.enterContext(patch.object(dynamic, 'connections', self.connections))
        self.enterContext(patch.object(context, 'settings', self.config))
        self.enterContext(patch.object(database_identity, 'settings', self.config))
        self.enterContext(patch.object(routing_snapshot, 'settings', self.config))

    def restore_registered(self):
        dynamic._registered.clear()
        dynamic._registered.update(self.original_registered)

    def register_store(self, *, status=Store.Status.ACTIVE, claim=True):
        store = Store.objects.create(slug='new-store', database_alias='tenant_new_store', name='测试店',
            status=status, initialized_at=None if status == Store.Status.PROVISIONING else timezone.now())
        if claim:
            # Existing claim format from initialization, independent of new code.
            fingerprint = sha256(b'["mysql","db.internal",3306,"tenant_new_store"]').hexdigest()
            StoreProvisioningClaim.objects.create(store=store, database_fingerprint=fingerprint)
        return store

    def test_derived_names_are_stable_and_distinct(self):
        self.assertEqual(dynamic.alias_for_slug('new-store'), 'tenant_new_store')
        self.assertNotEqual(dynamic.alias_for_slug('new-store'), dynamic.alias_for_slug('newstore'))
        self.assertEqual(dynamic.materialize('new-store'), 'tenant_new_store')
        self.assertEqual(self.config.DATABASES['tenant_new_store']['NAME'], 'tenant_new_store')
        self.assertIsNone(self.config.DATABASES['tenant_new_store']['TEST']['NAME'])
        self.assertEqual(dynamic.materialize('new-store'), 'tenant_new_store')
        for slug in ('', '-invalid', 'UPPER', 'a_b', '../x', 'x' * 49):
            with self.subTest(slug=slug), self.assertRaises(ValueError):
                dynamic.alias_for_slug(slug)

    def test_unowned_existing_alias_and_changed_template_fail_closed(self):
        self.config.DATABASES['tenant_other'] = deepcopy(self.config.DATABASES['template'])
        with self.assertRaisesMessage(ValueError, '冲突'):
            dynamic.materialize('other')
        dynamic.materialize('new')
        self.config.DATABASES['template']['HOST'] = 'different-host'
        with self.assertRaisesMessage(ValueError, '冲突'):
            dynamic.materialize('new')

    def test_opt_in_and_template_restrictions(self):
        self.config.DYNAMIC_STORE_DATABASE_ENABLED = False
        self.assertIsNone(dynamic.materialize('new'))
        self.config.DYNAMIC_STORE_DATABASE_ENABLED = True
        for alias in ('', 'default', 'unknown'):
            self.config.DYNAMIC_STORE_DATABASE_TEMPLATE_ALIAS = alias
            with self.subTest(alias=alias), self.assertRaises(ValueError):
                dynamic.materialize('new')

    def test_template_with_untracked_connection_target_is_not_registered(self):
        self.config.DATABASES['template']['OPTIONS'] = {'unix_socket': '/tmp/test-only.sock'}
        with self.assertRaisesMessage(ValueError, '无法核对归属的连接选项'):
            dynamic.materialize('new')
        self.assertNotIn('tenant_new', self.config.DATABASES)

    def test_fresh_worker_rejects_a_changed_template_before_registering_the_store(self):
        store = self.register_store()
        self.config.DATABASES['template']['HOST'] = 'changed.internal'
        self.config.ISOLATED_CONTROL_PLANE = True
        with self.assertRaisesMessage(ValueError, '目标数据库配置已变化'):
            dynamic.discover_store_connection(store)
        with patch.object(routing_snapshot, 'lookup') as fallback:
            with self.assertRaises(context.StoreUnavailable):
                context.resolve_store(store.slug, allow_cached=True)
        fallback.assert_not_called()
        self.assertNotIn(store.database_alias, self.config.DATABASES)
        self.assertNotIn(store.database_alias, self.config.STORE_DATABASE_ALIASES)

    def test_fresh_worker_registers_a_matching_claim_and_reuses_it_in_async_context(self):
        store = context.resolve_store(self.register_store().slug)

        @async_to_sync
        async def database_alias():
            # The Channels middleware uses synchronous `with` after authenticating
            # the store in a database thread.
            with context.store_context(store):
                return context.current_database()

        with self.assertNumQueries(0):
            self.assertEqual(database_alias(), store.database_alias)
        self.assertIn(store.database_alias, self.config.STORE_DATABASE_ALIASES)

    def test_initialized_store_requires_a_claim_but_pending_store_can_prepare_a_connection(self):
        store = self.register_store(claim=False)
        with self.assertRaisesMessage(ValueError, '门店数据库缺少归属记录'):
            dynamic.discover_store_connection(store)
        self.assertNotIn(store.database_alias, self.config.DATABASES)

        store.initialized_at, store.status = None, Store.Status.PROVISIONING
        store.save(update_fields=['initialized_at', 'status'])
        self.assertTrue(dynamic.discover_store_connection(store))
        with self.assertRaisesMessage(ValueError, '门店数据库缺少归属记录'):
            with context.store_context(store):
                self.fail('An unclaimed connection cannot route tenant operations')

    def test_matching_signed_snapshot_routes_when_the_online_claim_query_fails(self):
        from club_accounts.models import StoreRoutingSnapshot

        store = self.register_store()
        payload = routing_snapshot.capture(store, 1)
        row = SimpleNamespace(payload=payload, signature=routing_snapshot._signature(payload, settings.SECRET_KEY))
        del self.config.DATABASES[store.database_alias]
        self.config.STORE_DATABASE_ALIASES = ('template',)
        dynamic._registered.clear()
        self.config.ISOLATED_CONTROL_PLANE = True
        with (patch.object(StoreRoutingSnapshot.objects, 'using') as rows,
              patch.object(routing_snapshot, 'transaction', SimpleNamespace(atomic=lambda **kwargs: nullcontext())),
              patch.object(StoreProvisioningClaim.objects, 'filter', side_effect=DatabaseError('control unavailable')) as claims):
            rows.return_value.filter.return_value.first.return_value = row

            def account(selected, username, password):
                with context.store_context(selected):
                    self.assertEqual(context.current_database(), store.database_alias)
                    return object()

            with patch('stores.admin_entry._account', side_effect=account):
                recovered, _, _ = login_context(f'{store.slug}/admin', 'test-password', {})
            self.assertEqual(recovered.pk, store.pk)
            claims.assert_called_once_with(store_id=store.pk)

    def test_directory_collision_check_discovers_another_dynamic_store(self):
        store = self.register_store()
        alias = dynamic.materialize('another-store')
        database_identity.assert_distinct_store_database(alias)
        self.assertIn(store.database_alias, self.config.DATABASES)

    def test_cli_rediscovers_a_pending_store_before_resuming_initialization(self):
        store = self.register_store(status=Store.Status.PROVISIONING)
        command = 'stores.management.commands.provision_store'
        with (patch('stores.serializers.settings', self.config), patch(f'{command}.create_database') as create,
              patch(f'{command}.initialize_store') as initialize):
            call_command('provision_store', slug=store.slug, name=store.name, database=store.database_alias,
                         create_database=True, stdout=StringIO())
        self.assertIn(store.database_alias, self.config.DATABASES)
        create.assert_called_once_with(store.database_alias, admin_username=None)
        self.assertEqual(initialize.call_args.args[0].pk, store.pk)
