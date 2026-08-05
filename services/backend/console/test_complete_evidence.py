"""后台 complete 凭证风控回归测试。

背景：``OrderViewSet.complete`` 是把钱打给陪玩的结算入口，但原先不校验入队/结单图即可
结算，使报单审核退化成事后追认（"两套 ID 空间错充"之外的最后一类资金隐患）。修复后
后台 ``complete`` 在出账前先过凭证闸门 ``console.views._require_order_evidence``，
与顾客端 ``CompleteOrderView`` 受同一套 ``REQUIRE_ORDER_EVIDENCE_IMAGES`` 约束：
开启证据要求时，每个参与结算的陪玩报单必须齐备 ``entry_image`` + ``completion_image``，
缺失则整体回滚并返回清晰错误。
"""

from django.test import override_settings

from rest_framework.test import APITestCase

from orders.models import Order
from orders.tests.factories import make_order, make_provider, make_superuser, make_user, make_wallet
from wallet.models import ProviderReport, Transaction, Wallet

ADMIN_COMPLETE_URL = '/api/admin/orders/{pk}/complete/'


def _make_in_service_order(customer, provider, amount=10000, provider_income=8000):
    return make_order(
        customer,
        provider=provider,
        status=Order.Status.IN_SERVICE,
        amount=amount,
        provider_income=provider_income,
    )


def _make_report(order, provider, *, with_entry=True, with_completion=True):
    return ProviderReport.objects.create(
        provider=provider,
        order=order,
        game_name='王者荣耀',
        amount=order.amount,
        entry_image='entry.png' if with_entry else None,
        completion_image='completion.png' if with_completion else None,
    )


@override_settings(REQUIRE_ORDER_EVIDENCE_IMAGES=True)
class CompleteEvidenceRequiredTest(APITestCase):
    def setUp(self):
        self.admin = make_superuser()
        self.customer = make_user()
        self.provider = make_provider()
        make_wallet(self.provider, balance=0)

    def _complete(self, order):
        self.client.force_authenticate(self.admin)
        return self.client.post(ADMIN_COMPLETE_URL.format(pk=order.id))

    def test_complete_with_full_evidence_settles(self):
        """凭证齐备时正常结算：陪玩钱包入账、订单转已完成。"""
        order = _make_in_service_order(self.customer, self.provider)
        _make_report(order, self.provider)
        res = self._complete(order)
        self.assertEqual(res.data['code'], 0)
        self.provider.wallet.refresh_from_db()
        self.assertEqual(self.provider.wallet.balance, 8000)
        self.assertEqual(Transaction.objects.filter(wallet=self.provider.wallet).count(), 1)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.COMPLETED)

    def test_complete_missing_completion_image_blocked(self):
        """缺结单图时拒绝结算，钱包不变、订单保持服务中。"""
        order = _make_in_service_order(self.customer, self.provider)
        _make_report(order, self.provider, with_completion=False)
        res = self._complete(order)
        self.assertEqual(res.data['code'], 400)
        self.assertIn('结单截图', res.data['msg'])
        self.provider.wallet.refresh_from_db()
        self.assertEqual(self.provider.wallet.balance, 0)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.IN_SERVICE)

    def test_complete_missing_entry_image_blocked(self):
        """缺入队图时拒绝结算。"""
        order = _make_in_service_order(self.customer, self.provider)
        _make_report(order, self.provider, with_entry=False)
        res = self._complete(order)
        self.assertEqual(res.data['code'], 400)
        self.assertIn('入队截图', res.data['msg'])
        self.provider.wallet.refresh_from_db()
        self.assertEqual(self.provider.wallet.balance, 0)

    def test_complete_missing_report_blocked(self):
        """无报单凭证时拒绝结算。"""
        order = _make_in_service_order(self.customer, self.provider)
        # 故意不创建 ProviderReport
        res = self._complete(order)
        self.assertEqual(res.data['code'], 400)
        self.assertIn('报单凭证', res.data['msg'])
        self.provider.wallet.refresh_from_db()
        self.assertEqual(self.provider.wallet.balance, 0)


class CompleteEvidenceDisabledTest(APITestCase):
    """REQUIRE_ORDER_EVIDENCE_IMAGES=False（默认/测试配置）时，complete 不为凭证所阻。"""

    def setUp(self):
        self.admin = make_superuser()
        self.customer = make_user()
        self.provider = make_provider()
        make_wallet(self.provider, balance=0)

    def test_complete_settles_without_evidence_when_flag_off(self):
        order = _make_in_service_order(self.customer, self.provider)
        # 不创建任何报单凭证
        self.client.force_authenticate(self.admin)
        res = self.client.post(ADMIN_COMPLETE_URL.format(pk=order.id))
        self.assertEqual(res.data['code'], 0)
        self.provider.wallet.refresh_from_db()
        self.assertEqual(self.provider.wallet.balance, 8000)
