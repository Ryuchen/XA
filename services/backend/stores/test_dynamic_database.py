from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from . import dynamic_database as dynamic


class DynamicDatabaseConfigurationTests(SimpleTestCase):
    def setUp(self):
        self.original_registered = set(dynamic._registered)
        dynamic._registered.clear()
        self.addCleanup(self.restore_registered)
        template = {'ENGINE': 'django.db.backends.mysql', 'NAME': 'template', 'HOST': 'db.internal',
            'PORT': '3306', 'USER': 'internal-user', 'PASSWORD': 'not-for-logs'}
        self.config = SimpleNamespace(DYNAMIC_STORE_DATABASE_ENABLED=True,
            DYNAMIC_STORE_DATABASE_TEMPLATE_ALIAS='template', DATABASES={'template': template},
            STORE_DATABASE_ALIASES=('template',))
        self.connections = SimpleNamespace(databases=self.config.DATABASES)
        self.enterContext(patch.object(dynamic, 'settings', self.config))
        self.enterContext(patch.object(dynamic, 'connections', self.connections))

    def restore_registered(self):
        dynamic._registered.clear()
        dynamic._registered.update(self.original_registered)

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
