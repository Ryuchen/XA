"""The batched order-list participants must remain bound to their store."""
from unittest import skipUnless

from django.conf import settings
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import OrderProvider
from orders.tests.factories import make_order, make_provider, make_service, make_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated stores')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class OrderListPrefetchStoreTest(TransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    reset_sequences = True

    def test_legacy_transferred_income_and_active_participants_stay_in_store(self):
        client, entries = APIClient(), []
        for store, host, income in ((resolve_store(), 'sl.esports.club', 160),
                                    (xinan_store(), 'xa.esports.club', 230)):
            with store_context(store):
                boss = make_user()
                boss_account = get_or_create_account_for_legacy_user(boss)
                active = make_provider(deposit_mode='EARNINGS')
                active_account = get_or_create_account_for_legacy_user(active)
                active.escort_profile.account = active_account
                active.escort_profile.save(update_fields=['account'])
                released = make_provider(deposit_mode='EARNINGS')
                released_account = get_or_create_account_for_legacy_user(released)
                released.escort_profile.account = released_account
                released.escort_profile.save(update_fields=['account'])
                order = make_order(boss, service=make_service(), customer_account=boss_account,
                    provider=active, provider_account=active_account, status='IN_SERVICE',
                    payment_status='PAID')
                OrderProvider.objects.create(order=order, provider=active, provider_account=active_account,
                                             settlement_base=500, provider_income=400)
                OrderProvider.objects.create(order=order, provider=released, provider_account=None,
                                             settlement_base=300, provider_income=income,
                                             released_at=timezone.now())
                entries.append((host, issue_account_tokens(released_account)[0], order.pk,
                                active_account.pk, income))
        self.assertEqual(entries[0][2:4], entries[1][2:4])
        for host, token, order_id, active_id, income in entries:
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
            response = client.get('/api/orders/orders/', HTTP_HOST=host)
            self.assertEqual(response.data['code'], 0, response.data)
            row = next(item for item in response.data['data'] if item['id'] == order_id)
            self.assertEqual(row['expected_income'], income)
            self.assertTrue(row['my_assignment_released'])
            self.assertEqual([item['id'] for item in row['participants']], [active_id])
            other_host = entries[1 if host == entries[0][0] else 0][0]
            self.assertNotEqual(client.get('/api/orders/orders/', HTTP_HOST=other_host).status_code, 200)
