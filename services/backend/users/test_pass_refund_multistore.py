from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_provider
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from users.models import ProviderPassPurchase, ProviderPassRefund
from wallet.models import Transaction, get_platform_wallet
from wallet.services import get_wallet


@override_settings(LOGIN_OTP_REQUIRED=False, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class PassRefundStoreIsolationTest(TestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first, self.second = resolve_store(), xinan_store()
        self.rows = {}
        for store in (self.first, self.second):
            with store_context(store):
                provider = make_provider(deposit_mode='EARNINGS')
                account = get_or_create_account_for_legacy_user(provider)
                profile = provider.escort_profile
                now = timezone.now()
                profile.account, profile.pass_tier = account, 'BRONZE'
                profile.pass_expires_at = now + timedelta(hours=26)
                profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])
                wallet = get_wallet(account=account, user=provider)
                wallet.balance = 800
                wallet.save(update_fields=['balance'])
                payment = Transaction.objects.create(wallet=wallet, tx_type='PASS_PURCHASE', amount=-200,
                    balance_before=1000, balance_after=800)
                purchase = ProviderPassPurchase.objects.create(provider=provider, account=account, tier='BRONZE',
                    days=1, daily_price=200, original_amount=200, paid_amount=200, transaction=payment,
                    starts_at=now + timedelta(hours=2), expires_at=profile.pass_expires_at)
                shop = get_platform_wallet()
                shop.balance = 200
                shop.save(update_fields=['balance'])
                self.rows[store.slug] = (purchase.pk, account.pk, issue_account_tokens(account)[0])

    def test_same_number_refund_cannot_change_other_store(self):
        first_id, first_account, first_token = self.rows['shenglang']
        second_id, second_account, second_token = self.rows['xinan']
        self.assertEqual((first_id, first_account), (second_id, second_account))
        client = APIClient()
        url = f'/api/users/provider-pass/{first_id}/refund/'
        wrong_host = client.post(url, {'client_request_id':'wrong-store'}, format='json',
            HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + first_token)
        self.assertEqual(wrong_host.status_code, 401)
        response = client.post(url, {'client_request_id':'own-store'}, format='json',
            HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + first_token)
        self.assertEqual(response.data['code'], 0, response.data)
        with store_context(self.first):
            self.assertEqual(ProviderPassRefund.objects.count(), 1)
            self.assertEqual(get_wallet(account_id=first_account).balance, 1000)
        with store_context(self.second):
            self.assertEqual(ProviderPassRefund.objects.count(), 0)
            self.assertEqual(get_wallet(account_id=second_account).balance, 800)
        second = client.get('/api/users/provider-pass/', HTTP_HOST='xa.esports.club',
                            HTTP_AUTHORIZATION='Bearer ' + second_token)
        self.assertTrue(second.data['data']['history'][0]['refundable'])
