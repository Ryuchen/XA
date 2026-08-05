"""资金口径修复的回归测试。

覆盖三处历史缺陷，每条用例都对应一个曾经真实存在的账务漏洞：

1. **提现税额账务黑洞**：陪玩钱包按税前全额扣减，但代扣的 ``tax_amount``
   既不入平台钱包也无任何 Transaction，这部分钱在账面上凭空消失；
   同时到账通知用的是税前金额，与陪玩实收对不上。
2. **双陪报单金额错位**：报单审核回填 ``payout_amount`` 时取的是订单级
   汇总 ``order.provider_income``，双陪场景下副陪看到的审核金额远大于
   自己钱包的实际到账。
3. **负数调账语义错位**：后台调减余额落的是 ``WITHDRAW``（提现）流水，
   污染提现口径的统计与对账。
"""

from django.test import override_settings
from rest_framework.test import APITestCase

from orders.models import Order, OrderProvider
from orders.tests.factories import (
    Transaction,
    make_completed_order,
    make_provider,
    make_superuser,
    make_user,
    make_wallet,
)
from wallet.models import (
    WITHDRAW_TAX_RATE_KEY,
    ProviderReport,
    SystemConfig,
    Wallet,
    WithdrawRequest,
    get_platform_wallet,
)


class WithdrawTaxAccountingTest(APITestCase):
    """提现代扣税必须进平台钱包并留痕，且通知口径为实际到账。"""

    def setUp(self):
        self.admin = make_superuser()
        self.provider = make_provider()
        make_wallet(self.provider, balance=50000)

    def _set_tax_rate(self, rate):
        SystemConfig.objects.update_or_create(
            key=WITHDRAW_TAX_RATE_KEY, defaults={'value': str(rate)},
        )

    def _apply(self, amount=20000):
        self.client.force_authenticate(self.provider)
        res = self.client.post('/api/wallet/withdraw/', {
            'amount': amount,
            'payee_method': 'WECHAT',
            'payee_account': 'wx123',
            'payee_name': '李四',
        })
        self.assertEqual(res.data['code'], 0)
        return WithdrawRequest.objects.get(user=self.provider)

    def _approve(self, withdraw, reference='WX-PAYOUT-001'):
        self.client.force_authenticate(self.admin)
        res = self.client.post(
            f'/api/admin/withdrawals/{withdraw.id}/approve/',
            {'payout_reference': reference},
        )
        self.assertEqual(res.data['code'], 0)
        return res

    def test_apply_snapshots_tax_fields(self):
        self._set_tax_rate(10)
        withdraw = self._apply(amount=20000)
        self.assertEqual(withdraw.tax_rate, 10)
        self.assertEqual(withdraw.tax_amount, 2000)
        self.assertEqual(withdraw.actual_amount, 18000)

    def test_approve_credits_tax_to_platform_wallet(self):
        self._set_tax_rate(10)
        withdraw = self._apply(amount=20000)
        platform_before = get_platform_wallet().balance

        self._approve(withdraw)

        platform_wallet = get_platform_wallet()
        self.assertEqual(platform_wallet.balance, platform_before + 2000)
        tax_tx = Transaction.objects.get(
            wallet=platform_wallet, tx_type=Transaction.TxType.WITHDRAW_TAX,
        )
        self.assertEqual(tax_tx.amount, 2000)
        self.assertEqual(tax_tx.balance_after - tax_tx.balance_before, 2000)
        self.assertIn(str(withdraw.pk), tax_tx.remark)

    def test_withdraw_is_conserved_across_wallets(self):
        """陪玩净流出 == 平台收税 + 实际打款，一分不能凭空消失。"""
        self._set_tax_rate(15)
        wallet_before = Wallet.objects.get(user=self.provider).balance
        platform_before = get_platform_wallet().balance

        withdraw = self._apply(amount=20000)
        self._approve(withdraw)

        wallet_after = Wallet.objects.get(user=self.provider)
        provider_outflow = wallet_before - wallet_after.balance
        platform_inflow = get_platform_wallet().balance - platform_before

        self.assertEqual(wallet_after.frozen_amount, 0)
        self.assertEqual(provider_outflow, withdraw.amount)
        self.assertEqual(platform_inflow, withdraw.tax_amount)
        self.assertEqual(
            provider_outflow, platform_inflow + withdraw.actual_amount,
        )

    def test_zero_tax_rate_creates_no_tax_transaction(self):
        self._set_tax_rate(0)
        withdraw = self._apply(amount=20000)
        platform_before = get_platform_wallet().balance

        self._approve(withdraw)

        withdraw.refresh_from_db()
        self.assertEqual(withdraw.tax_amount, 0)
        self.assertEqual(withdraw.actual_amount, 20000)
        self.assertEqual(get_platform_wallet().balance, platform_before)
        self.assertFalse(
            Transaction.objects.filter(
                tx_type=Transaction.TxType.WITHDRAW_TAX,
            ).exists()
        )

    def test_approve_notifies_actual_amount_not_gross(self):
        """通知必须写实际到账，否则陪玩按税前对账会产生客诉。"""
        from site_messages.models import Message

        self._set_tax_rate(10)
        withdraw = self._apply(amount=20000)
        self._approve(withdraw)

        msg = Message.objects.filter(
            recipient=self.provider, title='提现审核通过',
        ).latest('id')
        # 实际到账 1800 兴安币，税前 2000 兴安币
        self.assertIn('1800', msg.preview)
        self.assertIn('1800', msg.detail)
        self.assertIn('代扣税', msg.detail)

    def test_provider_withdraw_transaction_records_tax_breakdown(self):
        self._set_tax_rate(10)
        withdraw = self._apply(amount=20000)
        self._approve(withdraw)

        tx = Transaction.objects.get(pk=withdraw.transaction_id)
        self.assertEqual(tx.status, Transaction.Status.SUCCESS)
        self.assertEqual(tx.amount, -20000)
        self.assertIn('代扣税', tx.remark)
        self.assertIn('1800', tx.remark)

    def test_reject_does_not_touch_platform_wallet(self):
        self._set_tax_rate(10)
        withdraw = self._apply(amount=20000)
        platform_before = get_platform_wallet().balance

        self.client.force_authenticate(self.admin)
        res = self.client.post(
            f'/api/admin/withdrawals/{withdraw.id}/reject/',
            {'audit_remark': '收款信息有误'},
        )

        self.assertEqual(res.data['code'], 0)
        self.assertEqual(get_platform_wallet().balance, platform_before)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 50000)


