import os
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog
from orders.points_redemption_config import current_rules
from orders.tests.factories import make_console_user


@skipUnless(os.environ.get('XA_POINTS_CONFIG_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class PointsConfigBrowserTest(LiveServerTestCase):
    def test_editor_saves_decimal_terms_and_viewer_cannot_edit(self):
        clients = []
        for permissions in (['dictionary:view'], ['dictionary:view', 'dictionary:edit']):
            user = make_console_user(permissions)
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            user.admin_membership.account = account
            user.admin_membership.save(update_fields=['account'])
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            clients.append(client)
        terms = {'group_point_hundredths': 1234, 'group_coin_units': 30,
                 'max_ratio_basis_points': 4555, 'version': 0}
        denied = clients[0].put('/api/admin/config/points-redemption/', terms, format='json')
        self.assertEqual(denied.status_code, 403, denied.data)
        saved = clients[1].put('/api/admin/config/points-redemption/', terms, format='json')
        self.assertEqual(saved.data['code'], 0, saved.data)
        self.assertEqual(current_rules(), {'group_point_hundredths': 1234, 'group_coin_units': 30,
                                           'max_ratio_basis_points': 4555, 'version': 1})
        self.assertEqual(AdminAuditLog.objects.filter(resource='points-redemption-terms').count(), 1)