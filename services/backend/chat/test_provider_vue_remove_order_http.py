"""Removing an order from a real consultation survives reload and reselection."""
import json
import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from chat.models import ChatSession
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueRemoveOrderHTTPTest(LiveServerTestCase):
    def test_remove_reload_and_choose_another_order(self):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root:
            self.skipTest('requires isolated provider Vue build')
        boss, provider, staff = make_user(), make_provider(), make_console_user(['chat:view', 'chat:reply', 'order:view'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff)]
        for user, account in zip((boss, provider, staff), accounts): link_legacy_relations(user, account)
        provider.escort_profile.account = accounts[1]; provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = accounts[2]; staff.admin_membership.save(update_fields=['account'])
        wallet = make_wallet(boss, balance=50000); wallet.account = accounts[0]; wallet.save(update_fields=['account'])
        service = make_service(name='隔离咨询订单', price=10000); provider.escort_profile.service_items.add(service)
        clients = [APIClient(), APIClient(), APIClient()]
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        for client, token in zip(clients, tokens): client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        first = clients[0].post('/api/orders/create/', {'service_id':service.pk, 'provider_id':accounts[1].pk,
            'client_request_id':'chat-remove-first'}, format='json').data
        self.assertEqual(first['code'], 0, first)
        self.assertEqual(clients[1].post('/api/users/escorts/status/', {'status':'AVAILABLE'}, format='json').data['code'], 0)
        second = clients[0].post('/api/orders/create/', {'service_id':service.pk, 'provider_id':accounts[1].pk,
            'client_request_id':'chat-remove-second'}, format='json').data
        self.assertEqual(second['code'], 0, second)
        for client in clients:
            for order in (first['data'], second['data']):
                prefix = '/api/admin/orders/' if client == clients[2] else '/api/orders/orders/'
                self.assertEqual(client.get(f"{prefix}{order['id']}/").data['data']['status'], 'GRABBED')
        fixed = os.environ.get('XA_CHAT_REMOVE_FIXED') == '1'
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/provider-vue-chat-remove-order-375.cjs')],
            input=json.dumps({'root':root, 'base':self.live_server_url, 'token':tokens[1], 'id':accounts[1].pk,
                'first':first['data'], 'second':second['data'], 'fixed':fixed}),
            text=True, encoding='utf-8', capture_output=True, timeout=50, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout.strip())
        if fixed:
            session = ChatSession.objects.get(account=accounts[1])
            response = clients[2].get(f'/api/admin/chat-sessions/{session.pk}/messages/').data
            self.assertEqual(response['code'], 0, response)
            contents = [row['content'] for row in response['data']['list']]
            self.assertIn('普通咨询草稿', contents)
            self.assertIn(f"订单号 {second['data']['order_no']}\n另一订单咨询", contents)
            self.assertFalse(any(first['data']['order_no'] in value for value in contents))
