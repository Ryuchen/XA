from types import SimpleNamespace
import os
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.direct_payment import VerifiedPayment, confirm_verified_payment, prepare_direct_order
from orders.models import DirectPaymentIntent
from orders.tests.factories import make_order, make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True)
class PaymentPoolContractTests(TestCase):
    def setUp(self):
        self.boss = make_user()
        self.account = get_or_create_account_for_legacy_user(self.boss)
        self.account.openid, self.account.wechat_appid, self.account.is_openid_bound = 'test-openid', 'wx-test', True
        self.account.save(update_fields=['openid', 'wechat_appid', 'is_openid_bound'])
        make_wallet(self.boss, balance=50000)
        service = make_service(price=10000)
        request = SimpleNamespace(account=self.account, legacy_user=self.boss, data={
            'service_id': service.pk, 'expected_amount': 10000, 'client_request_id': 'pool-contract-1',
        })
        self.intent = prepare_direct_order(request, appid='wx-test', mchid='mch-test')
        evidence = VerifiedPayment(self.intent.merchant_order_no, 'pool-contract-transaction', 'wx-test', 'mch-test',
                                   'test-openid', 10000, 'CNY', timezone.now(), payer_total_fen=10000,
                                   payer_currency='CNY')
        with patch('orders.direct_payment._notify_paid'):
            confirm_verified_payment(self.intent.order_id, evidence)
        self.legacy = make_order(self.boss, service, customer_account=self.account, payment_status='PAID')
        provider = make_provider()
        provider_account = get_or_create_account_for_legacy_user(provider)
        EscortProfile.objects.filter(user=provider).update(account=provider_account)
        self.client = APIClient()
        token, _ = issue_account_tokens(provider_account)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def visible(self):
        with patch.object(EscortProfile, 'order_visibility_delay_seconds', new_callable=lambda: property(lambda _: 0)):
            response = self.client.get('/api/orders/orders/?status=pending')
        self.assertEqual(response.data['code'], 0)
        return {item['id'] for item in response.data['data']}

    def test_unknown_version_stays_out_of_sql_filtered_pool(self):
        self.assertIn(self.intent.order_id, self.visible())
        self.assertIn(self.legacy.pk, self.visible())
        self.intent.fee_quote.contract_version = 'FUTURE_CONTRACT'
        self.intent.fee_quote.save(update_fields=['contract_version'])
        self.assertNotIn(self.intent.order_id, self.visible())
        self.assertIn(self.legacy.pk, self.visible())

    def test_non_test_environment_excludes_paid_simulation_from_pool(self):
        self.assertIn(self.intent.order_id, self.visible())
        with patch.dict(os.environ, {'DJANGO_SETTINGS_MODULE': 'config.settings'}):
            visible = self.visible()
        self.assertNotIn(self.intent.order_id, visible)
        self.assertIn(self.legacy.pk, visible)

    def test_mismatched_amount_snapshots_stay_out_of_pool(self):
        quote = self.intent.fee_quote
        quote.service_units += 1
        quote.save(update_fields=['service_units'])
        self.assertNotIn(self.intent.order_id, self.visible())
        quote.service_units -= 1
        quote.save(update_fields=['service_units'])
        DirectPaymentIntent.objects.filter(pk=self.intent.pk).update(amount_fen=10001)
        self.assertNotIn(self.intent.order_id, self.visible())
        self.assertIn(self.legacy.pk, self.visible())

    def test_invalid_hash_cannot_offer_grab_while_valid_and_balance_orders_can(self):
        def pool():
            with patch.object(EscortProfile, 'order_visibility_delay_seconds', new_callable=lambda: property(lambda _: 0)):
                response = self.client.get('/api/orders/orders/?status=pending')
            self.assertEqual(response.data['code'], 0)
            return {item['id']: item for item in response.data['data']}

        before = pool()
        self.assertIn('GRAB', before[self.intent.order_id]['allowed_actions'])
        self.assertIn('GRAB', before[self.legacy.pk]['allowed_actions'])
        quote = self.intent.fee_quote
        quote.quote_hash = '0' * 64 if quote.quote_hash != '0' * 64 else '1' * 64
        quote.save(update_fields=['quote_hash'])
        after = pool()
        # The coarse SQL list still contains this row; this regression only
        # ensures that it cannot offer a grab/apply button.
        self.assertIn(self.intent.order_id, after)
        self.assertNotIn('GRAB', after[self.intent.order_id]['allowed_actions'])
        self.assertIn('GRAB', after[self.legacy.pk]['allowed_actions'])
