from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_provider
from users.models import ProviderPassPurchase
from wallet.models import IdempotencyKey, SystemConfig, Transaction
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class ProviderPassRulesTests(APITestCase):
    url = '/api/users/provider-pass/'

    def setUp(self):
        self.provider = make_provider(deposit_mode='EARNINGS')
        self.account = get_or_create_account_for_legacy_user(self.provider)
        self.profile = self.provider.escort_profile
        self.profile.account = self.account
        self.profile.save(update_fields=['account'])
        self.wallet = get_wallet(account=self.account, user=self.provider)
        self.wallet.balance = 100000
        self.wallet.save(update_fields=['balance'])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])

    def buy(self, tier='BRONZE', request_id='initial'):
        return self.client.post(self.url, {'tier': tier, 'days': 1, 'client_request_id': request_id}, format='json')

    def test_immediate_activation_rejects_every_tier_until_exact_expiry(self):
        before = timezone.now()
        first = self.buy()
        self.assertEqual(first.data['code'], 0, first.data)
        purchase = ProviderPassPurchase.objects.get(account=self.account)
        self.assertGreaterEqual(purchase.starts_at, before)
        self.assertEqual(purchase.expires_at - purchase.starts_at, timedelta(hours=24))
        self.wallet.refresh_from_db()
        balance = self.wallet.balance
        count = Transaction.objects.count()
        for tier in ('BLACK', 'GOLD', 'SILVER', 'BRONZE'):
            denied = self.buy(tier, f'blocked-{tier}')
            self.assertEqual(denied.data['code'], 400)
            self.assertIn('尚未到期', denied.data['msg'])
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, balance)
        self.assertEqual(Transaction.objects.count(), count)
        self.assertEqual(ProviderPassPurchase.objects.filter(account=self.account).count(), 1)
        replay = self.buy(request_id='initial')
        self.assertEqual(replay.data['errorCode'], 'FUND_ALREADY_PROCESSED')
        with patch('django.utils.timezone.now', return_value=purchase.expires_at):
            second = self.buy('GOLD', 'after-expiry')
        self.assertEqual(second.data['code'], 0, second.data)
        newest = ProviderPassPurchase.objects.get(account=self.account, tier='GOLD')
        self.assertEqual(newest.starts_at, purchase.expires_at)
        self.assertEqual(newest.expires_at - newest.starts_at, timedelta(hours=24))

    def test_historical_pending_pass_blocks_overlap_without_rewriting_it(self):
        self.assertEqual(self.buy().data['code'], 0)
        purchase = ProviderPassPurchase.objects.get(account=self.account)
        later = timezone.now() + timedelta(days=2)
        purchase.starts_at, purchase.expires_at = later, later + timedelta(days=1)
        purchase.save(update_fields=['starts_at', 'expires_at'])
        self.profile.pass_tier, self.profile.pass_expires_at = '', None
        self.profile.save(update_fields=['pass_tier', 'pass_expires_at'])
        self.wallet.refresh_from_db()
        balance = self.wallet.balance
        snapshot = self.client.get(self.url).data['data']
        self.assertIn('历史待生效', snapshot['purchase_block_reason'])
        denied = self.buy('SILVER', 'pending')
        self.assertEqual(denied.data['code'], 400)
        self.wallet.refresh_from_db()
        purchase.refresh_from_db()
        self.assertEqual(self.wallet.balance, balance)
        self.assertEqual((purchase.starts_at, purchase.expires_at), (later, later + timedelta(days=1)))

    def test_changed_price_revalidates_without_claiming_request_or_debiting(self):
        quote = next(item['daily_price'] for item in self.client.get(self.url).data['data']['products'] if item['tier'] == 'BRONZE')
        SystemConfig.objects.update_or_create(key='provider_pass_daily_price_BRONZE', defaults={'value': str(quote + 100)})
        payload = {'tier': 'BRONZE', 'days': 1, 'expected_daily_price': quote, 'client_request_id': 'price-change'}
        denied = self.client.post(self.url, payload, format='json')
        self.assertEqual(denied.data['code'], 400)
        self.assertIn('价格已更新', denied.data['msg'])
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 100000)
        self.assertFalse(IdempotencyKey.objects.filter(account=self.account, request_id='price-change').exists())
        accepted = self.client.post(self.url, {**payload, 'expected_daily_price': quote + 100}, format='json')
        self.assertEqual(accepted.data['code'], 0, accepted.data)
