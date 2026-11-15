"""Real dictionary endpoint reads only its authenticated store's rules."""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.capacity import CONFIG_KEY, UNCONFIGURED
from orders.tests.factories import make_console_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from wallet.models import SystemConfig


@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False)
class SetupEvidenceIsolationTests(TestCase):
    databases = {'default', 'store_b'}

    def test_dictionary_uses_each_store_and_token_cannot_switch_by_header(self):
        stores = [(resolve_store(), 'sl.esports.club', UNCONFIGURED),
                  (xinan_store(), 'xa.esports.club', '{"max_concurrent_orders":4,"version":1}')]
        tokens = []
        for store, _, value in stores:
            with store_context(store):
                SystemConfig.objects.update_or_create(key=CONFIG_KEY, defaults={'value': value})
                SystemConfig.objects.update_or_create(key='provider_deposit_income_percent', defaults={'value': '0'})
                user = make_console_user(['dictionary:view', 'order:view', 'deposit_rule:view', 'deposit_rule:edit'])
                account = get_or_create_account_for_legacy_user(user)
                user.admin_membership.account = account
                user.admin_membership.save(update_fields=['account'])
                tokens.append(issue_account_tokens(account)[0])
        client = APIClient()
        for (store, host, _), token, expected in zip(stores, tokens, ('MISSING', 'SAVED')):
            response = client.get('/api/admin/dictionaries/', HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token)
            self.assertEqual(response.data['code'], 0, response.data)
            row = next(row for row in response.data['data']['setup_evidence'] if row['key'] == 'capacity')
            self.assertEqual(row['status'], expected, store.slug)
        mismatch = client.get('/api/admin/dictionaries/', HTTP_HOST='xa.esports.club',
                              HTTP_AUTHORIZATION='Bearer ' + tokens[0])
        self.assertNotEqual(mismatch.status_code, 200)
        url = '/api/admin/dictionaries/setup-confirmation/deposit_percent/'
        credentials = {'HTTP_HOST': 'sl.esports.club', 'HTTP_AUTHORIZATION': 'Bearer ' + tokens[0]}
        preview = client.get(url, **credentials).data['data']
        self.assertEqual(client.post(url, {'fingerprint': preview['fingerprint']}, format='json', **credentials).status_code, 200)
        for (_, host, _), token, expected in zip(stores, tokens, ('CONFIRMED', 'NEEDS_CONFIRMATION')):
            response = client.get('/api/admin/dictionaries/', HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token)
            row = next(row for row in response.data['data']['setup_evidence'] if row['key'] == 'deposit_percent')
            self.assertEqual(row['status'], expected)
