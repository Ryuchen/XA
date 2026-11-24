"""Real refund API against isolated data; enabled explicitly for live validation."""
from datetime import timedelta
import os
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_provider
from users.models import ProviderPassPurchase, ProviderPassRefund
from wallet.models import Transaction, get_platform_wallet
from wallet.services import get_wallet


@skipUnless(os.environ.get('XA_PASS_REFUND_BROWSER') == '1', 'Requires isolated ASGI and provider Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class PassRefundBrowserTest(LiveServerTestCase):
    def test_provider_refunds_from_actual_page(self):
        provider = make_provider(deposit_mode='EARNINGS')
        account = get_or_create_account_for_legacy_user(provider)
        link_legacy_relations(provider, account)
        profile = provider.escort_profile
        profile.account = account
        now = timezone.now()
        profile.pass_tier, profile.pass_expires_at = 'BRONZE', now + timedelta(hours=26)
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
        shop.balance = 100
        shop.save(update_fields=['balance'])
        client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        refunded = client.post(f'/api/users/provider-pass/{purchase.pk}/refund/',
            {'client_request_id': 'pass-refund-browser'}, format='json')
        self.assertEqual(refunded.data['code'], 0, refunded.data)
        wallet.refresh_from_db()
        refund = ProviderPassRefund.objects.get(purchase=purchase)
        self.assertEqual((wallet.balance, refund.amount, refund.shortfall_amount), (1000, 200, 100))
