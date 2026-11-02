"""Quick double dispatch links each real recipient to the same order."""
import base64
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
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from orders.tests.test_release_flow import screenshot
from orders.models import Order
from wallet.models import ProviderReport, Transaction


@skipUnless(os.environ.get('XA_QUICK_DOUBLE_BROWSER') == '1', 'Requires isolated H5 build and database')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True)
class QuickDoubleMessageBrowserTest(LiveServerTestCase):
    def test_boss_and_both_providers_open_same_order(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss, staff = make_user(), make_console_user(['order:view', 'order:dispatch', 'report:view', 'report:audit'])
        names = ['隔离陪玩甲', '隔离陪玩乙']
        providers = [make_provider(display_name=name) for name in names]
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, staff, *providers)]
        for user, account in zip((boss, staff, *providers), accounts):
            link_legacy_relations(user, account)
        staff.admin_membership.account = accounts[1]; staff.admin_membership.save(update_fields=['account'])
        for provider, account in zip(providers, accounts[2:]):
            provider.escort_profile.account = account; provider.escort_profile.save(update_fields=['account'])
        make_wallet(boss, balance=100000)
        service = make_service(name='隔离双陪服务', price=10000, billing_provider_count=2)
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[1])
        result = client.post('/api/admin/orders/dispatch/', {'customer_id': accounts[0].pk, 'service_id': service.pk,
            'escort_mode': 'DOUBLE', 'providers': [{'provider_id': account.pk} for account in accounts[2:]],
            'client_request_id': 'quick-double-mobile-navigation'}, format='json').data
        self.assertEqual(result['code'], 0, result)
        order = result['data']
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
        inbox = client.get('/api/messages/').data['data']['messages']
        message = next(row for row in inbox if row['related_order_id'] == order['id'] and row['title'] == '已为你指派双陪')
        repo = Path(settings.BASE_DIR).parent.parent
        native = subprocess.run([node, str(repo / 'scripts/native-quick-double-message-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'messageId': message['id'], 'orderId': order['id'], 'names': names,
                'session': {'token': tokens[0], 'user': {'id': str(accounts[0].pk), 'role': 'customer'}}}),
            text=True, encoding='utf-8', capture_output=True, timeout=45, cwd=repo)
        self.assertEqual(native.returncode, 0, native.stdout + native.stderr)
        for index, (account, token) in enumerate(zip(accounts[2:], tokens[2:])):
            provider = subprocess.run([node, str(repo / 'scripts/provider-aftersale-message-375-live.cjs')],
                input=json.dumps({'api': self.live_server_url, 'token': token, 'providerId': str(account.pk),
                                  'orderNo': order['order_no'], 'names': names, 'quickTeam': True,
                                  'submitEntry': index == 0, 'image': base64.b64encode(screenshot().read()).decode('ascii')}),
                text=True, encoding='utf-8', capture_output=True, timeout=60, cwd=repo)
            self.assertEqual(provider.returncode, 0, provider.stdout + provider.stderr)
        for index, token in enumerate(tokens[2:]):
            if index == 1:
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                response = client.post(f"/api/orders/orders/{order['id']}/start/", {'entry_image': screenshot()}, format='multipart').data
                self.assertEqual(response['code'], 0, response)
            for viewer in (tokens[0], tokens[1], *tokens[2:]):
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + viewer)
                url = f"/api/admin/orders/{order['id']}/" if viewer == tokens[1] else f"/api/orders/orders/{order['id']}/"
                progress = client.get(url).data['data']['team_progress']
                self.assertEqual((progress['stage'], progress['submitted_count'], progress['total_count']),
                                 ('ENTRY', 1, 2) if index == 0 else ('COMPLETION', 0, 2))
        for token in tokens[2:]:
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
            response = client.post(f"/api/orders/orders/{order['id']}/complete/", {'completion_image': screenshot()}, format='multipart').data
            self.assertEqual(response['code'], 0, response)
        self.assertEqual(Order.objects.get(pk=order['id']).status, 'COMPLETED')
        for provider, token in zip(providers, tokens[2:]):
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
            report = ProviderReport.objects.get(order_id=order['id'], provider=provider)
            image = client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart').data
            self.assertEqual(image['code'], 0, image)
            submitted = client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json').data
            self.assertEqual(submitted['code'], 0, submitted)
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[1])
            approved = client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json').data
            self.assertEqual(approved['code'], 0, approved)
        self.assertEqual(ProviderReport.objects.filter(order_id=order['id'], status='APPROVED').count(), 2)
        self.assertIsNone(Order.objects.get(pk=order['id']).settled_at)
        self.assertFalse(Transaction.objects.filter(order_id=order['id'], tx_type='INCOME').exists())
