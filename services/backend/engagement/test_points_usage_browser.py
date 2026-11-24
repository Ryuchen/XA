import os
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from engagement.points_usage import current_usage
from engagement.services import config
from orders.tests.factories import make_console_user


@skipUnless(os.environ.get('XA_POINTS_USAGE_BROWSER') == '1', 'Optional points-usage switch scenario')
@override_settings(POINTS_USAGE_SWITCHES_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False)
class PointsUsageBrowserTest(LiveServerTestCase):
    def test_permissions_and_paused_status_are_shown(self):
        config()
        clients = []
        for permissions in (['dictionary:view'], ['dictionary:view', 'dictionary:edit']):
            user = make_console_user(permissions)
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            user.admin_membership.account = account; user.admin_membership.save(update_fields=['account'])
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            clients.append(client)
        viewer, editor = clients
        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']
        state = ok(viewer.get('/api/admin/config/points-usage/'))
        payload = {key: state[key] for key in ('redemption_enabled', 'shop_enabled', 'lottery_enabled', 'version', 'issuance_enabled')}
        payload['expected_issuance_enabled'] = state['issuance_enabled']
        payload['lottery_enabled'] = False
        forbidden = viewer.put('/api/admin/config/points-usage/', payload, format='json')
        self.assertEqual(forbidden.status_code, 403)
        saved = ok(editor.put('/api/admin/config/points-usage/', payload, format='json'))
        self.assertFalse(saved['lottery_enabled'])
        self.assertFalse(saved['usage_enabled'])
        self.assertFalse(current_usage()[1]['lottery_enabled'])