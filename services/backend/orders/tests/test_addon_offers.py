from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import (GameCategory, Order, OrderOriginalAttribution, OrderParticipationSegment,
                           OrderProvider, OrderSegmentFinancialSnapshot, ServiceAddonConfiguration,
                           ServiceAddonPriceOption)
from orders.tests.factories import make_order, make_provider, make_service, make_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store


def authenticated(user, host='sl.esports.club'):
    account = get_or_create_account_for_legacy_user(user)
    return account, APIClient(HTTP_HOST=host,
                              HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class OrderAddonOffersTest(APITestCase):
    def setUp(self):
        self.boss_user = make_user()
        self.boss, self.client = authenticated(self.boss_user)
        self.other, self.other_client = authenticated(make_user())
        self.provider_user = make_provider()
        self.provider, self.provider_client = authenticated(self.provider_user)
        game = GameCategory.objects.create(name='瓦罗兰特')
        self.service = make_service(game_category=game)
        self.addon = make_service(name='1 陪 2', game_category=game, is_addon=True)
        self.order = make_order(self.boss_user, self.service, customer_account=self.boss,
                                status=Order.Status.IN_SERVICE, payment_status=Order.PaymentStatus.PAID)
        OrderProvider.objects.create(order=self.order, provider=self.provider_user, provider_account=self.provider,
                                     settlement_base=10000, commission_rate=20, provider_income=8000)
        attribution = OrderOriginalAttribution.objects.create(order=self.order, original_amount=10000, status='ACTIVE')
        self.segment = OrderParticipationSegment.objects.create(attribution=attribution, provider_account=self.provider,
            slot=1, original_share=10000, accepted_at=timezone.now(), started_at=timezone.now())
        OrderSegmentFinancialSnapshot.objects.create(segment=self.segment, incoming_basis=10000,
            commission_type='PERCENT', commission_rate=20, commission_fixed=0, income_at_entry=8000)
        self.config = ServiceAddonConfiguration.objects.create(service=self.service, addon=self.addon,
                    enabled=True, conditions='按小时加收', version=2)
        ServiceAddonPriceOption.objects.create(configuration=self.config, unit='HOUR', price=500)
        ServiceAddonPriceOption.objects.create(configuration=self.config, unit='ROUND', price=400, enabled=False)
        self.url = f'/api/orders/orders/{self.order.pk}/addons/'
        self.quote_url = self.url + 'quote/'
        self.quote = {'addon_id': self.addon.pk, 'unit': 'HOUR', 'quantity': 3,
                      'configuration_version': 2, 'provider_account_id': self.provider.pk}

    def test_owner_sees_only_active_configured_options_without_false_purchase_claim(self):
        result = self.client.get(self.url)
        self.assertEqual(result.status_code, 200)
        data = result.data['data']
        self.assertEqual(data['items'], [{'addon_id': self.addon.pk, 'name': '1 陪 2',
            'conditions': '按小时加收', 'condition_mode': 'STAFF_REVIEW', 'configuration_version': 2,
            'options': [{'unit': 'HOUR', 'unit_price_raw': 500}]}])
        self.assertFalse(data['purchase_available'])
        self.assertEqual(data['reason'], 'PURCHASE_NOT_AVAILABLE')

    def test_other_boss_and_provider_cannot_read_the_order(self):
        self.assertEqual(self.other_client.get(self.url).status_code, 404)
        self.assertEqual(self.provider_client.get(self.url).status_code, 404)

    def test_inactive_or_incompatible_catalog_and_unconfigured_price_not_offered(self):
        self.addon.is_active = False
        self.addon.save(update_fields=['is_active'])
        self.assertEqual(self.client.get(self.url).data['data']['items'], [])
        self.addon.is_active = True
        self.addon.game_category = GameCategory.objects.create(name='其他游戏')
        self.addon.save(update_fields=['is_active', 'game_category'])
        self.assertEqual(self.client.get(self.url).data['data']['items'], [])
        self.addon.game_category = self.service.game_category
        self.addon.save(update_fields=['game_category'])
        self.config.price_options.update(enabled=False)
        self.assertEqual(self.client.get(self.url).data['data']['items'], [])

    def test_orders_not_in_service_or_paid_cannot_expose_available_offers(self):
        for status, payment in [(Order.Status.COMPLETED, Order.PaymentStatus.PAID),
                                (Order.Status.IN_SERVICE, Order.PaymentStatus.REFUNDED)]:
            Order.objects.filter(pk=self.order.pk).update(status=status, payment_status=payment)
            data = self.client.get(self.url).data['data']
            self.assertEqual(data['items'], [])
            self.assertEqual(data['reason'], 'ORDER_NOT_ELIGIBLE')

    def test_partially_refunded_service_can_still_show_its_configured_items(self):
        Order.objects.filter(pk=self.order.pk).update(payment_status=Order.PaymentStatus.PARTIALLY_REFUNDED)
        self.assertEqual(len(self.client.get(self.url).data['data']['items']), 1)

    def test_quote_uses_store_price_and_current_segment_without_charging(self):
        from wallet.models import Transaction
        response = self.client.post(self.quote_url, self.quote, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data['data']
        self.assertEqual((data['amount_raw'], data['segment_id'], data['condition_mode']),
                         (1500, self.segment.pk, 'STAFF_REVIEW'))
        self.assertFalse(data['purchase_available'])
        self.assertFalse(Transaction.objects.filter(order=self.order).exists())

    def test_double_order_allows_boss_to_choose_either_current_player(self):
        self.segment.original_share = 5000
        self.segment.save(update_fields=['original_share'])
        OrderProvider.objects.filter(order=self.order, provider_account=self.provider).update(
            settlement_base=5000, provider_income=4000)
        OrderSegmentFinancialSnapshot.objects.filter(segment=self.segment).update(incoming_basis=5000, income_at_entry=4000)
        second_user = make_provider()
        second, _ = authenticated(second_user)
        OrderProvider.objects.create(order=self.order, provider=second_user, provider_account=second,
                                     settlement_base=5000, commission_rate=25, provider_income=3750)
        segment = OrderParticipationSegment.objects.create(attribution=self.segment.attribution,
            provider_account=second, slot=2, original_share=5000,
            accepted_at=timezone.now(), started_at=timezone.now())
        OrderSegmentFinancialSnapshot.objects.create(segment=segment, incoming_basis=5000,
            commission_type='PERCENT', commission_rate=25, commission_fixed=0, income_at_entry=3750)
        self.order.escort_mode = Order.EscortMode.DOUBLE
        self.order.save(update_fields=['escort_mode'])
        first = self.client.post(self.quote_url, self.quote, format='json')
        other = self.client.post(self.quote_url, {**self.quote, 'provider_account_id': second.pk}, format='json')
        self.assertEqual(first.data['data']['segment_id'], self.segment.pk)
        self.assertEqual(other.data['data']['segment_id'], segment.pk)

    def test_quote_rejects_fractional_quantity_stale_config_and_noncurrent_provider(self):
        for changes in ({'quantity': 1.5}, {'quantity': True}, {'quantity': 0},
                        {'configuration_version': 1}, {'provider_account_id': self.other.pk},
                        {'unit': 'ROUND'}, {'coupon_id': 1}):
            response = self.client.post(self.quote_url, {**self.quote, **changes}, format='json')
            self.assertNotEqual(response.status_code, 200, response.data)
        self.segment.released_at = timezone.now()
        self.segment.save(update_fields=['released_at'])
        self.assertEqual(self.client.post(self.quote_url, self.quote, format='json').status_code, 409)
        self.assertEqual(self.other_client.post(self.quote_url, self.quote, format='json').status_code, 404)
from unittest import skipUnless

from django.conf import settings


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class OrderAddonOffersStoresTest(APITestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_order_and_addon_ids_return_only_own_store_configuration(self):
        rows = []
        for store, host, price in ((resolve_store(), 'sl.esports.club', 500),
                                   (xinan_store(), 'xa.esports.club', 900)):
            with store_context(store):
                user = make_user()
                account, client = authenticated(user, host)
                game = GameCategory.objects.create(name='本店游戏')
                service = make_service(game_category=game)
                addon = make_service(game_category=game, is_addon=True)
                order = make_order(user, service, customer_account=account, status=Order.Status.IN_SERVICE)
                config = ServiceAddonConfiguration.objects.create(service=service, addon=addon, enabled=True)
                ServiceAddonPriceOption.objects.create(configuration=config, unit='HOUR', price=price)
                rows.append((client, order.pk, addon.pk))
        self.assertEqual(rows[0][1:], rows[1][1:])
        for (client, order_id, _), price in zip(rows, (500, 900)):
            response = client.get(f'/api/orders/orders/{order_id}/addons/')
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['data']['items'][0]['options'][0]['unit_price_raw'], price)
        rows[0][0].defaults['HTTP_HOST'] = 'xa.esports.club'
        self.assertNotEqual(rows[0][0].get(f'/api/orders/orders/{rows[0][1]}/addons/').status_code, 200)
