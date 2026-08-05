"""订单拆账（推荐人分佣 + 老板折扣 + 完成入账）单元测试。"""

from rest_framework.test import APITestCase

from orders.models import Order
from orders.settlement import compute_split
from orders.tests.factories import (
    Transaction,
    make_boss_type,
    make_order,
    make_provider,
    make_service,
    make_user,
    make_wallet,
)
from wallet.models import SystemConfig, get_commission_rate
from wallet.models import COMMISSION_RATE_KEY


class ComputeSplitTest(APITestCase):
    def test_invariant_sum_equals_amount(self):
        split = compute_split(10000, 20, 50)
        self.assertEqual(
            split.provider_income + split.inviter_commission + split.shop_income,
            10000,
        )

    def test_basic_split(self):
        # 平台抽成 = 10000*20% = 2000；陪玩实得 8000
        # 推荐人 = 2000*50% = 1000；店铺 = 1000
        split = compute_split(10000, 20, 50)
        self.assertEqual(split.commission_rate, 20)
        self.assertEqual(split.provider_income, 8000)
        self.assertEqual(split.inviter_commission, 1000)
        self.assertEqual(split.shop_income, 1000)

    def test_zero_commission(self):
        split = compute_split(10000, 0, 50)
        self.assertEqual(split.provider_income, 10000)
        self.assertEqual(split.inviter_commission, 0)
        self.assertEqual(split.shop_income, 0)

    def test_no_inviter_rate_all_to_shop(self):
        split = compute_split(10000, 30, 0)
        self.assertEqual(split.provider_income, 7000)
        self.assertEqual(split.inviter_commission, 0)
        self.assertEqual(split.shop_income, 3000)

    def test_rate_clamped(self):
        # 抽成率超界回落，分佣率超界裁剪
        split = compute_split(10000, 200, 200)
        self.assertEqual(split.commission_rate, 100)
        self.assertEqual(split.provider_income, 0)
        self.assertEqual(split.inviter_commission, 10000)
        self.assertEqual(split.shop_income, 0)

    def test_rounding_remainder_to_shop(self):
        # 333*? 取整余项归店铺，仍满足恒等式
        split = compute_split(333, 33, 33)
        total = split.provider_income + split.inviter_commission + split.shop_income
        self.assertEqual(total, 333)


class CreateOrderSplitTest(APITestCase):
    """下单时按服务抽成率 + 推荐人配置 + 老板折扣落库拆账。"""

    def setUp(self):
        self.customer = make_user()
        make_wallet(self.customer, balance=1000000)
        self.client.force_authenticate(self.customer)

    def _create(self, service):
        return self.client.post('/api/orders/create/', {'service_id': service.id})

    def test_service_commission_rate_used(self):
        service = make_service(price=10000, commission_rate=30)
        res = self._create(service)
        self.assertEqual(res.data['code'], 0)
        order = Order.objects.get(id=res.data['data']['id'])
        self.assertEqual(order.commission_rate, 30)
        self.assertEqual(order.provider_income, 7000)
        self.assertEqual(order.shop_income, 3000)
        self.assertEqual(order.inviter_commission, 0)

    def test_fallback_to_global_commission(self):
        SystemConfig.objects.update_or_create(
            key=COMMISSION_RATE_KEY, defaults={'value': '25'}
        )
        service = make_service(price=10000)  # commission_rate=None
        res = self._create(service)
        order = Order.objects.get(id=res.data['data']['id'])
        self.assertEqual(order.commission_rate, get_commission_rate())
        self.assertEqual(order.commission_rate, 25)
        self.assertEqual(order.provider_income, 7500)

    def test_inviter_commission_recorded(self):
        inviter = make_user()
        self.customer.inviter = inviter
        self.customer.inviter_commission_rate = 50
        self.customer.save(update_fields=['inviter', 'inviter_commission_rate'])
        service = make_service(price=10000, commission_rate=20)
        res = self._create(service)
        order = Order.objects.get(id=res.data['data']['id'])
        self.assertEqual(order.inviter_id, inviter.id)
        self.assertEqual(order.inviter_commission, 1000)
        self.assertEqual(order.shop_income, 1000)
        self.assertEqual(order.provider_income, 8000)

    def test_boss_type_discount_applied(self):
        boss_type = make_boss_type(discount_rate=90)  # 九折
        self.customer.boss_type = boss_type
        self.customer.save(update_fields=['boss_type'])
        service = make_service(price=10000, commission_rate=20)
        res = self._create(service)
        self.assertEqual(res.data['code'], 0)
        # 实付 = 10000*90% = 9000
        self.assertEqual(res.data['data']['amount'], 9000)
        order = Order.objects.get(id=res.data['data']['id'])
        self.assertEqual(order.amount, 9000)
        self.assertEqual(order.provider_income, 7200)  # 9000*80%
        self.assertEqual(order.shop_income, 1800)


class CompleteOrderSplitSettlementTest(APITestCase):
    """完成订单时按拆账字段给陪玩/推荐人分别入账。"""

    def setUp(self):
        self.customer = make_user()
        self.provider = make_provider()
        self.inviter = make_user()
        make_wallet(self.provider, balance=0)
        make_wallet(self.inviter, balance=0)
        self.service = make_service(price=10000, commission_rate=20)

    def _in_service_order(self, **kwargs):
        return make_order(
            self.customer,
            service=self.service,
            provider=self.provider,
            status=Order.Status.IN_SERVICE,
            amount=10000,
            **kwargs,
        )

    def _complete(self, order):
        self.client.force_authenticate(self.provider)
        return self.client.post(f'/api/orders/orders/{order.id}/complete/')

    def test_provider_credited_split_income(self):
        order = self._in_service_order(
            commission_rate=20, provider_income=8000, shop_income=2000,
        )
        res = self._complete(order)
        self.assertEqual(res.data['code'], 0)
        self.provider.wallet.refresh_from_db()
        self.assertEqual(self.provider.wallet.balance, 8000)
        tx = Transaction.objects.get(order=order, wallet=self.provider.wallet)
        self.assertEqual(tx.amount, 8000)
        self.assertEqual(tx.tx_type, Transaction.TxType.INCOME)

    def test_inviter_credited_commission(self):
        order = self._in_service_order(
            commission_rate=20, provider_income=8000,
            inviter=self.inviter, inviter_commission=1000, shop_income=1000,
        )
        self._complete(order)
        self.provider.wallet.refresh_from_db()
        self.inviter.wallet.refresh_from_db()
        self.assertEqual(self.provider.wallet.balance, 8000)
        self.assertEqual(self.inviter.wallet.balance, 1000)
        inv_tx = Transaction.objects.get(order=order, wallet=self.inviter.wallet)
        self.assertEqual(inv_tx.amount, 1000)
        self.assertEqual(inv_tx.tx_type, Transaction.TxType.INCOME)

    def test_single_provider_zero_income_credits_nothing(self):
        # P0-2：抽成率 100% 时 provider_income=0 是合法配置，完成订单不得凭空造钱。
        # 陪玩实得恒为 0，平台全额留存，订单仍正常完成（绝不可回落成全额入账）。
        order = self._in_service_order(provider_income=0, shop_income=10000)
        res = self._complete(order)
        self.assertEqual(res.data['code'], 0)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.COMPLETED)
        self.provider.wallet.refresh_from_db()
        self.assertEqual(self.provider.wallet.balance, 0)  # 不造钱
