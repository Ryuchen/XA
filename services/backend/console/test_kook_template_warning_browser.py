"""A saved custom template is flagged without changing or sending it."""
import json
import os
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user


@skipUnless(os.environ.get('XA_KOOK_WARNING_BROWSER') == '1', 'Requires isolated admin build')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, KOOK_BOT_TOKEN='')
class KookTemplateWarningBrowserTest(LiveServerTestCase):
    def test_custom_legacy_wording_is_visible_to_editor(self):
        staff = make_console_user(['dispatch_config:view', 'dispatch_config:edit'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account
        staff.admin_membership.save(update_fields=['account'])
        token = issue_account_tokens(account)[0]
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        template = '订单 {order_no} · 单钱 {provider_income}\n报单审核通过后结算。\n自定义说明'
        response = client.put('/api/admin/config/kook-dispatch/', {'revision': 0, 'enabled': False,
            'auto_new_orders': True, 'auto_rejected_orders': True, 'provider_order_url': '',
            'routes': [{'id': 'custom', 'name': '隔离旧文案', 'enabled': True, 'channel_id': '123456',
                        'template': template}]}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/team-evaluations-375-live.cjs')], cwd=repo,
            input=json.dumps({'base': self.live_server_url, 'kookWarningOnly': True, 'staffToken': token,
                'adminRoot': os.environ['XA_KOOK_WARNING_ADMIN_ROOT'], 'providerRoot': str(repo)}),
            text=True, encoding='utf-8', capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        current = client.get('/api/admin/config/kook-dispatch/').data['data']
        self.assertEqual(current['routes'][0]['template'], template)
