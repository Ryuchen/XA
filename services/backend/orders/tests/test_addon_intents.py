from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from rest_framework.test import APITestCase

from console.models import AdminAuditLog
from orders.models import OrderAddonIntent, OrderSegmentFinancialCoverage, OrderSegmentFinancialSnapshot
from orders.tests import test_addon_offers as offers_fixture
from orders.tests.test_addon_configuration import staff_client
from wallet.models import Transaction
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class AddonIntentTests(APITestCase):
    def setUp(self):
        offers_fixture.OrderAddonOffersTest.setUp(self)
        self.coverage = OrderSegmentFinancialCoverage.objects.create(
            attribution=self.segment.attribution, deal_amount=10000, source='WALLET_NO_POINTS', status='ACTIVE')
        self.url = self.url + 'intents/'
        self.staff_url = f'/api/admin/orders/{self.order.pk}/addons/intents/'
        self.staff = staff_client(['order:dispatch'])
        self.reader = staff_client(['order:view'])
        self.denied = staff_client(['service:view'])
        self.payload = {**self.quote, 'request_id':'addon-intent-1', 'expected_amount':1500,
                        'gift_balance_amount':200, 'compensation_balance_amount':100}

    def test_boss_intent_records_sources_snapshot_and_idempotency_without_payment(self):
        self.config.condition_mode = 'NONE'; self.config.conditions = ''
        self.config.save(update_fields=['condition_mode', 'conditions'])
        response = self.client.post(self.url, self.payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data['data']
        self.assertFalse(data['paid']); self.assertFalse(data['payment_available'])
        self.assertEqual(data['source_raw'], {'principal':1200, 'gift':200, 'compensation':100})
        self.assertEqual(self.client.post(self.url, self.payload, format='json').data['data']['id'], data['id'])
        self.assertEqual(self.client.post(self.url, {**self.payload, 'quantity':2}, format='json').status_code, 409)
        row = OrderAddonIntent.objects.get()
        self.assertEqual((row.commission_snapshot['type'], row.commission_snapshot['rate']), ('PERCENT', 20))
        self.assertFalse(Transaction.objects.filter(order=self.order).exists())
        self.assertEqual(self.client.get(self.url).data['data']['items'][0]['id'], row.pk)
        self.assertEqual(self.other_client.get(self.url).status_code, 404)

    def test_staff_review_requires_explicit_confirmation_and_audit(self):
        self.assertEqual(self.client.post(self.url, self.payload, format='json').status_code, 409)
        payload = {**self.payload, 'confirmation_method':'电话', 'confirmation_content':'老板确认三小时及钱包来源',
                   'review_note':'客服已核实玩法条件'}
        self.assertEqual(self.denied.post(self.staff_url, payload, format='json').status_code, 403)
        self.assertEqual(self.staff.post(self.staff_url, {**payload, 'confirmation_method':''}, format='json').status_code, 400)
        response = self.staff.post(self.staff_url, payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        row = OrderAddonIntent.objects.get()
        self.assertEqual(row.operator_id, response.wsgi_request.account.pk)
        self.assertEqual(row.confirmation['content'], payload['confirmation_content'])
        self.assertTrue(row.confirmation['recorded_at'])
        self.assertEqual(AdminAuditLog.objects.filter(resource='order-addon-intent').count(), 1)
        self.assertEqual(self.staff.post(self.staff_url, payload, format='json').data['data']['id'], row.pk)
        self.assertEqual(AdminAuditLog.objects.filter(resource='order-addon-intent').count(), 1)
        self.assertEqual(self.reader.get(self.staff_url).data['data']['items'][0]['id'], row.pk)
        self.assertEqual(self.denied.get(self.staff_url).status_code, 403)
        self.assertFalse(Transaction.objects.filter(order=self.order).exists())

    def test_stale_selection_invalid_sources_and_corrupt_commission_rejected(self):
        self.config.condition_mode = 'NONE'; self.config.conditions = ''
        self.config.save(update_fields=['condition_mode', 'conditions'])
        for changes in ({'expected_amount':1400}, {'quantity':1.5}, {'gift_balance_amount':1510},
                        {'provider_account_id':self.other.pk}, {'configuration_version':1},
                        {'gift_balance_amount':1}, {'coupon_id':1}):
            response = self.client.post(self.url, {**self.payload, **changes}, format='json')
            self.assertNotEqual(response.status_code, 200, response.data)
        OrderSegmentFinancialSnapshot.objects.filter(segment=self.segment).update(income_at_entry=1)
        self.assertEqual(self.client.post(self.url, self.payload, format='json').status_code, 409)
        self.assertFalse(OrderAddonIntent.objects.exists())

    def test_fixed_commission_preserves_rule_and_main_deal_basis(self):
        self.config.condition_mode = 'NONE'; self.config.conditions = ''
        self.config.save(update_fields=['condition_mode', 'conditions'])
        OrderSegmentFinancialSnapshot.objects.filter(segment=self.segment).update(
            commission_type='FIXED', commission_rate=0, commission_fixed=2000, fixed_rule_total=2000)
        result = self.client.post(self.url, self.payload, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        snapshot = OrderAddonIntent.objects.get().commission_snapshot
        self.assertEqual((snapshot['type'], snapshot['fixed_rule_total'], snapshot['order_deal_amount']),
                         ('FIXED', 2000, 10000))

    def test_staff_audit_failure_rolls_back_intent(self):
        payload = {**self.payload, 'confirmation_method':'电话', 'confirmation_content':'老板确认加购内容及余额',
                   'review_note':'客服已核实'}
        with patch('console.models.AdminAuditLog.objects.create', side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):
                self.staff.post(self.staff_url, payload, format='json')
        self.assertFalse(OrderAddonIntent.objects.exists())
        self.assertFalse(Transaction.objects.filter(order=self.order).exists())


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class AddonIntentRaceTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_identical_intents_create_one_record(self):
        fixture = AddonIntentTests(methodName='test_boss_intent_records_sources_snapshot_and_idempotency_without_payment')
        fixture.setUp()
        fixture.config.condition_mode = 'NONE'; fixture.config.conditions = ''
        fixture.config.save(update_fields=['condition_mode', 'conditions'])
        barrier = Barrier(2)
        token = fixture.client.defaults['HTTP_AUTHORIZATION']

        def post():
            from rest_framework.test import APIClient
            close_old_connections()
            client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION=token)
            barrier.wait(timeout=10)
            response = client.post(fixture.url, fixture.payload, format='json')
            close_old_connections()
            return response.status_code, response.data

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: post(), range(2)))
        self.assertEqual([status for status, _ in results], [200, 200])
        self.assertEqual(results[0][1]['data']['id'], results[1][1]['data']['id'])
        self.assertEqual(OrderAddonIntent.objects.count(), 1)
        self.assertFalse(Transaction.objects.filter(order=fixture.order).exists())


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class AddonIntentStoresTests(APITestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_identical_ids_and_request_ids_do_not_cross_stores(self):
        stores = (resolve_store(), xinan_store())
        fixtures = []
        for index, store in enumerate(stores):
            with store_context(store):
                fixture = AddonIntentTests(methodName='test_boss_intent_records_sources_snapshot_and_idempotency_without_payment')
                fixture.setUp()
                fixture.config.condition_mode = 'NONE'; fixture.config.conditions = ''
                fixture.config.save(update_fields=['condition_mode', 'conditions'])
                if index:
                    fixture.client.defaults['HTTP_HOST'] = 'xa.esports.club'
                fixtures.append(fixture)
        self.assertEqual(fixtures[0].order.pk, fixtures[1].order.pk)
        self.assertEqual(fixtures[0].boss.pk, fixtures[1].boss.pk)
        response = fixtures[0].client.post(fixtures[0].url, fixtures[0].payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        with store_context(stores[1]):
            self.assertFalse(OrderAddonIntent.objects.exists())
        response = fixtures[1].client.post(fixtures[1].url, fixtures[1].payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        for store in stores:
            with store_context(store):
                self.assertEqual(OrderAddonIntent.objects.count(), 1)
                self.assertFalse(Transaction.objects.exists())
