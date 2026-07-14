"""订单完成时 shop_income 入平台钱包的单元测试。

覆盖：
- POST /api/orders/orders/<id>/complete/ 完成订单后，shop_income 归集到平台系统账户钱包，
  并落一条 SHOP_INCOME 流水。
- shop_income 为 0 时不产生平台流水。
- 平台入账不污染陪玩收益口径（陪玩仍按 provider_income 入账）。
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from orders.models import Order
from orders.tests.factories import make_order, make_provider, make_service, make_user
from wallet.models import Transaction, get_platform_wallet

User = get_user_model()


class ShopIncomeSettlementTest(APITestCase):
    def setUp(self):
        self.customer = make_user()
        self.provider = make_provider()
        self.service = make_service(price=10000)

    def _in_service_order(self, **kwargs):
        return make_order(
            self.customer,
            service=self.service,
            provider=self.provider,
            status=Order.Status.IN_SERVICE,
            **kwargs,
        )

    def test_platform_account_seeded(self):
        # 数据迁移应已创建禁登录的平台系统账户
        platform_user = User.objects.get(username=settings.PLATFORM_SYSTEM_USERNAME)
        self.assertFalse(platform_user.is_active)
        self.assertFalse(platform_user.has_usable_password())

    def test_complete_credits_shop_income_to_platform_wallet(self):
        order = self._in_service_order(
            amount=10000,
            commission_rate=20,
            provider_income=8000,
            shop_income=2000,
        )
        platform_before = get_platform_wallet().balance

        self.client.force_authenticate(self.provider)
        res = self.client.post(f'/api/orders/orders/{order.id}/complete/')
        self.assertEqual(res.data['code'], 0)

        platform_wallet = get_platform_wallet()
        self.assertEqual(platform_wallet.balance, platform_before + 2000)
        tx = Transaction.objects.get(
            wallet=platform_wallet,
            order=order,
            tx_type=Transaction.TxType.SHOP_INCOME,
        )
        self.assertEqual(tx.amount, 2000)
        self.assertEqual(tx.balance_after, platform_before + 2000)
        self.assertEqual(tx.remark, f'平台收入：{order.order_no}')

    def test_provider_income_unaffected_by_platform_credit(self):
        order = self._in_service_order(
            amount=10000,
            commission_rate=20,
            provider_income=8000,
            shop_income=2000,
        )
        self.client.force_authenticate(self.provider)
        self.client.post(f'/api/orders/orders/{order.id}/complete/')

        income_tx = Transaction.objects.get(
            wallet__user=self.provider,
            order=order,
            tx_type=Transaction.TxType.INCOME,
        )
        self.assertEqual(income_tx.amount, 8000)

    def test_zero_shop_income_creates_no_platform_tx(self):
        order = self._in_service_order(
            amount=10000,
            commission_rate=0,
            provider_income=10000,
            shop_income=0,
        )
        self.client.force_authenticate(self.provider)
        self.client.post(f'/api/orders/orders/{order.id}/complete/')

        platform_wallet = get_platform_wallet()
        self.assertFalse(
            Transaction.objects.filter(
                wallet=platform_wallet,
                order=order,
                tx_type=Transaction.TxType.SHOP_INCOME,
            ).exists()
        )
