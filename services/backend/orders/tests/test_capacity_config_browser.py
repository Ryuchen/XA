"""Admin capacity configuration against the real isolated HTTP API."""
import os
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog
from orders.capacity import current_capacity_rules
from orders.tests.factories import make_console_user


@skipUnless(os.environ.get('XA_CAPACITY_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class CapacityConfigBrowserTest(LiveServerTestCase):
    def test_admin_updates_store_capacity_through_real_page(self):
        permissions = ['order:view', 'dictionary:edit']
        staff = make_console_user(permissions)
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account
        staff.admin_membership.save(update_fields=['account'])
        client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        saved = client.put('/api/admin/config/provider-capacity/',
                           {'max_concurrent_orders': 2, 'version': 0}, format='json')
        self.assertEqual(saved.data['code'], 0, saved.data)
        self.assertEqual(current_capacity_rules(), {'max_concurrent_orders': 2, 'version': 1})
        self.assertEqual(AdminAuditLog.objects.filter(resource='provider-capacity', operator_account=account).count(), 1)