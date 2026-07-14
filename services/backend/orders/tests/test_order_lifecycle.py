"""三端共用订单流转契约测试。"""

from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APITestCase

from orders.models import Order
from orders.tests.factories import make_order, make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile


class CrossEndOrderLifecycleTest(APITestCase):
    def setUp(self):
        self.customer = make_user()
        self.provider = make_provider()
        self.service = make_service(price=10000, commission_rate=20)
        make_wallet(self.customer, balance=50000)
        make_wallet(self.provider, balance=0)

    def _create_visible_order(self):
        self.client.force_authenticate(self.customer)
        response = self.client.post('/api/orders/create/', {'service_id': self.service.id})
        self.assertEqual(response.data['code'], 0)
        order = Order.objects.get(id=response.data['data']['id'])
        old = timezone.now() - timedelta(minutes=10)
        Order.objects.filter(id=order.id).update(created_at=old)
        order.refresh_from_db()
        return order

    @patch('orders.views._schedule_auto_cancel')
    def test_pending_grab_reject_grab_start_complete_contract(self, schedule_mock):
        order = self._create_visible_order()

        # 老板只能取消自己的已支付待接单订单。
        boss_list = self.client.get('/api/orders/orders/?status=pending')
        self.assertEqual(boss_list.data['data'][0]['allowed_actions'], ['CANCEL'])

        # 陪玩看到后端授权的抢单动作。
        self.client.force_authenticate(self.provider)
        pool = self.client.get('/api/orders/orders/?role=provider&status=pending')
        self.assertEqual(pool.data['data'][0]['allowed_actions'], ['GRAB'])

        grabbed = self.client.post(f'/api/orders/orders/{order.id}/grab/')
        self.assertEqual(grabbed.data['code'], 0)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.GRABBED)
        self.assertIsNone(order.auto_cancel_at)
        self.provider.escort_profile.refresh_from_db()
        self.assertEqual(self.provider.escort_profile.status, EscortProfile.Status.BUSY)

        accepted = self.client.get('/api/orders/orders/?status=grabbed')
        self.assertCountEqual(accepted.data['data'][0]['allowed_actions'], ['START', 'REJECT'])

        rejected = self.client.post(f'/api/orders/orders/{order.id}/reject/', {'reason': '档期冲突'})
        self.assertEqual(rejected.data['code'], 0)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertIsNone(order.provider_id)
        self.assertGreater(order.auto_cancel_at, timezone.now())
        schedule_mock.assert_called_with(order)

        # force_authenticate 会复用同一 User 实例，刷新其反向一对一缓存以模拟真实请求重新鉴权。
        self.provider.refresh_from_db()
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.id}/grab/').data['code'], 0)
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.id}/start/').data['code'], 0)
        in_service = self.client.get('/api/orders/orders/?status=in_progress')
        self.assertEqual(in_service.data['data'][0]['allowed_actions'], ['COMPLETE'])
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.id}/complete/').data['code'], 0)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.COMPLETED)
        self.provider.wallet.refresh_from_db()
        self.assertEqual(self.provider.wallet.balance, 8000)
        # 终态不能重复结算。
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.id}/complete/').data['code'], 400)
        self.provider.wallet.refresh_from_db()
        self.assertEqual(self.provider.wallet.balance, 8000)

        self.client.force_authenticate(self.customer)
        completed = self.client.get('/api/orders/orders/?status=completed')
        self.assertEqual(completed.data['data'][0]['allowed_actions'], ['EVALUATE'])

    def test_customer_cannot_spoof_provider_pool_with_query_parameter(self):
        mine = make_order(
            self.customer, service=self.service, status=Order.Status.PENDING,
            payment_status=Order.PaymentStatus.PAID,
        )
        other_customer = make_user()
        other = make_order(
            other_customer, service=self.service, status=Order.Status.PENDING,
            payment_status=Order.PaymentStatus.PAID,
        )
        self.client.force_authenticate(self.customer)
        response = self.client.get('/api/orders/orders/?role=provider&status=pending')
        ids = {row['id'] for row in response.data['data']}
        self.assertIn(mine.id, ids)
        self.assertNotIn(other.id, ids)

    def test_expired_pending_order_is_hidden_and_cannot_be_grabbed(self):
        order = make_order(
            self.customer,
            service=self.service,
            status=Order.Status.PENDING,
            payment_status=Order.PaymentStatus.PAID,
            auto_cancel_at=timezone.now() - timedelta(seconds=1),
        )
        Order.objects.filter(id=order.id).update(created_at=timezone.now() - timedelta(minutes=10))
        self.client.force_authenticate(self.provider)
        pool = self.client.get('/api/orders/orders/?role=provider&status=pending')
        self.assertNotIn(order.id, {row['id'] for row in pool.data['data']})
        grabbed = self.client.post(f'/api/orders/orders/{order.id}/grab/')
        self.assertEqual(grabbed.data['code'], 400)
        self.assertIn('超时', grabbed.data['msg'])

    def test_double_order_cannot_be_rejected_by_primary_provider(self):
        order = make_order(
            self.customer,
            service=self.service,
            provider=self.provider,
            status=Order.Status.GRABBED,
            payment_status=Order.PaymentStatus.PAID,
            escort_mode=Order.EscortMode.DOUBLE,
        )
        self.client.force_authenticate(self.provider)
        response = self.client.post(f'/api/orders/orders/{order.id}/reject/', {})
        self.assertEqual(response.data['code'], 400)
        self.assertIn('双陪', response.data['msg'])
