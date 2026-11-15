from datetime import timedelta
from unittest.mock import patch
from unittest import skipUnless

from django.conf import settings
from django.test import TestCase
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.points_redemption_config import save_points_terms
from engagement.models import MemberProfile, PointLedger
from engagement.points_usage import lock_usage
from engagement.services import config
from orders.tests.factories import make_console_user, make_provider
from stores.db import transaction
from users.models import ProviderPassPointRedemption, ProviderPassPurchase, ProviderPassRefund
from wallet.models import Transaction, get_platform_wallet
from wallet.services import get_wallet


@override_settings(PROVIDER_PASS_POINTS_PAYMENT_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class PassPointsPaymentTests(APITestCase):
    def setUp(self):
        self.user = make_provider(deposit_mode='EARNINGS')
        self.account = get_or_create_account_for_legacy_user(self.user)
        self.profile = self.user.escort_profile; self.profile.account = self.account
        self.profile.save(update_fields=['account'])
        self.wallet = get_wallet(account=self.account, user=self.user)
        self.wallet.balance = 1000; self.wallet.save(update_fields=['balance'])
        self.points = MemberProfile.objects.create(account=self.account, points_balance=30000)
        config()
        save_points_terms(group_point_hundredths=10000, group_coin_units=10,
                          max_ratio_basis_points=5000, version=0, audit=lambda *_: None)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
        self.url = '/api/users/provider-pass/'

    def quote(self, selected=20000):
        result = self.client.post(self.url + 'points-quote/', {'tier': 'BRONZE',
            'selected_point_hundredths': selected}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        return result.data['data']

    def buy(self, quote, request_id='points-pass-01'):
        return self.client.post(self.url, {'tier': 'BRONZE', 'days': 1,
            'expected_daily_price': quote['daily_price'], 'points_quote_token': quote['quote_token'],
            'client_request_id': request_id}, format='json')

    def test_purchase_and_idempotent_retry_preserve_both_ledgers(self):
        quote = self.quote()
        self.assertEqual((quote['daily_price'], quote['discount_units'], quote['payable_units']), (200, 20, 180))
        self.assertEqual(ProviderPassPurchase.objects.count(), 0)
        result = self.buy(quote)
        self.assertEqual(result.data['code'], 0, result.data)
        purchase = ProviderPassPurchase.objects.get(account=self.account)
        self.assertEqual((purchase.original_amount, purchase.discount_amount, purchase.point_discount_amount,
                          purchase.paid_amount), (200, 0, 20, 180))
        points = ProviderPassPointRedemption.objects.get(purchase=purchase)
        self.assertEqual((points.points_hundredths, points.discount_units, points.groups), (20000, 20, 2))
        self.assertEqual((points.ledger.account_id, points.ledger.amount), (self.account.pk, -20000))
        self.wallet.refresh_from_db(); self.points.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.points.points_balance), (820, 10000))
        self.assertEqual(get_platform_wallet().balance, 180)
        self.assertEqual(purchase.expires_at - purchase.starts_at, timedelta(hours=24))
        replay = self.buy(quote)
        self.assertEqual(replay.data['errorCode'], 'FUND_ALREADY_PROCESSED')
        self.assertEqual(ProviderPassPurchase.objects.count(), 1)
        self.assertEqual(PointLedger.objects.filter(kind='PASS_POINTS').count(), 1)
        staff = make_console_user(['escort:view'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        from rest_framework.test import APIClient
        admin = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        row = admin.get('/api/admin/pass-purchases/').data['data']['list'][0]
        self.assertEqual((row['original_amount'], row['paid_amount'], row['point_discount_amount'],
                          row['points_hundredths']), (200, 180, 20, 20000))

    def test_change_in_balance_rules_or_switch_rejects_without_debit(self):
        quote = self.quote()
        self.points.points_balance = 5000; self.points.save(update_fields=['points_balance'])
        self.assertEqual(self.buy(quote).status_code, 400)
        self.points.points_balance = 30000; self.points.save(update_fields=['points_balance'])
        save_points_terms(group_point_hundredths=10000, group_coin_units=20,
                          max_ratio_basis_points=5000, version=1, audit=lambda *_: None)
        self.assertEqual(self.buy(quote).status_code, 400)
        quote = self.quote()
        with transaction.atomic():
            _, row, _ = lock_usage()
            row.value = '{"redemption_enabled":true,"shop_enabled":false,"lottery_enabled":true,"version":1}'
            row.save(update_fields=['value'])
        self.assertEqual(self.buy(quote).status_code, 400)
        self.wallet.refresh_from_db(); self.points.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.points.points_balance), (1000, 30000))
        self.assertFalse(ProviderPassPurchase.objects.exists())
        self.assertFalse(PointLedger.objects.exists())

    def test_each_usage_switch_blocks_existing_quote_and_resumes(self):
        import json
        for field in ('points_enabled', 'redemption_enabled', 'shop_enabled', 'lottery_enabled'):
            with self.subTest(field=field):
                quote = self.quote()
                with transaction.atomic():
                    operation, row, switches = lock_usage()
                    if field == 'points_enabled':
                        operation.points_enabled = False
                        operation.save(update_fields=['points_enabled'])
                    else:
                        switches[field] = False
                        row.value = json.dumps(switches)
                        row.save(update_fields=['value'])
                self.assertEqual(self.buy(quote, 'paused-' + field).status_code, 400)
                self.wallet.refresh_from_db(); self.points.refresh_from_db()
                self.assertEqual((self.wallet.balance, self.points.points_balance), (1000, 30000))
                self.assertFalse(ProviderPassPurchase.objects.exists())
                with transaction.atomic():
                    operation, row, switches = lock_usage()
                    operation.points_enabled = True
                    operation.save(update_fields=['points_enabled'])
                    switches.update(redemption_enabled=True, shop_enabled=True, lottery_enabled=True)
                    row.value = json.dumps(switches)
                    row.save(update_fields=['value'])
        self.assertEqual(self.buy(self.quote(), 'resumed-pass').data['code'], 0)

    def test_storage_failure_rolls_back_all_money_and_entitlement(self):
        quote = self.quote()
        with patch('users.models.ProviderPassPointRedemption.objects.create', side_effect=RuntimeError('rollback')):
            with self.assertRaises(RuntimeError):
                self.buy(quote)
        self.wallet.refresh_from_db(); self.points.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.points.points_balance, self.profile.pass_tier), (1000, 30000, ''))
        self.assertFalse(ProviderPassPurchase.objects.exists())
        self.assertFalse(PointLedger.objects.exists())
        self.assertFalse(Transaction.objects.filter(tx_type='PASS_PURCHASE').exists())

    def test_discounted_price_can_be_paid_with_less_than_full_price(self):
        self.wallet.balance = 180; self.wallet.save(update_fields=['balance'])
        quote = self.quote()
        self.assertTrue(quote['balance_sufficient'])
        self.assertEqual(self.buy(quote).data['code'], 0)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 0)

    def test_invalid_or_foreign_quote_cannot_create_payment(self):
        quote = self.quote()
        for token in (None, '', quote['quote_token'] + 'tampered'):
            with self.subTest(token=token):
                result = self.client.post(self.url, {'tier': 'BRONZE', 'days': 1,
                    'points_quote_token': token, 'client_request_id': 'invalid-token'}, format='json')
                self.assertEqual(result.status_code, 400)
        other_user = make_provider(deposit_mode='EARNINGS')
        other_account = get_or_create_account_for_legacy_user(other_user)
        other_profile = other_user.escort_profile; other_profile.account = other_account; other_profile.save(update_fields=['account'])
        other_wallet = get_wallet(account=other_account, user=other_user); other_wallet.balance = 1000; other_wallet.save(update_fields=['balance'])
        MemberProfile.objects.create(account=other_account, points_balance=30000)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(other_account)[0])
        self.assertEqual(self.buy(quote, 'foreign-token').status_code, 400)
        other_wallet.refresh_from_db()
        self.assertEqual(other_wallet.balance, 1000)
        self.assertFalse(ProviderPassPurchase.objects.exists())

    def test_expired_quote_is_rejected(self):
        import time
        quote = self.quote()
        with patch('django.core.signing.time.time', return_value=time.time() + 601):
            self.assertEqual(self.buy(quote, 'expired-points-token').status_code, 400)
        self.assertFalse(ProviderPassPurchase.objects.exists())

    def test_pending_purchase_refunds_coin_and_points_even_when_usage_paused(self):
        quote = self.quote(); self.assertEqual(self.buy(quote).data['code'], 0)
        purchase = ProviderPassPurchase.objects.get(account=self.account)
        start = timezone.now() + timedelta(hours=2)
        purchase.starts_at, purchase.expires_at = start, start + timedelta(days=1)
        purchase.save(update_fields=['starts_at', 'expires_at'])
        self.profile.pass_tier, self.profile.pass_expires_at = purchase.tier, purchase.expires_at
        self.profile.save(update_fields=['pass_tier', 'pass_expires_at'])
        shop = get_platform_wallet(); shop.balance = 0; shop.save(update_fields=['balance'])
        with transaction.atomic():
            _, row, _ = lock_usage()
            row.value = '{"redemption_enabled":false,"shop_enabled":true,"lottery_enabled":true,"version":1}'
            row.save(update_fields=['value'])
        response = self.client.post(self.url + f'{purchase.pk}/refund/', {'client_request_id': 'points-refund-01'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        refund = ProviderPassRefund.objects.get(purchase=purchase)
        self.assertEqual((refund.amount, refund.shortfall_amount, refund.points_returned), (180, 180, 20000))
        self.assertEqual(refund.points_return_ledger.amount, 20000)
        self.wallet.refresh_from_db(); self.points.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.points.points_balance), (1000, 30000))
        replay = self.client.post(self.url + f'{purchase.pk}/refund/', {'client_request_id': 'points-refund-01'}, format='json')
        self.assertEqual(replay.data['errorCode'], 'FUND_ALREADY_PROCESSED')
        self.assertEqual(PointLedger.objects.filter(kind='PASS_POINTS_RETURN').count(), 1)

    def test_refund_storage_failure_rolls_back_coin_and_points(self):
        self.assertEqual(self.buy(self.quote()).data['code'], 0)
        purchase = ProviderPassPurchase.objects.get(account=self.account)
        start = timezone.now() + timedelta(hours=2)
        purchase.starts_at, purchase.expires_at = start, start + timedelta(days=1)
        purchase.save(update_fields=['starts_at', 'expires_at'])
        self.profile.pass_tier, self.profile.pass_expires_at = purchase.tier, purchase.expires_at
        self.profile.save(update_fields=['pass_tier', 'pass_expires_at'])
        with patch('users.pass_refund.ProviderPassRefund.objects.create', side_effect=RuntimeError('rollback')):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url + f'{purchase.pk}/refund/', {'client_request_id': 'points-refund-rollback'}, format='json')
        self.wallet.refresh_from_db(); self.points.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.points.points_balance), (820, 10000))
        self.assertFalse(ProviderPassRefund.objects.exists())
        self.assertFalse(PointLedger.objects.filter(kind='PASS_POINTS_RETURN').exists())

    @override_settings(PROVIDER_PASS_POINTS_PAYMENT_ENABLED=False)
    def test_disabled_feature_does_not_silently_ignore_quote_token(self):
        result = self.client.post(self.url, {'tier': 'BRONZE', 'points_quote_token': 'not-valid',
                                            'client_request_id': 'disabled-pass-points'}, format='json')
        self.assertEqual(result.status_code, 400)
        self.wallet.refresh_from_db(); self.assertEqual(self.wallet.balance, 1000)