class DoubleEscortReportPayoutTest(APITestCase):
    """双陪报单审核回填的金额必须是报单人自己那份，而非订单汇总。"""

    def setUp(self):
        self.admin = make_superuser()
        self.customer = make_user()
        self.primary = make_provider()
        self.secondary = make_provider()

    def _double_order(self, amount=10000, primary_income=4000, secondary_income=3500):
        order = make_completed_order(
            self.customer,
            self.primary,
            amount=amount,
            escort_mode=Order.EscortMode.DOUBLE,
            provider_income=primary_income + secondary_income,
            shop_income=amount - primary_income - secondary_income,
        )
        OrderProvider.objects.create(
            order=order, provider=self.primary,
            settlement_base=amount // 2, commission_rate=20,
            provider_income=primary_income,
        )
        OrderProvider.objects.create(
            order=order, provider=self.secondary,
            settlement_base=amount // 2, commission_rate=30,
            provider_income=secondary_income,
        )
        return order

    def test_secondary_report_payout_uses_own_share(self):
        order = self._double_order()
        report = ProviderReport.objects.create(
            provider=self.secondary, order=order, game_name='王者', amount=10000,
        )
        self.client.force_authenticate(self.admin)

        res = self.client.post(f'/api/admin/reports/{report.id}/approve/')

        self.assertEqual(res.data['code'], 0)
        report.refresh_from_db()
        # 订单级汇总是 7500，副陪自己那份是 3500
        self.assertEqual(order.provider_income, 7500)
        self.assertEqual(report.payout_amount, 3500)
        self.assertEqual(report.commission_rate, 30)

    def test_primary_report_payout_uses_own_share(self):
        order = self._double_order()
        report = ProviderReport.objects.create(
            provider=self.primary, order=order, game_name='王者', amount=10000,
        )
        self.client.force_authenticate(self.admin)

        res = self.client.post(f'/api/admin/reports/{report.id}/approve/')

        self.assertEqual(res.data['code'], 0)
        report.refresh_from_db()
        self.assertEqual(report.payout_amount, 4000)
        self.assertEqual(report.commission_rate, 20)

    def test_report_payout_matches_wallet_credit(self):
        """审核显示的金额必须与结算入账口径一致，否则陪玩无法对账。"""
        order = self._double_order()
        share = OrderProvider.objects.get(order=order, provider=self.secondary)
        report = ProviderReport.objects.create(
            provider=self.secondary, order=order, game_name='王者', amount=10000,
        )
        self.client.force_authenticate(self.admin)

        self.client.post(f'/api/admin/reports/{report.id}/approve/')

        report.refresh_from_db()
        self.assertEqual(report.payout_amount, share.provider_income)

    def test_order_report_approve_still_does_not_credit_twice(self):
        order = self._double_order()
        report = ProviderReport.objects.create(
            provider=self.secondary, order=order, game_name='王者', amount=10000,
        )
        balance_before = Wallet.objects.get(user=self.secondary).balance
        self.client.force_authenticate(self.admin)

        self.client.post(f'/api/admin/reports/{report.id}/approve/')

        self.assertEqual(
            Wallet.objects.get(user=self.secondary).balance, balance_before,
        )

    def test_legacy_order_without_split_rows_falls_back_to_order_level(self):
        """老订单没有 OrderProvider 明细行时回退订单级字段（单陪等价）。"""
        order = make_completed_order(
            self.customer, self.primary, amount=10000,
            provider_income=8000, shop_income=2000,
        )
        self.assertFalse(order.providers.exists())
        report = ProviderReport.objects.create(
            provider=self.primary, order=order, game_name='王者', amount=10000,
        )
        self.client.force_authenticate(self.admin)

        res = self.client.post(f'/api/admin/reports/{report.id}/approve/')

        self.assertEqual(res.data['code'], 0)
        report.refresh_from_db()
        self.assertEqual(report.payout_amount, 8000)


