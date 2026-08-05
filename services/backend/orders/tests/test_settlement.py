"""订单完成结算的单元测试（CompleteOrderView 钱包入账 + 流水）。"""

from rest_framework.test import APITestCase

from orders.models import Order
from orders.tests.factories import (
    Transaction,
    make_order,
    make_provider,
    make_service,
    make_user,
    make_wallet,
)


class CompleteOrderSettlementTest(APITestCase):
    def setUp(self):
        self.customer = make_user()
        self.provider = make_provider()
        self.service = make_service(price=10000)
        make_wallet(self.provider, balance=2000)

    def _in_service_order(self, **kwargs):
        return make_order(
            self.customer,
            service=self.service,
            provider=self.provider,
            status=Order.Status.IN_SERVICE,
            amount=10000,
            **kwargs,
        )

    def _complete(self, order, user=None):
        self.client.force_authenticate(user or self.provider)
        return self.client.post(f'/api/orders/orders/{order.id}/complete/')

    def test_complete_credits_provider_wallet(self):
        # 20% 抽成：实付 10000，陪玩实得 8000（不得回落全额，P0-2 防造钱）
        order = self._in_service_order(provider_income=8000, shop_income=2000)
        res = self._complete(order)
        self.assertEqual(res.data['code'], 0)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.COMPLETED)
        self.provider.wallet.refresh_from_db()
        self.assertEqual(self.provider.wallet.balance, 10000)  # 2000 + 8000

    def test_complete_creates_income_transaction(self):
        # 入账流水金额必须等于拆账实得 8000，而非订单全额（防回落造钱）
        order = self._in_service_order(provider_income=8000, shop_income=2000)
        self._complete(order)
        tx = Transaction.objects.get(order=order, tx_type=Transaction.TxType.INCOME)
        self.assertEqual(tx.amount, 8000)
        self.assertEqual(tx.balance_before, 2000)
        self.assertEqual(tx.balance_after, 10000)

    def test_complete_updates_escort_profile(self):
        order = self._in_service_order()
        self._complete(order)
        self.provider.escort_profile.refresh_from_db()
        self.assertEqual(self.provider.escort_profile.completed_order_count, 1)

    def test_complete_rejects_non_owner_provider(self):
        order = self._in_service_order()
        other = make_provider()
        res = self._complete(order, user=other)
        self.assertEqual(res.data['code'], 400)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.IN_SERVICE)
        self.assertFalse(Transaction.objects.filter(order=order).exists())

    def test_complete_rejects_illegal_status(self):
        order = make_order(
            self.customer,
            service=self.service,
            provider=self.provider,
            status=Order.Status.PENDING,
        )
        res = self._complete(order)
        self.assertEqual(res.data['code'], 400)
        self.assertFalse(Transaction.objects.filter(order=order).exists())

    def test_complete_missing_order_returns_404(self):
        self.client.force_authenticate(self.provider)
        res = self.client.post('/api/orders/orders/999999/complete/')
        self.assertEqual(res.data['code'], 404)

    def test_complete_requires_auth(self):
        order = self._in_service_order()
        res = self.client.post(f'/api/orders/orders/{order.id}/complete/')
        self.assertEqual(res.status_code, 401)

    def test_complete_is_idempotent_guarded(self):
        order = self._in_service_order(provider_income=8000, shop_income=2000)
        self._complete(order)
        res = self._complete(order)  # 第二次：已是终态
        self.assertEqual(res.data['code'], 400)
        # 陪玩收益流水仅入账一次（幂等），第二次被拒未新增
        self.assertEqual(
            Transaction.objects.filter(
                order=order, tx_type=Transaction.TxType.INCOME,
            ).count(),
            1,
        )
        self.provider.wallet.refresh_from_db()
        self.assertEqual(self.provider.wallet.balance, 10000)
