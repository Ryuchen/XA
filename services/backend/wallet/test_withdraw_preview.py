"""T8-a 提现试算（``GET /api/wallet/withdraw/?amount=``）回归测试。

背景
----
前端此前在 ``provider-miniapp/src/pages/wallet/index.tsx`` 里自己算了一遍税::

    const withdrawAmount = Math.round(parsedWithdraw * 10);
    const withdrawTax = Math.round(withdrawAmount * taxRate / 100);

前端算的税**从不入库** —— 落库走的是服务端 ``compute_withdraw_tax()``，
``WithdrawRequest.save()`` 还会用 ``actual = amount - tax_amount`` 再兜一次底。
所以这不是数据污染，而是「展示承诺 vs 实际到账」不一致：用户在提现前看到扣完剩
12.3 币，事后翻记录却是 12.2 币。

病根不在舍入方式对不齐 —— 把两边的舍入调成一致只能让它们**暂时**相同，下次谁改了
税率口径，展示值就会再次悄悄分叉。病根是前端不该持有第二份算法。因此服务端提供
试算，前端只负责展示。

本文件的核心断言只有一条：**试算返回值必须与同一金额走 POST 落库后的
``tax_amount`` / ``actual_amount`` 逐字段相等**。只要有人日后在 GET 里另写一份
算法，这条断言立刻红。

为什么用 125（= 12.5 兴安币）
-----------------------------
用户已拍板保留 0.1 兴安币粒度、不加整币校验，所以非整币是合法提现额。而整币金额
（如 10000）在任何舍入策略下结果都一样，**会把舍入分歧整个掩盖掉**；125 在 2% 税率
下恰好落在 2.5 这个 ROUND_HALF_UP 与银行家舍入结果不同的临界点上，是真正的压力点。
"""

from rest_framework.test import APITestCase

from orders.tests.factories import make_provider, make_wallet
from wallet.models import (
    MIN_WITHDRAW_AMOUNT_KEY,
    WITHDRAW_TAX_RATE_KEY,
    IdempotencyKey,
    SystemConfig,
    Transaction,
    Wallet,
    WithdrawRequest,
)

URL = '/api/wallet/withdraw/'