class WalletAdjustTxTypeTest(APITestCase):
    """后台调账必须使用独立的 ADJUST 类型，不得借用 REWARD / WITHDRAW。"""

    def setUp(self):
        self.admin = make_superuser()
        self.customer = make_user()
        make_wallet(self.customer, balance=10000)
        self.wallet = Wallet.objects.get(user=self.customer)

    def _adjust(self, amount, remark='测试调账'):
        self.client.force_authenticate(self.admin)
        return self.client.post(
            f'/api/admin/wallets/{self.wallet.id}/adjust/',
            {'amount': amount, 'remark': remark},
        )

    def test_negative_adjust_is_not_recorded_as_withdraw(self):
        res = self._adjust(-3000)

        self.assertEqual(res.data['code'], 0)
        tx = Transaction.objects.get(wallet=self.wallet)
        self.assertEqual(tx.tx_type, Transaction.TxType.ADJUST)
        self.assertEqual(tx.amount, -3000)
        self.assertFalse(
            Transaction.objects.filter(
                wallet=self.wallet, tx_type=Transaction.TxType.WITHDRAW,
            ).exists()
        )

    def test_positive_adjust_is_not_recorded_as_reward(self):
        res = self._adjust(3000)

        self.assertEqual(res.data['code'], 0)
        tx = Transaction.objects.get(wallet=self.wallet)
        self.assertEqual(tx.tx_type, Transaction.TxType.ADJUST)
        self.assertEqual(tx.amount, 3000)
        self.assertFalse(
            Transaction.objects.filter(
                wallet=self.wallet, tx_type=Transaction.TxType.REWARD,
            ).exists()
        )

    def test_adjust_keeps_balance_and_operator_trace(self):
        self._adjust(-3000, remark='误充回退')

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 7000)
        tx = Transaction.objects.get(wallet=self.wallet)
        self.assertEqual(tx.balance_before, 10000)
        self.assertEqual(tx.balance_after, 7000)
        self.assertEqual(tx.remark, '误充回退')
        self.assertEqual(tx.operator_id, self.admin.id)
