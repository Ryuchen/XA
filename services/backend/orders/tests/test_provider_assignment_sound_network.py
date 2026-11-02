"""Exercise staff assignment through real ASGI and the provider's H5."""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.test import override_settings
from rest_framework.test import APITransactionTestCase
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ProviderAssignmentSoundNetworkTest(APITransactionTestCase):
    def test_quick_double_dispatch_notifies_boss_of_both_providers(self):
        boss, staff = make_user(), make_console_user(['order:view', 'order:dispatch'])
        providers = [make_provider(display_name='隔离陪玩甲'), make_provider(display_name='隔离陪玩乙')]
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, staff, *providers)]
        for user, account in zip((boss, staff, *providers), accounts):
            link_legacy_relations(user, account)
        staff.admin_membership.account = accounts[1]; staff.admin_membership.save(update_fields=['account'])
        for provider, account in zip(providers, accounts[2:]):
            provider.escort_profile.account = account; provider.escort_profile.save(update_fields=['account'])
        make_wallet(boss, balance=100000)
        service = make_service(name='隔离双陪服务', price=10000, billing_provider_count=2)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[1])[0])
        result = client.post('/api/admin/orders/dispatch/', {
            'customer_id': accounts[0].pk, 'service_id': service.pk, 'escort_mode': 'DOUBLE',
            'providers': [{'provider_id': account.pk} for account in accounts[2:]],
            'client_request_id': 'quick-double-notice-real',
        }, format='json').data
        self.assertEqual(result['code'], 0, result)
        order_id = result['data']['id']
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[0])[0])
        inbox = client.get('/api/messages/').data['data']['messages']
        notices = [row for row in inbox if row['title'] == '已为你指派双陪' and row['related_order_id'] == order_id]
        self.assertEqual(len(notices), 1)
        self.assertIn('隔离陪玩甲', notices[0]['preview'])
        self.assertIn('隔离陪玩乙', notices[0]['preview'])
        order = client.get(f'/api/orders/orders/{order_id}/').data['data']
        self.assertEqual({row['nickname'] for row in order['participants']}, {'隔离陪玩甲', '隔离陪玩乙'})
        for account in accounts[2:]:
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            inbox = client.get('/api/messages/').data['data']['messages']
            notice = next(row for row in inbox if row['related_order_id'] == order_id and row['title'] == '客服派单给你')
            cursor = client.get('/api/messages/assignment-alerts/', {'after': 0}).data['data']
            self.assertEqual(cursor, {'latest_id': notice['id'], 'pending': True})

    def test_boss_direct_selection_alerts_each_assigned_provider_once(self):
        from site_messages.models import Message
        boss = make_user()
        providers = [make_provider() for _ in range(3)]
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, *providers)]
        for user, account in zip((boss, *providers), accounts):
            link_legacy_relations(user, account)
        for user, account in zip(providers, accounts[1:]):
            user.escort_profile.account = account
            user.escort_profile.save(update_fields=['account'])
        make_wallet(boss, balance=100000)
        single, team = make_service(price=10000), make_service(price=10000)
        team.billing_provider_count = 2; team.save(update_fields=['billing_provider_count'])
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[0])[0])
        requests = [
            {'service_id': single.pk, 'provider_id': accounts[1].pk, 'client_request_id': 'direct-sound-single'},
            {'service_id': team.pk, 'provider_ids': [accounts[2].pk, accounts[3].pk], 'client_request_id': 'direct-sound-team'},
        ]
        order_ids = []
        for payload in requests:
            result = client.post('/api/orders/create/', payload, format='json').data
            self.assertEqual(result['code'], 0, result)
            order_ids.append(result['data']['id'])
            replay = client.post('/api/orders/create/', payload, format='json').data
            self.assertEqual(replay['errorCode'], 'FUND_ALREADY_PROCESSED', replay)
            self.assertEqual(replay['fundResult']['data']['id'], result['data']['id'])
        for account, order_id in zip(accounts[1:], (order_ids[0], order_ids[1], order_ids[1])):
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            notice = Message.objects.get(recipient_account=account, related_order_id=order_id, title='老板指定你服务')
            self.assertTrue(notice.assignment_alert)
            self.assertEqual(client.get('/api/messages/assignment-alerts/', {'after': 0}).data['data'],
                             {'latest_id': notice.pk, 'pending': True})
            self.assertEqual(client.get(f'/api/orders/orders/{order_id}/').data['data']['status'], 'GRABBED')

    def test_cursor_uses_unread_current_assignments_and_own_account(self):
        boss, staff, provider, other = make_user(), make_console_user(['order:view', 'order:dispatch']), make_provider(), make_provider()
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, staff, provider, other)]
        for user, account in zip((boss, staff, provider, other), accounts):
            link_legacy_relations(user, account)
        staff.admin_membership.account = accounts[1]; staff.admin_membership.save(update_fields=['account'])
        for user, account in ((provider, accounts[2]), (other, accounts[3])):
            user.escort_profile.account = account; user.escort_profile.save(update_fields=['account'])
        make_wallet(boss, balance=50000)
        service = make_service(price=10000)
        client = APIClient()
        def auth(account):
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        auth(accounts[0])
        result = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'STAFF',
            'client_request_id': 'assignment-cursor-real'}, format='json').data
        self.assertEqual(result['code'], 0, result)
        order_id = result['data']['id']
        auth(accounts[1])
        result = client.post(f'/api/admin/orders/{order_id}/assign/', {'provider_id': accounts[2].pk}, format='json').data
        self.assertEqual(result['code'], 0, result)
        auth(accounts[2])
        cursor = client.get('/api/messages/assignment-alerts/', {'after': 0}).data['data']
        self.assertGreater(cursor['latest_id'], 0); self.assertTrue(cursor['pending'])
        self.assertFalse(client.get('/api/messages/assignment-alerts/', {'after': cursor['latest_id']}).data['data']['pending'])
        auth(accounts[3])
        self.assertEqual(client.get('/api/messages/assignment-alerts/', {'after': 0}).data['data'], {'latest_id': 0, 'pending': False})
        auth(accounts[2])
        result = client.post(f'/api/orders/orders/{order_id}/reject/', {'reason': '隔离验收'}, format='json').data
        self.assertEqual(result['code'], 0, result)
        self.assertFalse(client.get('/api/messages/assignment-alerts/', {'after': 0}).data['data']['pending'])
        auth(accounts[0])
        result = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'STAFF',
            'client_request_id': 'assignment-cursor-read-real'}, format='json').data
        self.assertEqual(result['code'], 0, result)
        auth(accounts[1])
        result = client.post(f"/api/admin/orders/{result['data']['id']}/assign/", {'provider_id': accounts[2].pk}, format='json').data
        self.assertEqual(result['code'], 0, result)
        auth(accounts[2])
        latest = client.get('/api/messages/assignment-alerts/', {'after': cursor['latest_id']}).data['data']
        self.assertTrue(latest['pending'])
        self.assertEqual(client.get(f"/api/messages/{latest['latest_id']}/").data['code'], 0)
        self.assertFalse(client.get('/api/messages/assignment-alerts/', {'after': cursor['latest_id']}).data['data']['pending'])

    def test_real_assignment_and_mute(self):
        database, node = os.environ.get('XA_PROVIDER_CHAT_QA_DB'), shutil.which('node')
        if not node or not database or connection.settings_dict['NAME'] != database:
            self.skipTest('requires an isolated file-backed QA database and Node')
        boss, staff, provider = make_user(), make_console_user(['order:view', 'order:dispatch']), make_provider()
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, staff, provider)]
        for user, account in zip((boss, staff, provider), accounts):
            link_legacy_relations(user, account)
        staff.admin_membership.account = accounts[1]; staff.admin_membership.save(update_fields=['account'])
        provider.escort_profile.account = accounts[2]; provider.escort_profile.save(update_fields=['account'])
        make_wallet(boss, balance=100000)
        service = make_service(price=10000)
        from wallet.models import SystemConfig
        SystemConfig.objects.update_or_create(key='provider_pass_delay_seconds_NONE', defaults={'value': '0'})
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        process = subprocess.Popen([sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', str(port), 'config.asgi:application'],
            cwd=settings.BASE_DIR, env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            for _ in range(150):
                if process.poll() is not None:
                    self.fail('isolated ASGI server exited')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2): break
                except OSError:
                    time.sleep(.1)
            else:
                self.fail('isolated ASGI server did not start')
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run([node, str(repo / 'scripts/provider-assignment-sound-network.cjs')],
                input=json.dumps({'api': f'http://127.0.0.1:{port}', 'bossToken': issue_account_tokens(accounts[0])[0],
                    'staffToken': issue_account_tokens(accounts[1])[0], 'providerToken': issue_account_tokens(accounts[2])[0],
                    'providerId': str(accounts[2].pk), 'serviceId': service.pk}),
                text=True, encoding='utf-8', capture_output=True, timeout=70, cwd=repo)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(Order.objects.filter(customer_account=accounts[0], dispatch_route='STAFF', status='PENDING', reservation__isnull=True).count(), 3)
            self.assertEqual(Order.objects.filter(customer_account=accounts[0], dispatch_route='PUBLIC', status='PENDING', reservation__isnull=True).count(), 2)
            self.assertEqual(Order.objects.filter(customer_account=accounts[0], provider_account=accounts[2], reservation__isnull=True, status='GRABBED').count(), 1)
        finally:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
