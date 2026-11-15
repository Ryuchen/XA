from datetime import timedelta
from unittest import skipUnless

from django.conf import settings
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_order, make_provider, make_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated databases')
@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False)
class PoolDetailStoreIsolationTests(TransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    reset_sequences = True

    def test_equal_order_ids_obey_each_store_pool_and_token_scope(self):
        records = []
        for store, host, status in ((resolve_store(), 'sl.esports.club', 'AVAILABLE'),
                                    (xinan_store(), 'xa.esports.club', 'OFFLINE')):
            with store_context(store):
                boss = make_user()
                boss_account = get_or_create_account_for_legacy_user(boss)
                player = make_provider(deposit_mode='EARNINGS', status=status, pass_tier='BLACK',
                                       pass_expires_at=timezone.now()+timedelta(days=1))
                account = get_or_create_account_for_legacy_user(player)
                player.escort_profile.account = account
                player.escort_profile.save(update_fields=['account'])
                order = make_order(boss, customer_account=boss_account, payment_status='PAID',
                                   game_nickname='PRIVATE-' + store.slug, game_uid='SECRET-' + store.slug)
                records.append((host, account.pk, order.pk, issue_account_tokens(account)[0]))
        self.assertEqual(records[0][1:3], records[1][1:3])
        client = APIClient()
        first, second = records
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + first[3])
        detail = client.get(f'/api/orders/orders/{first[2]}/', HTTP_HOST=first[0])
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertFalse(detail.data['data']['game_nickname'])
        self.assertFalse(detail.data['data']['game_uid'])
        self.assertNotEqual(client.get(f'/api/orders/orders/{second[2]}/', HTTP_HOST=second[0]).status_code, 200)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + second[3])
        self.assertEqual(client.get(f'/api/orders/orders/{second[2]}/', HTTP_HOST=second[0]).status_code, 404)
