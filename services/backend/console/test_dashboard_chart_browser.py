"""Opt-in browser verification of real workbench charts using isolated APIs."""
import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider, make_service, make_service_category, make_user
from orders.tests.test_release_flow import screenshot


@skipUnless(os.environ.get('XA_ADMIN_CHART_BROWSER') == '1', 'Requires an isolated browser and database')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class DashboardChartBrowserTest(LiveServerTestCase):
    def test_actual_orders_render_charts_and_support_tooltips_and_theme(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        boss, provider = make_user(), make_provider()
        staff = make_console_user(['dashboard:view', 'player_dashboard:view', 'order:view', 'wallet:recharge'])
        accounts = []
        for user in (boss, provider, staff):
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            accounts.append(account)
        provider.escort_profile.account = accounts[1]
        provider.escort_profile.gender = 'MALE'
        provider.escort_profile.save(update_fields=['account', 'gender'])
        staff.admin_membership.account = accounts[2]
        staff.admin_membership.save(update_fields=['account'])
        service = make_service(name='隔离服务单', price=10000)
        gift = make_service(name='隔离礼物', price=2100, service_category=make_service_category(is_gift=True))
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[2])
        recharge = client.post('/api/admin/wallets/recharge/', {
            'account_id': accounts[0].pk, 'amount': 50000, 'gift_amount': 0,
            'trade_no': 'chart-browser-recharge', 'proof_image': screenshot(),
            'client_request_id': 'chart-browser-recharge',
        }, format='multipart')
        self.assertEqual(recharge.data['code'], 0, recharge.data)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
        order = client.post('/api/orders/create/', {'service_id': service.pk,
            'provider_id': accounts[1].pk, 'client_request_id': 'chart-browser-order'}, format='json')
        self.assertEqual(order.data['code'], 0, order.data)
        sent = client.post('/api/orders/gifts/send/', {'gift_service_id': gift.pk, 'provider_id': accounts[1].pk,
            'quantity': 1, 'expected_amount': 2100, 'client_request_id': 'chart-browser-gift'}, format='json')
        self.assertEqual(sent.data['code'], 0, sent.data)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[2])
        summary = client.get('/api/admin/dashboard/player').data
        self.assertEqual(summary['code'], 0, summary)
        self.assertEqual((summary['data']['kpi']['game_amount'], summary['data']['kpi']['gift_amount']), (10000, 2100))
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/admin-dashboard-charts-live.cjs')],
            input=json.dumps({'api': self.live_server_url, 'token': tokens[2]}), text=True,
            encoding='utf-8', capture_output=True, timeout=120, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
