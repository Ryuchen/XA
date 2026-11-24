"""Setup evidence saves and permission masking against the isolated backend."""
import os
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user
from wallet.models import SystemConfig


@skipUnless(os.environ.get('XA_SETUP_EVIDENCE_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class SetupEvidenceBrowserTest(LiveServerTestCase):
    def test_real_screen_shows_evidence_and_respects_view_permissions(self):
        clients = []
        SystemConfig.objects.update_or_create(key='provider_deposit_income_percent', defaults={'value': '0'})
        for permissions in (['dictionary:view', 'dictionary:edit', 'order:view', 'deposit_rule:view', 'deposit_rule:edit'],
                            ['dictionary:view']):
            user = make_console_user(permissions)
            account = get_or_create_account_for_legacy_user(user)
            user.admin_membership.account = account
            user.admin_membership.save(update_fields=['account'])
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            clients.append(client)
        saved = clients[0].put('/api/admin/config/provider-capacity/', {'max_concurrent_orders': 4, 'version': 0}, format='json')
        self.assertEqual(saved.data['code'], 0, saved.data)
        self.assertEqual(saved.data['data'], {'max_concurrent_orders': 4, 'version': 1})
        confirmed = clients[0].put('/api/admin/config/deposit-rules', {'deposit_percent': 0, 'expected_deposit_percent': 0}, format='json')
        self.assertEqual(confirmed.data['code'], 0, confirmed.data)
        restricted = clients[1].get('/api/admin/config/withdraw')
        self.assertEqual(restricted.status_code, 403, restricted.data)