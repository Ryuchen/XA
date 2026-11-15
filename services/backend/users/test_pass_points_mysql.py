"""Real concurrent pass purchases on an isolated MySQL database."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from common.mysql_test_support import require_disposable_mysql
from console.points_redemption_config import save_points_terms
from engagement.models import MemberProfile, PointLedger
from engagement.services import config
from orders.tests.factories import make_provider
from users.models import ProviderPassPointRedemption, ProviderPassPurchase
from wallet.models import Transaction, get_platform_wallet
from wallet.services import get_wallet


@skipUnless(connection.vendor == 'mysql', 'Requires isolated MySQL row locks')
@override_settings(PROVIDER_PASS_POINTS_PAYMENT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'],
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class PassPointsPurchaseMySQLTest(TransactionTestCase):
    def test_concurrent_quotes_cannot_buy_two_active_passes(self):
        require_disposable_mysql(connection)
        user = make_provider(deposit_mode='EARNINGS')
        account = get_or_create_account_for_legacy_user(user)
        profile = user.escort_profile
        profile.account = account
        profile.save(update_fields=['account'])
        wallet = get_wallet(account=account, user=user)
        wallet.balance = 1000
        wallet.save(update_fields=['balance'])
        points = MemberProfile.objects.create(account=account, points_balance=30000)
        config()
        save_points_terms(group_point_hundredths=10000, group_coin_units=10,
                          max_ratio_basis_points=5000, version=0, audit=lambda *_: None)
        get_platform_wallet()
        token = issue_account_tokens(account)[0]
        client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        quoted = client.post('/api/users/provider-pass/points-quote/',
                             {'tier': 'BRONZE', 'selected_point_hundredths': 20000}, format='json')
        self.assertEqual(quoted.status_code, 200, quoted.data)
        quote = quoted.data['data']
        gate = Barrier(2)

        def buy(index):
            close_old_connections()
            try:
                worker = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
                gate.wait(timeout=10)
                response = worker.post('/api/users/provider-pass/', {
                    'tier': 'BRONZE', 'days': 1, 'expected_daily_price': quote['daily_price'],
                    'points_quote_token': quote['quote_token'], 'client_request_id': f'concurrent-pass-{index}',
                }, format='json')
                return response.data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(buy, (1, 2)))

        self.assertEqual(sum(row.get('code') == 0 for row in responses), 1, responses)
        self.assertEqual(ProviderPassPurchase.objects.filter(account=account).count(), 1)
        self.assertEqual(ProviderPassPointRedemption.objects.filter(purchase__account=account).count(), 1)
        self.assertEqual(PointLedger.objects.filter(account=account, kind='PASS_POINTS').count(), 1)
        self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='PASS_PURCHASE', amount__lt=0).count(), 1)
        wallet.refresh_from_db(); points.refresh_from_db(); profile.refresh_from_db()
        self.assertEqual((wallet.balance, points.points_balance, profile.pass_tier), (820, 10000, 'BRONZE'))
        self.assertEqual(get_platform_wallet().balance, 180)
