from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APITestCase

from coupons.models import Coupon, UserCoupon
from orders.models import Order, OrderProvider, OrderStatusLog
from orders.tests.factories import (
    make_evaluation,
    make_order,
    make_provider,
    make_service,
    make_user,
    make_wallet,
)
from wallet.models import Transaction
from users.models import EscortProfile


class SelectedProviderOrderFlowTest(APITestCase):
    def setUp(self):
        self.customer = make_user()
        self.provider = make_provider()
        self.service = make_service(price=10000, commission_rate=20)
        make_wallet(self.customer, balance=50000)
        self.client.force_authenticate(self.customer)

    def test_selected_provider_order_is_assigned_immediately(self):
        response = self.client.post('/api/orders/create/', {
            'service_id': self.service.id,
            'provider_id': self.provider.id,
        })
        self.assertEqual(response.data['code'], 0)
        order = Order.objects.get(id=response.data['data']['id'])
        self.assertEqual(order.status, Order.Status.GRABBED)
        self.assertEqual(order.provider_id, self.provider.id)
        self.assertIsNone(order.auto_cancel_at)
        self.assertTrue(order.status_logs.filter(
            action=OrderStatusLog.Action.ASSIGN,
            reason='老板指定陪玩',
        ).exists())
        self.provider.escort_profile.refresh_from_db()
        self.assertEqual(self.provider.escort_profile.status, EscortProfile.Status.BUSY)

    def test_busy_selected_provider_is_rejected_before_payment(self):
        self.provider.escort_profile.status = EscortProfile.Status.BUSY
        self.provider.escort_profile.save(update_fields=['status'])
        response = self.client.post('/api/orders/create/', {
            'service_id': self.service.id,
            'provider_id': self.provider.id,
        })
        self.assertEqual(response.data['code'], 400)
        self.assertFalse(Order.objects.exists())
        self.customer.wallet.refresh_from_db()
        self.assertEqual(self.customer.wallet.balance, 50000)


class CustomerOrderIntegrationTest(APITestCase):
    def setUp(self):
        self.customer = make_user()
        self.provider = make_provider()
        self.service = make_service(price=10000, commission_rate=20)
        make_wallet(self.customer, balance=30000)
        self.client.force_authenticate(self.customer)

    def test_quote_uses_backend_pricing_without_deducting_wallet(self):
        response = self.client.post('/api/orders/quote/', {
            'service_id': self.service.id,
            'game_rounds': 2,
        })
        self.assertEqual(response.data['code'], 0)
        self.assertEqual(response.data['data']['original_amount'], 20000)
        self.assertEqual(response.data['data']['amount'], 20000)
        self.assertTrue(response.data['data']['balance_sufficient'])
        self.assertFalse(Order.objects.exists())
        self.customer.wallet.refresh_from_db()
        self.assertEqual(self.customer.wallet.balance, 30000)

    def test_order_list_contains_pricing_and_provider_reply(self):
        order = make_order(
            self.customer,
            service=self.service,
            provider=self.provider,
            status=Order.Status.COMPLETED,
            amount=9000,
            original_amount=10000,
            boss_discount=1000,
        )
        evaluation = make_evaluation(
            order,
            content='服务很好',
            reply_content='感谢老板认可',
        )
        response = self.client.get('/api/orders/orders/?role=customer')
        self.assertEqual(response.data['code'], 0)
        item = response.data['data'][0]
        self.assertEqual(item['original_amount'], 10000)
        self.assertEqual(item['boss_discount'], 1000)
        self.assertEqual(item['evaluation']['id'], evaluation.id)
        self.assertEqual(item['evaluation']['reply_content'], '感谢老板认可')


class MultiProviderVisibilityTest(APITestCase):
    def test_second_provider_can_see_but_not_operate_order(self):
        customer = make_user()
        primary = make_provider()
        secondary = make_provider()
        order = make_order(
            customer, provider=primary, status=Order.Status.GRABBED,
            escort_mode=Order.EscortMode.DOUBLE,
        )
        OrderProvider.objects.create(order=order, provider=primary, settlement_base=5000)
        OrderProvider.objects.create(order=order, provider=secondary, settlement_base=5000)

        self.client.force_authenticate(secondary)
        response = self.client.get('/api/orders/orders/?role=provider&status=grabbed')
        self.assertEqual(response.data['code'], 0)
        self.assertEqual(len(response.data['data']), 1)
        self.assertFalse(response.data['data'][0]['can_operate'])
        self.assertFalse(response.data['data'][0]['can_reject'])

    def test_primary_completion_settles_both_providers(self):
        customer = make_user()
        primary = make_provider()
        secondary = make_provider()
        make_wallet(primary, balance=0)
        make_wallet(secondary, balance=0)
        order = make_order(
            customer, provider=primary, status=Order.Status.IN_SERVICE,
            escort_mode=Order.EscortMode.DOUBLE, amount=10000,
            provider_income=7500, shop_income=2500,
        )
        first = OrderProvider.objects.create(
            order=order, provider=primary, settlement_base=5000, provider_income=4000,
        )
        second = OrderProvider.objects.create(
            order=order, provider=secondary, settlement_base=5000, provider_income=3500,
        )

        self.client.force_authenticate(primary)
        response = self.client.post(f'/api/orders/orders/{order.id}/complete/')
        self.assertEqual(response.data['code'], 0)
        primary.wallet.refresh_from_db()
        secondary.wallet.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(primary.wallet.balance, 4000)
        self.assertEqual(secondary.wallet.balance, 3500)
        self.assertIsNotNone(first.settled_at)
        self.assertIsNotNone(second.settled_at)
        self.assertEqual(
            Transaction.objects.filter(order=order, tx_type=Transaction.TxType.INCOME).count(),
            2,
        )


class CouponRefundFlowTest(APITestCase):
    def test_cancel_returns_used_coupon(self):
        customer = make_user()
        service = make_service(price=10000)
        make_wallet(customer, balance=50000)
        coupon = Coupon.objects.create(
            name='测试券', discount_type=Coupon.DiscountType.DIRECT,
            threshold=0, amount=1000,
            valid_to=timezone.now() + timedelta(days=7), is_active=True,
        )
        user_coupon = UserCoupon.objects.create(user=customer, coupon=coupon)
        self.client.force_authenticate(customer)
        created = self.client.post('/api/orders/create/', {
            'service_id': service.id, 'user_coupon_id': user_coupon.id,
        })
        order_id = created.data['data']['id']
        cancelled = self.client.post(f'/api/orders/orders/{order_id}/cancel/', {})
        self.assertEqual(cancelled.data['code'], 0)
        user_coupon.refresh_from_db()
        self.assertEqual(user_coupon.status, UserCoupon.Status.UNUSED)
        self.assertIsNone(user_coupon.order_id)
        self.assertIsNone(user_coupon.used_at)
