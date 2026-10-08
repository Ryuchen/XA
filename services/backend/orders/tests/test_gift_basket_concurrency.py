"""Exercise wallet and naming locks with independent MySQL connections."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch
from django.db import close_old_connections, connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import GiftNamingGrant, ProviderNaming
from orders.models import Order, ServiceCategory
from orders.tests.factories import make_user, make_provider, make_service, make_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
@skipUnlessDBFeature('has_select_for_update')
class GiftBasketConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.boss, self.provider = make_user(), make_provider()
        self.buyer = get_or_create_account_for_legacy_user(self.boss)
        self.recipient = get_or_create_account_for_legacy_user(self.provider)
        self.wallet = make_wallet(self.boss, balance=100000)
        self.wallet.account = self.buyer
        self.wallet.save(update_fields=['account'])
        provider_wallet = make_wallet(self.provider, balance=0)
        provider_wallet.account = self.recipient
        provider_wallet.save(update_fields=['account'])
        profile = self.provider.escort_profile
        profile.account = self.recipient
        profile.save(update_fields=['account'])
        category = ServiceCategory.objects.create(name='并发礼物', is_gift=True)
        self.gift = make_service(service_category=category, price=2100, gift_naming_days=2)
        self.token, _ = issue_account_tokens(self.buyer)

    def race(self, keys):
        barrier = Barrier(2)
        def send(key):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + self.token)
                barrier.wait(timeout=10)
                return client.post('/api/orders/gifts/send/', {
                    'items': [{'gift_service_id': self.gift.pk, 'quantity': 1}],
                    'provider_id': self.recipient.pk, 'expected_amount': 2100,
                    'naming_title': '并发心意', 'client_request_id': key,
                }, format='json').data
            finally:
                connections.close_all()
        with patch('engagement.tasks.enqueue_broadcast'), ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(send, keys))

    def test_distinct_purchases_preserve_both_naming_extensions(self):
        start = timezone.now()
        result = self.race(['gift-concurrent-001', 'gift-concurrent-002'])
        self.assertEqual([row['code'] for row in result], [0, 0], result)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 95800)
        self.assertEqual(GiftNamingGrant.objects.count(), 2)
        expiry = ProviderNaming.objects.get().expires_at
        self.assertGreaterEqual(expiry, start + timedelta(days=4))
        self.assertLessEqual(expiry, timezone.now() + timedelta(days=4))

    def test_duplicate_request_debits_and_extends_once(self):
        result = self.race(['gift-concurrent-same'] * 2)
        self.assertEqual(sum(row['code'] == 0 for row in result), 1, result)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 97900)
        self.assertEqual(Order.objects.filter(customer_account=self.buyer).count(), 1)
        self.assertEqual(GiftNamingGrant.objects.count(), 1)
