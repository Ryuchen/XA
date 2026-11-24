"""A returning checkout reads current service configuration before purchase."""
import os
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from engagement.models import PointFundingLot
from orders.models import GameCategory, Gameplay, Order, ServiceItem
from orders.reservation_views import reservation_service_terms
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from users.models import ProviderQualification


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class NativeCheckoutServiceRefreshTest(LiveServerTestCase):
    def _gate(self):
        if os.environ.get('XA_CHECKOUT_SERVICE_REFRESH') != '1':
            self.skipTest('Requires isolated checkout acceptance (env gate)')

    def _fund(self, account, balance, key):
        PointFundingLot.objects.create(account=account, source_key=key, kind='PREPAID', remaining=balance)

    def _provider(self, name, service):
        provider = make_provider(display_name=name, deposit_mode='EARNINGS', is_verified=True)
        provider_account = get_or_create_account_for_legacy_user(provider)
        link_legacy_relations(provider, provider_account)
        provider.escort_profile.account = provider_account
        provider.escort_profile.save(update_fields=['account'])
        provider.escort_profile.service_items.add(service)
        return provider, provider_account

    def _quote(self, client, payload):
        response = client.post('/api/orders/quote/', payload, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        data = response.data['data']
        data['service'] = reservation_service_terms(ServiceItem.objects.get(pk=payload['service_id']))
        return data

    def _create(self, client, payload, quote):
        return client.post('/api/orders/create/', {**payload,
            'expected_amount': quote['amount'], 'expected_service': quote['service']}, format='json')

    def _confirm_after_change(self, client, payload, mutate, race, payload_after=None, expect_stale_reject=True):
        if race:
            quote_old = self._quote(client, payload)
            mutate()
            payload = payload_after or payload
            if expect_stale_reject:
                stale = self._create(client, payload, quote_old)
                self.assertNotEqual(stale.data['code'], 0, stale.data)
            quote = self._quote(client, payload)
        else:
            mutate()
            payload = payload_after or payload
            quote = self._quote(client, payload)
        response = self._create(client, payload, quote)
        self.assertEqual(response.data['code'], 0, response.data)
        return response

    def test_double_checkout_confirms_selected_people_before_real_order(self):
        self._gate()
        boss = make_user(); account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account); make_wallet(boss, balance=100000)
        self._fund(account, 100000, 'checkout-double')
        service = make_service(name='隔离双陪结算', price=10000, billing_provider_count=2, fulfillment_provider_count=2)
        accounts = [self._provider(name, service)[1] for name in ('隔离陪玩甲', '隔离陪玩乙')]
        token, refresh = issue_account_tokens(account)
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        payload = {'service_id': service.pk, 'provider_ids': [item.pk for item in accounts],
            'client_request_id': 'checkout-confirm-team'}
        self._confirm_after_change(client, payload, lambda: None, False)
        order = Order.objects.get(customer_account=account)
        self.assertEqual(set(order.providers.values_list('provider_account_id', flat=True)), {item.pk for item in accounts})

    def _difficulty_change_requires_reconfirmation(self, race, change_grade=False):
        cache.clear()
        self.addCleanup(cache.clear)
        self._gate()
        boss, staff = make_user(), make_console_user(['service:view', 'service:edit'])
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        admin = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = admin
        staff.admin_membership.save(update_fields=['account'])
        wallet = make_wallet(boss, balance=50000)
        self._fund(account, 50000, 'checkout-difficulty')
        game = GameCategory.objects.create(name='隔离难度变更游戏')
        gameplay = Gameplay.objects.create(name='隔离难度变更玩法', code='checkout-difficulty-change')
        service = make_service(price=10000, game_category=game, gameplay=gameplay,
            provider_kind='GUARD', provider_grade='MASTER', platform='PC', security_level='CONFIDENTIAL')
        provider, provider_account = self._provider('隔离难度陪玩', service)
        token, refresh = issue_account_tokens(account)
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        security = 'CONFIDENTIAL' if change_grade else 'TOP_SECRET'
        grade = 'LEGEND' if change_grade else 'MASTER'
        provider.escort_profile.game_categories.add(game)
        ProviderQualification.objects.create(profile=provider.escort_profile, game_category=game,
            provider_kind='GUARD', platform='PC', security_level=security, grade=grade)

        def mutate():
            service.security_level = 'TOP_SECRET'
            if change_grade:
                service.provider_grade = 'LEGEND'
            service.save()
            cache.clear()

        payload = {'service_id': service.pk, 'game_rounds': 1,
            'client_request_id': 'checkout-difficulty-change'}
        self._confirm_after_change(client, payload, mutate, race, expect_stale_reject=False)
        order = Order.objects.get(customer_account=account)
        self.assertEqual((order.amount, order.gameplay_snapshot['security_level']), (10000, security))
        self.assertEqual((order.provider_requirement_snapshot['security_level'], order.provider_requirement_snapshot['grade']), (security, grade))
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 40000)
        provider = make_provider(deposit_mode='EARNINGS')
        provider_account = get_or_create_account_for_legacy_user(provider)
        profile = provider.escort_profile
        profile.account = provider_account
        profile.save(update_fields=['account'])
        profile.service_items.add(service)
        profile.game_categories.add(game)
        ProviderQualification.objects.create(profile=profile, game_category=game,
            provider_kind='GUARD', platform='PC', security_level=security, grade=grade)
        provider_client = APIClient()
        provider_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(provider_account)[0])
        # Exercise the real pass delay; only the test clock is advanced.
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(seconds=profile.order_visibility_delay_seconds + 1)):
            response = provider_client.post(f'/api/orders/orders/{order.pk}/grab/', {}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)

    def test_foreground_difficulty_change_requires_reconfirmation(self):
        self._difficulty_change_requires_reconfirmation(False)

    def test_difficulty_change_during_confirmation_rejected_before_debit(self):
        self._difficulty_change_requires_reconfirmation(True)

    def test_grade_change_requires_reconfirmation(self):
        self._difficulty_change_requires_reconfirmation(False, change_grade=True)

    def _package_change(self, race):
        cache.clear()
        self.addCleanup(cache.clear)
        self._gate()
        boss = make_user()
        staff = make_console_user(['service:view', 'service:edit'])
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        admin = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = admin
        staff.admin_membership.save(update_fields=['account'])
        wallet = make_wallet(boss, balance=50000)
        self._fund(account, 50000, 'checkout-package')
        service = make_service(price=10000)
        accounts = [self._provider(name, service)[1] for name in ('隔离陪玩甲', '隔离陪玩乙')]
        token, refresh = issue_account_tokens(account)
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

        def mutate():
            service.price = 5000
            service.billing_provider_count = 2
            service.fulfillment_provider_count = 2
            service.save()

        payload = {'service_id': service.pk, 'provider_id': accounts[0].pk,
            'client_request_id': 'checkout-package-change'}
        payload_after = {'service_id': service.pk, 'provider_ids': [item.pk for item in accounts],
            'client_request_id': 'checkout-package-change'}
        self._confirm_after_change(client, payload, mutate, race, payload_after=payload_after)
        self.assertEqual(Order.objects.filter(customer_account=account).count(), 1)
        order = Order.objects.get(customer_account=account)
        self.assertEqual((order.amount, order.required_provider_count), (10000, 2))
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 40000)

    def test_package_change_during_confirmation_is_rejected_before_debit(self):
        self._package_change(True)

    def test_foreground_package_change_at_same_total_requires_review(self):
        self._package_change(False)

    def test_admin_changes_price_and_package_while_checkout_is_backgrounded(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self._gate()
        boss, staff = make_user(), make_console_user(['service:view', 'service:edit', 'order:view', 'order:traffic'])
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        admin = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = admin
        staff.admin_membership.save(update_fields=['account'])
        make_wallet(boss, balance=50000)
        self._fund(account, 50000, 'checkout-backgrounded')
        service = make_service(price=10000)
        accounts = [self._provider(name, service)[1] for name in ('隔离陪玩甲', '隔离陪玩乙')]
        token, refresh = issue_account_tokens(account)
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

        def mutate():
            service.price = 12000
            service.billing_provider_count = 2
            service.fulfillment_provider_count = 2
            service.save()

        payload = {'service_id': service.pk, 'provider_id': accounts[0].pk, 'game_rounds': 1,
            'game_region': '本地大区', 'game_nickname': '本地昵称',
            'client_request_id': 'checkout-backgrounded-change'}
        payload_after = {'service_id': service.pk, 'provider_ids': [item.pk for item in accounts], 'game_rounds': 1,
            'game_region': '本地大区', 'game_nickname': '本地昵称',
            'client_request_id': 'checkout-backgrounded-change'}
        self._confirm_after_change(client, payload, mutate, True, payload_after=payload_after)
        order = Order.objects.get(customer_account=account)
        self.assertEqual((order.amount, order.game_rounds, order.game_region, order.game_nickname),
                         (24000, 1, '本地大区', '本地昵称'))
        self.assertEqual(order.required_provider_count, 2)