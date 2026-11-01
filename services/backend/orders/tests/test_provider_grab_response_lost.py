"""An accepted grab remains verifiable when its HTTP response is lost."""
import json
import os
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
from orders.models import Order
from orders.tests.factories import make_provider, make_service, make_user, make_wallet
from wallet.models import Transaction, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class ProviderGrabResponseLostTest(LiveServerTestCase):
    def test_real_grab_with_lost_response_refreshes_pool(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required for the provider page')
        boss = make_user()
        boss_account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, boss_account)
        wallet = make_wallet(boss, balance=50000)
        wallet.account = boss_account
        wallet.save(update_fields=['account'])
        provider = make_provider(deposit_mode='EARNINGS')
        provider_account = get_or_create_account_for_legacy_user(provider)
        link_legacy_relations(provider, provider_account)
        provider.escort_profile.account = provider_account
        provider.escort_profile.save(update_fields=['account'])
        service = make_service(price=10000, billing_provider_count=1)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss_account)[0])
        result = client.post('/api/orders/create/', {
            'service_id': service.pk, 'dispatch_route': 'PUBLIC', 'dispatch_mode': 'AUTO_GRAB',
            'client_request_id': 'provider-grab-lost-response',
        }, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get(pk=result.data['data']['id'])
        Order.objects.filter(pk=order.pk).update(pool_entered_at=timezone.now() - timedelta(hours=1))
        repo = Path(settings.BASE_DIR).parent.parent
        env = {**os.environ, 'XA_PROVIDER_GRAB_LOST_HTTP': json.dumps({
            'base': self.live_server_url, 'providerToken': issue_account_tokens(provider_account)[0],
            'orderId': order.pk, 'orderNo': order.order_no,
        })}
        result = subprocess.run([node, '--test', str(repo / 'apps/provider-miniapp/tests/order-report-actions.cjs')],
                                env=env, cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        order.refresh_from_db()
        self.assertEqual((order.status, order.provider_account_id), ('GRABBED', provider_account.pk))
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)
        self.assertEqual(Wallet.objects.get(pk=wallet.pk).balance, 40000)
