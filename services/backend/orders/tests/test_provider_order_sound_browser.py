"""Actual paid order pool stays usable through grab/reject over real HTTP.

Rewritten from the retired sound H5 driver: sound-toggle/oscillator-count and
375px scroll-overflow UI coverage is dropped; backend assertions are unchanged.
"""
import os
from unittest import skipUnless
from django.test import override_settings
from rest_framework.test import APIClient, APITransactionTestCase
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.grab_config import save_grab_window
from engagement.models import PointFundingLot
from orders.models import Order
from orders.tests.factories import make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile
from wallet.models import SystemConfig, Wallet


@skipUnless(os.environ.get('XA_PROVIDER_SOUND_BROWSER') == '1', 'Requires isolated QA database')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
                   GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True, GRAB_SINGLE_WINDOW_ENABLED=True,
                   GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True)
class ProviderOrderSoundBrowserTest(APITransactionTestCase):
    def test_mute_and_enable_with_actual_paid_orders(self):
        boss, provider, other_provider = make_user(), make_provider(), make_provider()
        boss_account, provider_account = [get_or_create_account_for_legacy_user(user) for user in (boss, provider)]
        for user, account in ((boss, boss_account), (provider, provider_account)):
            link_legacy_relations(user, account)
        EscortProfile.objects.filter(user=provider).update(account=provider_account)
        other_account = get_or_create_account_for_legacy_user(other_provider)
        link_legacy_relations(other_provider, other_account)
        EscortProfile.objects.filter(user=other_provider).update(account=other_account)
        make_wallet(boss, balance=50000)
        PointFundingLot.objects.create(account=boss_account, source_key='sound-real-prepaid', remaining=50000)
        service = make_service(price=10000)
        for tier in ('BLACK', 'GOLD', 'SILVER', 'BRONZE', 'NONE'):
            SystemConfig.objects.update_or_create(key='provider_pass_delay_seconds_' + tier, defaults={'value': '0'})
        save_grab_window(30, 0, lambda *args: None)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss_account)[0])
        order_ids = []
        for request_id in ('sound-real-mute', 'sound-real-on'):
            result = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC',
                'dispatch_mode': 'AUTO_GRAB', 'client_request_id': request_id}, format='json').data
            self.assertEqual(result['code'], 0, result)
            order_ids.append(result['data']['id'])
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(other_account)[0])
        for action in ('grab', 'reject'):
            result = client.post(f'/api/orders/orders/{order_ids[1]}/{action}/', {}, format='json').data
            self.assertEqual(result['code'], 0, result)
        self.assertEqual(Order.objects.filter(customer_account=boss_account, payment_status='PAID').count(), 2)
        self.assertEqual(Order.objects.get(customer_account=boss_account, reject_count=1).status, 'PENDING')
        self.assertEqual(Wallet.objects.get(user=boss).balance, 30000)
