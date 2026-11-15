"""Web-created physical database in a disposable MySQL container only."""
import json
import os
import subprocess
import sys
import traceback
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.core.management.base import CommandError
from django.db import connection, connections
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from wallet.test_deposit_lifecycle_mysql import require_isolated
from . import dynamic_database
from .context import resolve_store, store_context
from .models import PlatformAccount, PlatformAuditEvent, PlatformRoleGrant, Store
from .platform_auth import issue_tokens


@skipUnless(connection.vendor == 'mysql' and 'store_b' in connections, 'Requires disposable MySQL')
@override_settings(DYNAMIC_STORE_DATABASE_ENABLED=True, DYNAMIC_STORE_DATABASE_TEMPLATE_ALIAS='store_b',
    PLATFORM_IDENTITY_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class DynamicStoreMySQLTests(TransactionTestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        require_isolated(self)
        # This alias is intentionally materialized during the test, after
        # Django has validated the initial test database allowlist.
        self.__class__.databases = self.__class__.databases | {'tenant_dynamic_test'}
        actor = PlatformAccount(username='dynamic-owner', phone='13800001111', is_phone_verified=True)
        actor.set_password('Synthetic-Platform-Password-2026!')
        actor.save()
        PlatformRoleGrant.objects.create(account=actor, role='SUPER_ADMIN', scope_slug='*')
        token, _ = issue_tokens(actor, 'SMS', actor.phone)
        self.client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        self.actor = actor

    def test_web_create_resume_and_fresh_process_rediscovery(self):
        slug = 'dynamic-test'
        password = 'Synthetic-Store-Administrator-Password-2026!'
        body = {'slug': slug, 'name': '新门店', 'entity_prefix': 'DT', 'provider_name_prefix': 'DT',
                'currency_name': '测试币', 'admin_username': 'dynamic-owner', 'admin_password': password,
                'admin_phone': '13800002222', 'admin_nickname': '首位管理员'}
        finance = PlatformAccount(username='dynamic-finance', phone='13800003333', is_phone_verified=True)
        finance.set_password('Synthetic-Finance-Password-2026!')
        finance.save()
        PlatformRoleGrant.objects.create(account=finance, role='FINANCE', scope_slug='*')
        finance_token, _ = issue_tokens(finance, 'SMS', finance.phone)
        denied = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + finance_token)
        self.assertEqual(denied.post('/api/admin/platform/store-management', body, format='json').status_code, 403)
        self.assertFalse(Store.objects.filter(slug=slug).exists())
        with patch('stores.provisioning.initialize_store', side_effect=CommandError('模拟初始化中断')):
            first = self.client.post('/api/admin/platform/store-management', body, format='json')
        self.assertEqual(first.status_code, 409, first.content)
        store = Store.objects.get(slug=slug)
        self.assertEqual(store.database_alias, 'tenant_dynamic_test')
        self.assertEqual(store.status, Store.Status.PROVISIONING)
        self.assertIsNone(store.initialized_at)
        from .provisioning import initialize_store
        errors = []
        def inspect_initialize(*args, **kwargs):
            try:
                return initialize_store(*args, **kwargs)
            except Exception:
                errors.append(traceback.format_exc())
                raise
        with patch('stores.provisioning.initialize_store', side_effect=inspect_initialize):
            result = self.client.post(f'/api/admin/platform/store-management/{store.pk}/initialize', {
                'admin_username': body['admin_username'], 'admin_password': password,
                'admin_phone': body['admin_phone'], 'admin_nickname': body['admin_nickname'],
            }, format='json')
        self.assertEqual(result.status_code, 200, (result.content, errors))
        store.refresh_from_db()
        self.assertEqual(store.status, Store.Status.CONFIGURING)
        with store_context(store):
            self.assertTrue(ClubAccount.objects.filter(username=body['admin_username'], account_type='STAFF').exists())
            self.assertFalse(ClubAccount.objects.filter(account_type__in=['BOSS', 'PROVIDER']).exists())
        self.assertNotIn(password, json.dumps(list(PlatformAuditEvent.objects.filter(account=self.actor).values('detail')), ensure_ascii=False))

        # Simulate a different worker: discard only this test connection and its
        # in-process registration, not the isolated container's physical DB.
        alias = store.database_alias
        connections[alias].close()
        del connections[alias]
        del settings.DATABASES[alias]
        dynamic_database._registered.discard(alias)
        settings.STORE_DATABASE_ALIASES = tuple(a for a in settings.STORE_DATABASE_ALIASES if a != alias)
        rediscovered = resolve_store(slug, require_active=False)
        self.assertEqual(rediscovered.database_alias, alias)
        with store_context(rediscovered):
            self.assertTrue(ClubAccount.objects.filter(username=body['admin_username']).exists())
        script = '''
import os
import django
django.setup()
from django.conf import settings
settings.DATABASES['default']['NAME'] = os.environ['XA_DYNAMIC_TEST_CONTROL']
settings.DATABASES['store_b']['NAME'] = os.environ['XA_DYNAMIC_TEST_TEMPLATE']
settings.DYNAMIC_STORE_DATABASE_ENABLED = True
settings.DYNAMIC_STORE_DATABASE_TEMPLATE_ALIAS = 'store_b'
from stores.context import resolve_store, store_context
from club_accounts.models import ClubAccount
store = resolve_store('dynamic-test', require_active=False)
with store_context(store):
    assert ClubAccount.objects.filter(username='dynamic-owner', account_type='STAFF').exists()
'''
        env = dict(os.environ, XA_DYNAMIC_TEST_CONTROL=connections['default'].settings_dict['NAME'],
                   XA_DYNAMIC_TEST_TEMPLATE=connections['store_b'].settings_dict['NAME'])
        child = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True, timeout=45)
        self.assertEqual(child.returncode, 0, child.stderr)
