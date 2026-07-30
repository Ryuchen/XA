from rest_framework.test import APITestCase

from orders.models import Order, ServiceCategory
from orders.tests.factories import (
    make_order,
    make_provider,
    make_service,
    make_user,
    make_wallet,
)
from wallet.models import Transaction


class TipOrderTest(APITestCase):
    URL_TEMPLATE = '/api/orders/orders/{}/tip/'

    def setUp(self):
        self.customer = make_user(role='CUSTOMER', nickname='测试老板')
        self.provider = make_provider(display_name='测试陪玩', is_verified=True)
        make_wallet(self.customer, balance=10000)
        make_wallet(self.provider, balance=1000)
        normal_category = ServiceCategory.objects.create(name='测试陪玩分类', is_gift=False)
        gift_category = ServiceCategory.objects.create(name='测试礼物分类', is_gift=True)
        self.normal_service = make_service(
            name='王者陪玩', price=5000, service_category=normal_category,
        )
        self.gift = make_service(
            name='心动比心', price=2000, service_category=gift_category,
            commission_rate=20,
        )
        self.source_order = make_order(
            self.customer,
            service=self.normal_service,
            provider=self.provider,
            status=Order.Status.COMPLETED,
            payment_status=Order.PaymentStatus.PAID,
        )
        self.client.force_authenticate(self.customer)

    def test_completed_order_tip_is_paid_and_settled_immediately(self):
        response = self.client.post(
            self.URL_TEMPLATE.format(self.source_order.id),
            {'gift_service_id': self.gift.id},
        )
        self.assertEqual(response.data['code'], 0)
        gift_order = Order.objects.get(source_order=self.source_order)
        self.assertEqual(gift_order.status, Order.Status.COMPLETED)
        self.assertEqual(gift_order.provider, self.provider)
        self.assertEqual(gift_order.provider_income, 1600)
        self.customer.wallet.refresh_from_db()
        self.provider.wallet.refresh_from_db()
        self.assertEqual(self.customer.wallet.balance, 8000)
        self.assertEqual(self.provider.wallet.balance, 2600)
        self.assertTrue(Transaction.objects.filter(order=gift_order, tx_type=Transaction.TxType.GIFT, amount=-2000).exists())
        self.assertTrue(Transaction.objects.filter(order=gift_order, tx_type=Transaction.TxType.GIFT, amount=1600).exists())

        self.client.force_authenticate(self.provider)
        benefits = self.client.get('/api/wallet/provider-benefits/')
        self.assertEqual(benefits.data['data']['summary']['gift_count'], 1)
        self.assertEqual(benefits.data['data']['gift_records'][0]['service_name'], '心动比心')

    def test_pending_order_cannot_tip(self):
        self.source_order.status = Order.Status.PENDING
        self.source_order.save(update_fields=['status'])
        response = self.client.post(
            self.URL_TEMPLATE.format(self.source_order.id),
            {'gift_service_id': self.gift.id},
        )
        self.assertEqual(response.data['code'], 400)
        self.assertFalse(Order.objects.filter(source_order=self.source_order).exists())

    def test_insufficient_balance_does_not_create_gift_order(self):
        self.customer.wallet.balance = 100
        self.customer.wallet.save(update_fields=['balance'])
        response = self.client.post(
            self.URL_TEMPLATE.format(self.source_order.id),
            {'gift_service_id': self.gift.id},
        )
        self.assertEqual(response.data['code'], 400)
        self.assertFalse(Order.objects.filter(source_order=self.source_order).exists())
