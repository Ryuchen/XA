from datetime import datetime, timezone as dt_timezone
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import Order, OrderTrafficConfig
from orders.traffic import atomic_count, current_mode, CONFIG_KEY
from orders.traffic_models import default_limits
from orders.tests.factories import make_user, make_provider, make_service, make_order, make_console_user
from wallet.services import get_wallet


class AtomicTrafficTest(SimpleTestCase):
    def test_atomic_counter_does_not_lose_parallel_updates(self):
        cache.clear()
        with ThreadPoolExecutor(max_workers=12) as pool:
            counts = list(pool.map(lambda _: atomic_count('traffic-race-test'), range(150)))
        self.assertEqual(sorted(counts), list(range(1, 151)))

    def test_shanghai_cross_midnight_and_end_boundary(self):
        config = {'peak_start': 22, 'peak_end': 2, 'valley_start': 3, 'valley_end': 8}
        for utc_hour, mode in [(15, 'peak'), (17, 'peak'), (18, 'normal'), (19, 'valley'), (0, 'normal')]:
            with patch('orders.traffic.timezone.now', return_value=datetime(2026, 10, 6, utc_hour, tzinfo=dt_timezone.utc)):
                self.assertEqual(current_mode(config), mode)


@override_settings(ORDER_TRAFFIC_ENABLED=True)
class OrderTrafficTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.boss = make_user()
        self.account = get_or_create_account_for_legacy_user(self.boss)
        self.service = make_service(price=1000)
        self.wallet = get_wallet(account=self.account, user=self.boss)
        self.wallet.balance = 100000
        self.wallet.save()
        limits = default_limits()
        for mode in limits.values():
            for action in mode.values():
                action.update(account_per_minute=2, global_per_minute=1000, global_per_second=1000)
        self.config = OrderTrafficConfig.objects.create(limits=limits)
        self.client.force_authenticate(self.boss)
        for name in ('_schedule_auto_cancel', 'enqueue_kook_dispatch', 'enqueue_wecom_dispatch', 'notify_order_update'):
            patcher = patch('orders.views.' + name); patcher.start(); self.addCleanup(patcher.stop)
        self.addCleanup(cache.clear)

    def create(self, ident='purchase-a', mode='AUTO_GRAB'):
        return self.client.post('/api/orders/create/', {'service_id': self.service.pk, 'expected_amount': 1000,
            'game_region': '微信', 'game_nickname': '老板', 'client_request_id': ident, 'dispatch_mode': mode}, format='json')

    def test_rate_limit_returns_wait_and_never_charges_blocked_request(self):
        self.assertEqual(self.create('a').data['code'], 0)
        self.assertEqual(self.create('b').data['code'], 0)
        response = self.create('c')
        self.assertEqual(response.status_code, 429, response.data)
        self.assertEqual(response.data['errorCode'], 'ORDER_RATE_LIMITED')
        self.assertGreater(response.data['retry_after'], 0)
        self.assertEqual(response['Retry-After'], str(response.data['retry_after']))
        self.wallet.refresh_from_db(); self.assertEqual(self.wallet.balance, 98000)
        self.assertEqual(Order.objects.count(), 2)
        # 取消不受下单流量闸门影响。
        self.assertEqual(self.client.post(f'/api/orders/orders/{Order.objects.first().pk}/cancel/').data['code'], 0)

    def test_different_accounts_same_ip_have_independent_quota_and_quote_is_independent(self):
        self.create('a'); self.create('b')
        self.assertEqual(self.client.post('/api/orders/quote/', {'service_id': self.service.pk}, format='json').data['code'], 0)
        second = make_user(); account = get_or_create_account_for_legacy_user(second)
        wallet = get_wallet(account=account, user=second); wallet.balance = 10000; wallet.save()
        self.client.force_authenticate(second)
        self.assertEqual(self.create('other').data['code'], 0)

    def test_global_gate_applies_across_accounts(self):
        limits = self.config.limits
        for mode in limits.values(): mode['quote']['global_per_minute'] = 1
        self.config.limits = limits; self.config.save(); cache.delete(CONFIG_KEY)
        self.assertEqual(self.client.post('/api/orders/quote/', {'service_id': self.service.pk}).data['code'], 0)
        self.client.force_authenticate(make_user())
        self.assertEqual(self.client.post('/api/orders/quote/', {'service_id': self.service.pk}).status_code, 429)

    def test_self_order_disabled_before_payment(self):
        self.config.self_order_enabled = False; self.config.save(); cache.delete(CONFIG_KEY)
        self.assertEqual(self.create().status_code, 403)
        self.wallet.refresh_from_db(); self.assertEqual(self.wallet.balance, 100000)
        self.assertFalse(Order.objects.exists())

    def test_cache_outage_rejects_before_debit(self):
        with patch('orders.traffic.cache.get', side_effect=ConnectionError()):
            self.assertEqual(self.create().status_code, 503)
        self.wallet.refresh_from_db(); self.assertEqual(self.wallet.balance, 100000)

    def test_new_dispatch_modes_and_duplicate_request_preserve_order(self):
        auto = self.create().data['data']
        self.assertFalse(auto['requires_boss_selection'])
        self.assertEqual(self.create().data['code'], 409)
        cache.clear()
        chosen = self.create('boss-choice', 'BOSS_SELECT').data['data']
        self.assertTrue(chosen['requires_boss_selection'])

    def test_provider_grab_limited_without_changing_other_orders(self):
        provider = make_provider(); self.client.force_authenticate(provider)
        for _ in range(2): self.client.post('/api/orders/orders/999999/grab/')
        order = make_order(self.boss, self.service, requires_boss_selection=False)
        response = self.client.post(f'/api/orders/orders/{order.pk}/grab/')
        self.assertEqual(response.status_code, 429)
        order.refresh_from_db(); self.assertEqual(order.status, Order.Status.PENDING)

    def test_config_permissions_overlap_and_stale_revision(self):
        viewer = make_console_user(['order:view']); self.client.force_authenticate(viewer)
        self.assertEqual(self.client.get('/api/admin/order-traffic/').status_code, 200)
        payload = {field: getattr(self.config, field) for field in ('revision','enabled','self_order_enabled','peak_start','peak_end','valley_start','valley_end','limits')}
        self.assertEqual(self.client.put('/api/admin/order-traffic/', payload, format='json').status_code, 403)
        editor = make_console_user(['order:view','order:traffic']); self.client.force_authenticate(editor)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.put('/api/admin/order-traffic/', payload, format='json').data['code'], 0)
        self.assertEqual(self.client.put('/api/admin/order-traffic/', payload, format='json').status_code, 409)
        payload['revision'] += 1; payload['valley_start'] = 19; payload['valley_end'] = 23
        self.assertEqual(self.client.put('/api/admin/order-traffic/', payload, format='json').status_code, 400)

    def test_second_burst_limit_is_global_and_resets_next_second(self):
        for mode in self.config.limits.values():
            mode['quote'].update(account_per_minute=1000, global_per_second=1)
        self.config.save(); cache.delete(CONFIG_KEY)
        with patch('orders.traffic.time.time', return_value=6000.1):
            self.assertEqual(self.client.post('/api/orders/quote/', {'service_id': self.service.pk}).data['code'], 0)
            blocked = self.client.post('/api/orders/quote/', {'service_id': self.service.pk})
            self.assertEqual(blocked.status_code, 429)
            self.assertEqual(blocked.data['retry_after'], 1)
        with patch('orders.traffic.time.time', return_value=6001.1):
            self.assertEqual(self.client.post('/api/orders/quote/', {'service_id': self.service.pk}).data['code'], 0)

    def test_account_limit_is_preserved_when_switching_profiles(self):
        self.create('a'); self.create('b')
        self.config.revision += 1; self.config.save(); cache.delete(CONFIG_KEY)
        self.assertEqual(self.create('c').status_code, 429)

    def test_monitoring_validation_permissions_and_aggregation(self):
        payload = {'samples': 10, 'failures': 2, 'average_ms': 180, 'jitter_ms': 60}
        self.assertEqual(self.client.post('/api/orders/network-report/', payload).status_code, 403)
        self.client.force_authenticate(make_provider())
        self.assertEqual(self.client.post('/api/orders/network-report/', payload).data['code'], 0)
        self.assertEqual(self.client.post('/api/orders/network-report/', {**payload, 'failures': 11}).status_code, 400)
        viewer = make_console_user(['order:view']); self.client.force_authenticate(viewer)
        stats = self.client.get('/api/admin/order-traffic/').data['data']['metrics']
        self.assertEqual(stats['average_latency_ms'], 180)
        self.assertEqual(stats['network_failure_percent'], 20)


from . import test_dispatch_concurrency as dispatch_concurrency


@override_settings(ORDER_TRAFFIC_ENABLED=True)
class TrafficDispatchConcurrencyTest(dispatch_concurrency.DispatchConcurrencyTest):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        super().setUp()
