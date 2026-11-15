from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderProviderCompletionFact
from orders.tests import test_staff_team_dispatch as fixture
from orders.tests.factories import make_console_user
from wallet.models import Wallet


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=False, LEGACY_FORCE_AUTH_COMPAT=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ProviderCompletionRankingTest(APITestCase):
    auth = fixture.StaffTeamDispatchTest.auth
    def create_specified(self, providers):
        self.auth(self.boss)
        result = self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'provider_ids': [self.accounts[user.pk].pk for user in providers],
            'client_request_id': 'ranking-order'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        return Order.objects.get(pk=result.data['data']['id'])

    def setUp(self):
        fixture.StaffTeamDispatchTest.setUp(self)
        self.service.price = 5000
        self.service.save(update_fields=['price'])
        wallet = Wallet.objects.get(user=self.boss)
        wallet.balance = 0
        wallet.save(update_fields=['balance'])
        staff = make_console_user(['wallet:recharge'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account
        staff.admin_membership.save(update_fields=['account'])
        admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        quote = admin.get('/api/admin/wallets/recharge-quote/', {'amount': 20000})
        self.assertEqual(quote.data['code'], 0, quote.data)
        recharge = admin.post('/api/admin/wallets/recharge/', {'account_id': self.accounts[self.boss.pk].pk,
            'amount': 20000, 'gift_amount': 0, 'trade_no': 'ranking-recharge',
            'client_request_id': 'ranking-recharge', 'bonus_version': quote.data['data']['version']}, format='json')
        self.assertEqual(recharge.data['code'], 0, recharge.data)

    def rows(self):
        self.auth(self.boss)
        result = self.client.get('/api/orders/rankings/?period=day')
        self.assertEqual(result.status_code, 200, result.data)
        return {row['provider_account_id']: row for row in result.data['data']['order_rank']}

    def test_team_first_completion_has_personal_day_and_reupload_does_not_move_it(self):
        now = timezone.now()
        yesterday = now - timedelta(days=1)
        with patch('django.utils.timezone.now', return_value=yesterday):
            order = self.create_specified(self.providers[:2])
            for provider in self.providers[:2]:
                self.auth(provider)
                self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/start/',
                    {'entry_image': fixture.screenshot()}, format='multipart').data['code'], 0)
            self.auth(self.providers[0])
            self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/complete/',
                {'completion_image': fixture.screenshot()}, format='multipart').data['code'], 0)
            self.assertEqual(self.rows()[self.accounts[self.providers[0].pk].pk]['order_count'], 1)
        with patch('django.utils.timezone.now', return_value=now):
            self.auth(self.providers[0])
            self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/complete/',
                {'completion_image': fixture.screenshot()}, format='multipart').data['code'], 0)
            self.assertEqual(OrderProviderCompletionFact.objects.get(order=order,
                provider_account=self.accounts[self.providers[0].pk]).completed_at, yesterday)
            self.auth(self.providers[1])
            self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/complete/',
                {'completion_image': fixture.screenshot()}, format='multipart').data['code'], 0)
            rows = self.rows()
            self.assertNotIn(self.accounts[self.providers[0].pk].pk, rows)
            self.assertEqual(rows[self.accounts[self.providers[1].pk].pk]['order_count'], 1)
            response = self.client.get('/api/orders/rankings/?period=day')
            self.assertTrue(response.data['data']['order_rank_complete'])

    def test_single_real_completion_counts_without_original_attribution(self):
        self.service.billing_provider_count = 1
        self.service.save(update_fields=['billing_provider_count'])
        order = self.create_specified(self.providers[:1])
        self.auth(self.providers[0])
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image': fixture.screenshot()}, format='multipart').data['code'], 0)
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/complete/',
            {'completion_image': fixture.screenshot()}, format='multipart').data['code'], 0)
        self.assertEqual(self.rows()[self.accounts[self.providers[0].pk].pk]['order_count'], 1)

    @override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True)
    def test_team_personal_completion_uses_participation_segment(self):
        self.test_team_first_completion_has_personal_day_and_reupload_does_not_move_it()
        self.assertEqual(OrderProviderCompletionFact.objects.filter(segment__isnull=False).count(), 2)

    def test_missing_personal_fact_reports_incomplete(self):
        self.test_team_first_completion_has_personal_day_and_reupload_does_not_move_it()
        OrderProviderCompletionFact.objects.filter(provider_account=self.accounts[self.providers[1].pk]).delete()
        from orders.models import OrderWorkStint
        OrderWorkStint.objects.filter(account=self.accounts[self.providers[1].pk]).update(completed_at=None)
        response = self.client.get('/api/orders/rankings/?period=day')
        self.assertFalse(response.data['data']['order_rank_complete'])


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=False, LEGACY_FORCE_AUTH_COMPAT=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   ALLOWED_HOSTS=['*'])
class ProviderCompletionRankingStoresTest(APITestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    auth = ProviderCompletionRankingTest.auth
    create_specified = ProviderCompletionRankingTest.create_specified
    rows = ProviderCompletionRankingTest.rows

    def test_both_stores_keep_personal_completion_dates(self):
        if 'store_b' not in settings.DATABASES:
            self.skipTest('requires two isolated stores')
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        for store in (resolve_store(), xinan_store()):
            with self.subTest(store=store.slug), store_context(store):
                ProviderCompletionRankingTest.setUp(self)
                self.client.defaults['HTTP_HOST'] = 'xa.esports.club' if store.slug == 'xinan' else 'sl.esports.club'
                ProviderCompletionRankingTest.test_team_first_completion_has_personal_day_and_reupload_does_not_move_it(self)
