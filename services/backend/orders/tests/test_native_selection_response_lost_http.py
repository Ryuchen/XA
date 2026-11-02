"""Native boss selection recovery against real business and HTTP."""
import json
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderApplication
from orders.tests.factories import make_provider, make_service, make_user, make_wallet
from wallet.models import Transaction, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class NativeSelectionResponseLostHTTPTest(LiveServerTestCase):
    def test_team_selection_response_lost_refreshes_real_order(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node required for native controller')
        boss = make_user()
        boss_account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, boss_account)
        wallet = make_wallet(boss, balance=50000)
        wallet.account = boss_account
        wallet.save(update_fields=['account'])
        service = make_service(price=10000, billing_provider_count=2)
        accounts = []
        for _ in range(2):
            provider = make_provider(deposit_mode='EARNINGS', pass_tier='BLACK',
                                     pass_expires_at=timezone.now() + timedelta(days=1))
            account = get_or_create_account_for_legacy_user(provider)
            link_legacy_relations(provider, account)
            provider.escort_profile.account = account
            provider.escort_profile.save(update_fields=['account'])
            provider.escort_profile.service_items.add(service)
            accounts.append(account)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss_account)[0])
        response = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC',
            'dispatch_mode': 'BOSS_SELECT', 'client_request_id': 'native-selection-response-lost'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        Order.objects.filter(pk=order.pk).update(pool_entered_at=timezone.now() - timedelta(hours=1))
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[0])[0])
        response = client.post(f'/api/orders/orders/{order.pk}/grab/',
                               {'team_size': 2, 'partner_id': accounts[1].pk}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        application = OrderApplication.objects.get(order=order, applicant=accounts[0], partner=accounts[1])
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[1])[0])
        response = client.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/action/',
                               {'action': 'CONFIRM_TEAM'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-selection-lost-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'orderId': order.pk, 'applicationId': application.pk,
                'session': {'token': issue_account_tokens(boss_account)[0],
                            'user': {'id': str(boss_account.pk), 'role': 'customer', 'nickname': '隔离老板'}}}),
            text=True, encoding='utf-8', capture_output=True, timeout=40, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        order.refresh_from_db()
        self.assertEqual(order.status, 'GRABBED')
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)
        self.assertEqual(Wallet.objects.get(pk=wallet.pk).balance, 50000 - order.amount)
