"""A persisted old-workflow team order is identifiable to staff at 375px."""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from orders.tests.test_release_flow import screenshot


@skipUnless(os.environ.get('XA_LEGACY_TEAM_BROWSER') == '1', 'Requires isolated browser and database')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True)
class LegacyTeamWarningBrowserTest(LiveServerTestCase):
    def test_staff_can_identify_an_inflight_old_workflow(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss, staff, first, second = make_user(), make_console_user(['dashboard:view', 'order:view', 'order:dispatch']), make_provider(), make_provider()
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, staff, first, second)]
        for user, account in zip((boss, staff, first, second), accounts):
            link_legacy_relations(user, account)
        staff.admin_membership.account = accounts[1]; staff.admin_membership.save(update_fields=['account'])
        for user, account in zip((first, second), accounts[2:]):
            user.escort_profile.account = account; user.escort_profile.save(update_fields=['account'])
        make_wallet(boss, balance=100000)
        service = make_service(price=10000, billing_provider_count=2)
        client = APIClient(); token = issue_account_tokens(accounts[1])[0]
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        created = client.post('/api/admin/orders/dispatch/', {'customer_id': accounts[0].pk, 'service_id': service.pk,
            'escort_mode': 'DOUBLE', 'providers': [{'provider_id': account.pk} for account in accounts[2:]],
            'client_request_id': 'legacy-team-warning-fixture'}, format='json').data
        self.assertEqual(created['code'], 0, created)
        order = Order.objects.get(pk=created['data']['id'])
        self.assertTrue(order.requires_boss_selection)
        # Model an existing row created before the new workflow flag was saved.
        Order.objects.filter(pk=order.pk).update(requires_boss_selection=False)
        detail = client.get(f'/api/admin/orders/{order.pk}/').data['data']
        self.assertEqual((detail['escort_mode'], detail['status'], detail['requires_boss_selection']), ('DOUBLE', 'GRABBED', False))
        snapshot = client.get('/api/admin/dashboard/stats').data['data']
        queue = client.get('/api/admin/orders/', {'legacy_team': '1'}).data['data']
        self.assertEqual(snapshot['legacy_team_inflight_total'], 1)
        self.assertTrue(queue['legacy_team_applied'])
        self.assertEqual([row['id'] for row in queue['list']], [order.pk])
        missing_confirmation = client.post(f'/api/admin/orders/{order.pk}/enable-team-evidence/', {}, format='json').data
        self.assertEqual(missing_confirmation['code'], 400)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/admin-aftersale-history-mobile-live.cjs')],
            input=json.dumps({'api': self.live_server_url, 'staffToken': token, 'orderNo': order.order_no, 'legacyTeam': True}),
            text=True, encoding='utf-8', capture_output=True, timeout=75, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        order.refresh_from_db()
        self.assertTrue(order.requires_boss_selection)
        self.assertTrue(order.status_logs.filter(action='TEAM_WORKFLOW', operator_account=accounts[1]).exists())
        self.assertEqual(client.get('/api/admin/dashboard/stats').data['data']['legacy_team_inflight_total'], 0)
        repeated = client.post(f'/api/admin/orders/{order.pk}/enable-team-evidence/', {'confirmed': True}, format='json').data
        self.assertEqual(repeated['code'], 409)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[0])[0])
        owner_messages = client.get('/api/messages/').data['data']['messages']
        updates = [row for row in owner_messages if row['title'] == '双陪服务进度已更新' and row['related_order_id'] == order.pk]
        self.assertEqual(len(updates), 1)
        self.assertIn('分别提交入队和结单截图', updates[0]['preview'])
        owner_token, owner_refresh = issue_account_tokens(accounts[0])
        native = subprocess.run([node, str(repo / 'scripts/native-reservation-notice-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'token': owner_token, 'refresh': owner_refresh,
                'account': accounts[0].pk, 'message': updates[0]['id'], 'expectedTitle': '双陪服务进度已更新',
                'expectedTarget': f'/pages/order-detail/index?id={order.pk}'}),
            text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=repo)
        self.assertEqual(native.returncode, 0, native.stdout + native.stderr)
        for index, account in enumerate(accounts[2:]):
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            notices = client.get('/api/messages/').data['data']['messages']
            self.assertTrue(any(row['title'] == '双陪履约方式已更新' and row['related_order_id'] == order.pk for row in notices))
            response = client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart').data
            self.assertEqual(response['code'], 0, response)
            order.refresh_from_db()
            self.assertEqual(order.status, 'GRABBED' if index == 0 else 'IN_SERVICE')
        # Model an already-running legacy row; it must not be converted in place.
        Order.objects.filter(pk=order.pk).update(requires_boss_selection=False)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        denied = client.post(f'/api/admin/orders/{order.pk}/enable-team-evidence/', {'confirmed': True}, format='json').data
        self.assertEqual(denied['code'], 409)
        order.refresh_from_db()
        self.assertEqual((order.status, order.requires_boss_selection), ('IN_SERVICE', False))