class WithdrawPreviewApiTest(APITestCase):
    """试算接口的行为约定。"""

    def setUp(self):
        self.provider = make_provider()
        make_wallet(self.provider, balance=50000)
        # 默认最低提现 10000（1000 兴安币），会让 125 这类小额被 POST 拦下，
        # 而核心用例必须让同一个 125 同时走通试算与落库，故调低门槛。
        SystemConfig.objects.create(key=MIN_WITHDRAW_AMOUNT_KEY, value='10')
        self.client.force_authenticate(self.provider)

    def _post(self, amount, **overrides):
        payload = {
            'amount': amount,
            'payee_method': 'WECHAT',
            'payee_account': 'wx-preview',
            'payee_name': '张三',
        }
        payload.update(overrides)
        return self.client.post(URL, payload)

    # ------------------------------------------------------------------
    # 核心断言：试算 == 落库
    # ------------------------------------------------------------------
    def test_preview_matches_persisted_withdraw_field_by_field(self):
        """125 的试算值必须与 125 落库后的税额、实收逐字段相等。"""
        preview = self.client.get(URL, {'amount': 125})
        self.assertEqual(preview.data['code'], 0)
        previewed = preview.data['data']

        created = self._post(125)
        self.assertEqual(created.data['code'], 0)

        row = WithdrawRequest.objects.get(user=self.provider)
        self.assertEqual(previewed['tax_amount'], row.tax_amount)
        self.assertEqual(previewed['actual_amount'], row.actual_amount)
        self.assertEqual(previewed['tax_rate'], row.tax_rate)
        self.assertEqual(previewed['amount'], row.amount)

        # 同时与 POST 响应体对齐，确保「提交后立刻看到的数字」也一致。
        submitted = created.data['data']['request']
        self.assertEqual(previewed['tax_amount'], submitted['tax_amount'])
        self.assertEqual(previewed['actual_amount'], submitted['actual_amount'])

    def test_preview_matches_persisted_under_custom_tax_rate(self):
        """换一个税率再对一次：防止试算把税率写死或读了另一处配置。"""
        SystemConfig.objects.create(key=WITHDRAW_TAX_RATE_KEY, value='7')

        previewed = self.client.get(URL, {'amount': 125}).data['data']
        self.assertEqual(previewed['tax_rate'], 7)

        self.assertEqual(self._post(125).data['code'], 0)
        row = WithdrawRequest.objects.get(user=self.provider)
        self.assertEqual(previewed['tax_amount'], row.tax_amount)
        self.assertEqual(previewed['actual_amount'], row.actual_amount)
        self.assertEqual(previewed['tax_rate'], row.tax_rate)

    # ------------------------------------------------------------------
    # 舍入：ROUND_HALF_UP，且必须与内建 round 的银行家舍入区分开
    # ------------------------------------------------------------------
    def test_half_unit_rounds_half_up_not_to_even(self):
        """.5 一律进位。内建 round 会给出 2 和 0，那是错的。"""
        # 125 * 2% = 2.5 -> 3（银行家舍入会得 2）
        data = self.client.get(URL, {'amount': 125}).data['data']
        self.assertEqual(data['tax_amount'], 3)
        self.assertEqual(data['actual_amount'], 122)

        # 25 * 2% = 0.5 -> 1（银行家舍入会得 0，即「零税」，用户一眼能看出差异）
        data = self.client.get(URL, {'amount': 25}).data['data']
        self.assertEqual(data['tax_amount'], 1)
        self.assertEqual(data['actual_amount'], 24)

    def test_half_unit_rounding_also_holds_after_persisting(self):
        """把 .5 边界一路走到落库，确认兜底逻辑没把它改回去。"""
        self.assertEqual(self._post(25).data['code'], 0)
        row = WithdrawRequest.objects.get(user=self.provider)
        self.assertEqual(row.tax_amount, 1)
        self.assertEqual(row.actual_amount, 24)

    def test_actual_amount_always_equals_amount_minus_tax(self):
        """扫一遍非整币金额，实收恒等于本金减税额，不允许出现第三种口径。"""
        for amount in (1, 5, 25, 75, 125, 999, 1234):
            with self.subTest(amount=amount):
                data = self.client.get(URL, {'amount': amount}).data['data']
                self.assertEqual(
                    data['actual_amount'], amount - data['tax_amount'],
                )

    # ------------------------------------------------------------------
    # 只读：试算不得有任何副作用
    # ------------------------------------------------------------------
    def test_preview_has_no_side_effects(self):
        """试算不落申请、不写流水、不占幂等键、不动余额。

        它挂在出账接口上，一旦哪天被顺手改成「先建单再算」，钱就会在用户只是
        看一眼估值的时候被冻结。
        """
        before = Wallet.objects.get(user=self.provider).balance

        for _ in range(3):
            self.assertEqual(
                self.client.get(URL, {'amount': 125}).data['code'], 0,
            )

        self.assertEqual(WithdrawRequest.objects.count(), 0)
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertEqual(IdempotencyKey.objects.count(), 0)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, before)

    def test_preview_does_not_burn_the_idempotency_key(self):
        """试算不占键，所以试算之后用任意 client_request_id 提交都应放行。"""
        self.assertEqual(
            self.client.get(URL, {'amount': 125}).data['code'], 0,
        )
        created = self._post(125, client_request_id='cid-after-preview')
        self.assertEqual(created.data['code'], 0)

    # ------------------------------------------------------------------
    # 向后兼容：不带 amount 时行为不变
    # ------------------------------------------------------------------
    def test_without_amount_response_shape_is_unchanged(self):
        data = self.client.get(URL).data['data']
        self.assertEqual(
            set(data),
            {'balance', 'frozen_amount', 'min_amount', 'tax_rate', 'requests'},
        )

    def test_blank_amount_is_treated_as_absent(self):
        """``?amount=``（输入框被清空）不该报错，按未试算处理即可。"""
        resp = self.client.get(URL, {'amount': ''})
        self.assertEqual(resp.data['code'], 0)
        self.assertNotIn('tax_amount', resp.data['data'])

    # ------------------------------------------------------------------
    # 非法入参：fail-fast，不做静默兜底
    # ------------------------------------------------------------------
    def test_illegal_amount_returns_400(self):
        """静默忽略非法值会让前端拿着上一次的旧数字继续展示，必须明确拒绝。"""
        for raw in ('12.5', 'abc', '0', '-100', '1e3', '12 5'):
            with self.subTest(raw=raw):
                resp = self.client.get(URL, {'amount': raw})
                self.assertEqual(resp.data['code'], 400)
                self.assertEqual(resp.data['msg'], '金额非法')

    def test_amount_below_min_still_previews(self):
        """低于最低提现额仍给试算：门槛由前端拿同一响应里的 min_amount 把关。

        若此处也返回 400，用户逐字输入「1」「12」「125」时每敲一下都会闪一次错误
        提示，而这三次输入其实都还没提交。试算是纯计算，不承担准入判断。
        """
        SystemConfig.objects.filter(key=MIN_WITHDRAW_AMOUNT_KEY).update(
            value='10000',
        )
        data = self.client.get(URL, {'amount': 125}).data['data']
        self.assertEqual(data['min_amount'], 10000)
        self.assertEqual(data['tax_amount'], 3)
        self.assertEqual(data['actual_amount'], 122)

    def test_amount_is_echoed_back(self):
        """回显入参，供前端丢弃乱序到达的旧响应。

        用户连续输入时会并发打出多个试算请求，响应未必按发出顺序回来；没有回显
        就无法判断「手里这个估值对应的是不是当前输入框里的金额」。
        """
        data = self.client.get(URL, {'amount': 125}).data['data']
        self.assertEqual(data['amount'], 125)

    def test_preview_requires_authentication(self):
        self.client.force_authenticate(None)
        resp = self.client.get(URL, {'amount': 125})
        self.assertIn(resp.status_code, (401, 403))