@override_settings(PROVIDER_PASS_POINTS_PAYMENT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class PassPointsStoreTests(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_quotes_and_payments_use_only_the_bound_store(self):
        from rest_framework.test import APIClient
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        stores = [(resolve_store(), 'sl.esports.club', 10), (xinan_store(), 'xa.esports.club', 20)]
        results = []
        first_token = None
        for store, host, coin_units in stores:
            with store_context(store):
                user = make_provider(deposit_mode='EARNINGS')
                account = get_or_create_account_for_legacy_user(user)
                profile = user.escort_profile; profile.account = account; profile.save(update_fields=['account'])
                wallet = get_wallet(account=account, user=user); wallet.balance = 1000; wallet.save(update_fields=['balance'])
                MemberProfile.objects.create(account=account, points_balance=30000)
                config()
                save_points_terms(group_point_hundredths=10000, group_coin_units=coin_units,
                                  max_ratio_basis_points=5000, version=0, audit=lambda *_: None)
                client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                quote = client.post('/api/users/provider-pass/points-quote/', {'tier': 'BRONZE',
                                    'selected_point_hundredths': 20000}, format='json')
                self.assertEqual(quote.data['code'], 0, quote.data)
                token = quote.data['data']['quote_token']
                if first_token:
                    foreign = client.post('/api/users/provider-pass/', {'tier': 'BRONZE', 'days': 1,
                        'points_quote_token': first_token, 'client_request_id': 'foreign-store-token'}, format='json')
                    self.assertEqual(foreign.status_code, 400)
                else:
                    first_token = token
                response = client.post('/api/users/provider-pass/', {'tier': 'BRONZE', 'days': 1,
                    'points_quote_token': token, 'client_request_id': 'same-store-request'}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                wallet.refresh_from_db()
                results.append((wallet.balance, MemberProfile.objects.get(account=account).points_balance,
                                ProviderPassPurchase.objects.get(account=account).point_discount_amount))
        self.assertEqual(results, [(820, 10000, 20), (840, 10000, 40)])
