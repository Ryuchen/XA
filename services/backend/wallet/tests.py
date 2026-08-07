"""钱包配置回落与提现申请的单元测试。"""

from unittest import mock

from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase

from orders.tests.factories import (
    make_completed_order, make_order, make_provider, make_service, make_service_category,
    make_user, make_wallet,
)
from users.models import EscortProfile
from wallet.models import (
    COMMISSION_RATE_KEY,
    MIN_WITHDRAW_AMOUNT_KEY,
    SystemConfig,
    Transaction,
    DisposeRecord,
    Wallet,
    WithdrawRequest,
    get_commission_rate,
    get_min_withdraw_amount,
    get_platform_wallet,
)


class CustomerTopupDisabledTest(APITestCase):
    def test_customer_cannot_credit_wallet_directly(self):
        customer = make_user()
        wallet = make_wallet(customer, balance=1200)
        self.client.force_authenticate(customer)

        res = self.client.post('/api/wallet/topup/', {'amount': 10000})

        self.assertEqual(res.data['code'], 403)
        self.assertIn('企业微信客服', res.data['msg'])
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 1200)
        self.assertFalse(wallet.transactions.filter(tx_type=Transaction.TxType.TOPUP).exists())


class ProviderBenefitRecordsTest(APITestCase):
    URL = '/api/wallet/provider-benefits/'

    def setUp(self):
        self.provider = make_provider()
        self.customer = make_user(nickname='送礼老板')
        gift_category = make_service_category(is_gift=True)
        gift_service = make_service(name='心动礼物', price=5000, service_category=gift_category)
        make_completed_order(
            self.customer, self.provider, service=gift_service,
            provider_income=4000,
        )
        DisposeRecord.objects.create(
            user=self.provider, dispose_type=DisposeRecord.DisposeType.REWARD,
            amount=1200, reason='五星好评奖励',
        )
        DisposeRecord.objects.create(
            user=self.provider, dispose_type=DisposeRecord.DisposeType.PENALTY,
            amount=300, reason='迟到处罚',
        )

    def test_provider_gets_own_dispose_and_gift_records(self):
        self.client.force_authenticate(self.provider)
        res = self.client.get(self.URL)
        self.assertEqual(res.data['code'], 0)
        data = res.data['data']
        self.assertEqual(data['summary']['total_reward'], 1200)
        self.assertEqual(data['summary']['total_penalty'], 300)
        self.assertEqual(data['summary']['total_gift_income'], 4000)
        self.assertEqual(data['summary']['gift_count'], 1)
        self.assertEqual(data['gift_records'][0]['service_name'], '心动礼物')
        self.assertEqual(data['dispose_records'][0]['signed_amount'], -300)

    def test_customer_is_denied(self):
        self.client.force_authenticate(self.customer)
        res = self.client.get(self.URL)
        self.assertEqual(res.data['code'], 403)


class OrderReportFlowTest(APITestCase):
    def setUp(self):
        self.provider = make_provider()
        self.customer = make_user()
        self.order = make_completed_order(
            self.customer, self.provider, provider_income=8000, amount=10000,
        )
        self.client.force_authenticate(self.provider)

    @staticmethod
    def _image(name):
        # 1x1 GIF，足够通过 ImageField 格式校验。
        content = b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
        return SimpleUploadedFile(name, content, content_type='image/gif')

    def test_completed_order_report_requires_three_evidence_categories(self):
        create_res = self.client.post('/api/wallet/reports/', {'order_id': self.order.id}, format='json')
        self.assertEqual(create_res.data['code'], 0)
        report_id = create_res.data['data']['id']

        incomplete = self.client.post(f'/api/wallet/reports/{report_id}/submit/')
        self.assertEqual(incomplete.data['code'], 400)

        for kind, name in [('ENTRY', 'entry.gif'), ('COMPLETION', 'completion.gif'), ('RESULT', 'result.gif')]:
            upload = self.client.post(
                f'/api/wallet/reports/{report_id}/images/',
                {'kind': kind, 'image': self._image(name)}, format='multipart',
            )
            self.assertEqual(upload.data['code'], 0)

        submitted = self.client.post(f'/api/wallet/reports/{report_id}/submit/')
        self.assertEqual(submitted.data['code'], 0)
        self.assertEqual(submitted.data['data']['status'], 'PENDING')
        self.assertEqual(len(submitted.data['data']['result_image_urls']), 1)

    def test_pending_order_cannot_create_report(self):
        pending = make_order(self.customer, provider=self.provider)
        res = self.client.post('/api/wallet/reports/', {'order_id': pending.id}, format='json')
        self.assertEqual(res.data['code'], 400)


class CommissionRateConfigTest(TestCase):
    def test_default_when_unconfigured(self):
        self.assertEqual(get_commission_rate(), 20)

    def test_reads_configured_value(self):
        SystemConfig.objects.create(key=COMMISSION_RATE_KEY, value='35')
        self.assertEqual(get_commission_rate(), 35)

    def test_falls_back_on_non_numeric(self):
        SystemConfig.objects.create(key=COMMISSION_RATE_KEY, value='abc')
        self.assertEqual(get_commission_rate(), 20)

    def test_falls_back_on_out_of_range(self):
        SystemConfig.objects.create(key=COMMISSION_RATE_KEY, value='150')
        self.assertEqual(get_commission_rate(), 20)


class MinWithdrawConfigTest(TestCase):
    def test_default_when_unconfigured(self):
        self.assertEqual(get_min_withdraw_amount(), 10000)

    def test_reads_configured_value(self):
        SystemConfig.objects.create(key=MIN_WITHDRAW_AMOUNT_KEY, value='5000')
        self.assertEqual(get_min_withdraw_amount(), 5000)

    def test_falls_back_on_negative(self):
        SystemConfig.objects.create(key=MIN_WITHDRAW_AMOUNT_KEY, value='-1')
        self.assertEqual(get_min_withdraw_amount(), 10000)


class WithdrawApplyTest(APITestCase):
    def setUp(self):
        self.provider = make_provider()
        make_wallet(self.provider, balance=50000)

    def _post(self, **overrides):
        payload = {
            'amount': 20000,
            'payee_method': 'WECHAT',
            'payee_account': 'wx123',
            'payee_name': '李四',
        }
        payload.update(overrides)
        self.client.force_authenticate(self.provider)
        return self.client.post('/api/wallet/withdraw/', payload)

    def test_apply_success_freezes_balance(self):
        res = self._post(amount=20000)
        self.assertEqual(res.data['code'], 0)
        wallet = Wallet.objects.get(user=self.provider)
        self.assertEqual(wallet.balance, 30000)
        self.assertEqual(wallet.frozen_amount, 20000)
        self.assertEqual(WithdrawRequest.objects.filter(user=self.provider).count(), 1)

    def test_non_provider_denied(self):
        customer = make_user()
        make_wallet(customer, balance=50000)
        self.client.force_authenticate(customer)
        res = self.client.post('/api/wallet/withdraw/', {
            'amount': 20000, 'payee_method': 'WECHAT',
            'payee_account': 'wx', 'payee_name': '王五',
        })
        self.assertEqual(res.data['code'], 403)

    def test_below_minimum_rejected(self):
        res = self._post(amount=5000)
        self.assertEqual(res.data['code'], 400)

    def test_insufficient_balance_rejected(self):
        res = self._post(amount=60000)
        self.assertEqual(res.data['code'], 400)
        wallet = Wallet.objects.get(user=self.provider)
        self.assertEqual(wallet.balance, 50000)

    def test_invalid_payee_method_rejected(self):
        res = self._post(payee_method='BTC')
        self.assertEqual(res.data['code'], 400)

    def test_missing_payee_info_rejected(self):
        res = self._post(payee_account='', payee_name='')
        self.assertEqual(res.data['code'], 400)


class DepositPayTest(APITestCase):
    """押金缴纳（余额）相关测试。"""

    DEPOSIT_URL = '/api/wallet/deposit/'

    def setUp(self):
        self.provider = make_provider(deposit_required=50000)
        make_wallet(self.provider, balance=80000)

    def _profile(self):
        return EscortProfile.objects.get(user=self.provider)

    def test_get_overview(self):
        self.client.force_authenticate(self.provider)
        res = self.client.get(self.DEPOSIT_URL)
        self.assertEqual(res.data['code'], 0)
        data = res.data['data']
        self.assertEqual(data['deposit_required'], 50000)
        self.assertEqual(data['deposit_paid'], 0)
        self.assertEqual(data['deposit_remaining'], 50000)
        self.assertEqual(data['balance'], 80000)

    def test_pay_success(self):
        self.client.force_authenticate(self.provider)
        res = self.client.post(self.DEPOSIT_URL, {'amount': 30000})
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['deposit_paid'], 30000)
        self.assertEqual(res.data['data']['deposit_remaining'], 20000)
        self.assertEqual(res.data['data']['balance'], 50000)

        wallet = Wallet.objects.get(user=self.provider)
        self.assertEqual(wallet.balance, 50000)
        self.assertEqual(self._profile().deposit_paid, 30000)
        tx = Transaction.objects.get(
            wallet=wallet, tx_type=Transaction.TxType.DEPOSIT
        )
        self.assertEqual(tx.amount, -30000)
        self.assertEqual(tx.status, Transaction.Status.SUCCESS)

    # -- 双边记账 -------------------------------------------------------
    #
    # 押金从陪玩钱包扣走后必须在平台钱包落地。此前只有陪玩侧那半边账，
    # 钱在账面上凭空蒸发，全站 sum(balance) 对不上，退押金时平台账上也没有
    # 对应的钱可吐。以下用例把「双边余额 + 双边流水 + 同一事务」钉死。

    def test_pay_credits_platform_wallet(self):
        """缴押金 = 陪玩侧出账 + 平台侧入账，两条流水金额互为相反数。"""
        platform_wallet = get_platform_wallet()
        self.assertEqual(platform_wallet.balance, 0)

        self.client.force_authenticate(self.provider)
        res = self.client.post(self.DEPOSIT_URL, {'amount': 30000})
        self.assertEqual(res.data['code'], 0)

        provider_wallet = Wallet.objects.get(user=self.provider)
        platform_wallet.refresh_from_db()
        # 双边余额：陪玩少多少，平台就多多少，钱没有蒸发
        self.assertEqual(provider_wallet.balance, 50000)
        self.assertEqual(platform_wallet.balance, 30000)

        # 流水条数：一进一出各一条，不多不少
        self.assertEqual(Transaction.objects.count(), 2)
        out_tx = Transaction.objects.get(tx_type=Transaction.TxType.DEPOSIT)
        in_tx = Transaction.objects.get(tx_type=Transaction.TxType.DEPOSIT_INCOME)
        self.assertEqual(out_tx.wallet_id, provider_wallet.pk)
        self.assertEqual(in_tx.wallet_id, platform_wallet.pk)
        self.assertEqual(out_tx.amount, -30000)
        self.assertEqual(in_tx.amount, 30000)
        # 恒等式 sum(DEPOSIT) == -sum(DEPOSIT_INCOME)：对账脚本的地基
        self.assertEqual(out_tx.amount + in_tx.amount, 0)

        # 平台侧余额快照要自洽，否则按 balance_after 复算的对账会对不上
        self.assertEqual(in_tx.balance_before, 0)
        self.assertEqual(in_tx.balance_after, 30000)
        self.assertEqual(in_tx.status, Transaction.Status.SUCCESS)
        # 押金不属于任何订单（与 WITHDRAW_TAX 同理），order 必须为空
        self.assertIsNone(in_tx.order_id)

    def test_pay_twice_accumulates_on_both_sides(self):
        """分两笔缴清：双边余额与流水条数逐笔累加，不出现合并/覆盖。"""
        platform_wallet = get_platform_wallet()
        self.client.force_authenticate(self.provider)

        self.assertEqual(
            self.client.post(self.DEPOSIT_URL, {'amount': 20000}).data['code'], 0
        )
        self.assertEqual(
            self.client.post(self.DEPOSIT_URL, {'amount': 30000}).data['code'], 0
        )

        provider_wallet = Wallet.objects.get(user=self.provider)
        platform_wallet.refresh_from_db()
        self.assertEqual(provider_wallet.balance, 30000)
        self.assertEqual(platform_wallet.balance, 50000)
        self.assertEqual(self._profile().deposit_paid, 50000)

        out_qs = Transaction.objects.filter(tx_type=Transaction.TxType.DEPOSIT)
        in_qs = Transaction.objects.filter(tx_type=Transaction.TxType.DEPOSIT_INCOME)
        self.assertEqual(out_qs.count(), 2)
        self.assertEqual(in_qs.count(), 2)
        self.assertEqual(sum(t.amount for t in out_qs), -50000)
        self.assertEqual(sum(t.amount for t in in_qs), 50000)

    def test_pay_rolls_back_when_platform_credit_fails(self):
        """平台侧写盘炸掉时，陪玩侧的扣款 / 已缴累加 / 出账流水必须一并回滚。

        构造方式贴近真实故障：平台钱包**能查到**（行锁已拿），倒在
        ``save()`` 上（行锁超时、约束冲突之类）。这样能证明失败点确实落在
        平台入账那一步，而不是在进入 ``atomic`` 之前就短路了。
        """
        platform_wallet = get_platform_wallet()
        self.assertEqual(platform_wallet.balance, 0)

        def _broken_platform_wallet(for_update=False):
            wallet = get_platform_wallet(for_update=for_update)
            # 实例级属性遮蔽类方法，只让平台钱包这一个对象的 save 炸，
            # 不影响同一请求里陪玩钱包的正常写入。
            wallet.save = mock.Mock(side_effect=RuntimeError('平台钱包行锁超时'))
            return wallet

        self.client.force_authenticate(self.provider)
        with mock.patch('wallet.views.get_platform_wallet', _broken_platform_wallet):
            with self.assertRaises(RuntimeError):
                self.client.post(self.DEPOSIT_URL, {'amount': 30000})

        provider_wallet = Wallet.objects.get(user=self.provider)
        platform_wallet.refresh_from_db()
        self.assertEqual(provider_wallet.balance, 80000)
        self.assertEqual(platform_wallet.balance, 0)
        self.assertEqual(self._profile().deposit_paid, 0)
        # 半条流水都不许留：留下 DEPOSIT 而没有 DEPOSIT_INCOME 就是脱账
        self.assertEqual(Transaction.objects.count(), 0)

    def test_pay_rolls_back_when_platform_wallet_unavailable(self):
        """平台钱包压根取不到（系统账户缺失）时同样整体回滚，不留半边账。"""
        self.client.force_authenticate(self.provider)
        with mock.patch(
            'wallet.views.get_platform_wallet',
            side_effect=RuntimeError('平台系统账户不存在'),
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(self.DEPOSIT_URL, {'amount': 30000})

        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 80000)
        self.assertEqual(self._profile().deposit_paid, 0)
        self.assertFalse(
            Transaction.objects.filter(tx_type=Transaction.TxType.DEPOSIT).exists()
        )

    def test_retry_after_platform_failure_reuses_same_request_id(self):
        """回滚要连幂等键一起回滚，否则用户被自己失败的那次请求永久挡在门外。"""
        get_platform_wallet()
        self.client.force_authenticate(self.provider)
        payload = {'amount': 30000, 'client_request_id': 'deposit-retry-1'}

        with mock.patch(
            'wallet.views.get_platform_wallet',
            side_effect=RuntimeError('平台钱包行锁超时'),
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(self.DEPOSIT_URL, payload)

        res = self.client.post(self.DEPOSIT_URL, payload)
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(self._profile().deposit_paid, 30000)
        self.assertEqual(Transaction.objects.count(), 2)

    def test_pay_insufficient_balance(self):
        make_wallet(self.provider, balance=10000)
        self.client.force_authenticate(self.provider)
        res = self.client.post(self.DEPOSIT_URL, {'amount': 20000})
        self.assertEqual(res.data['code'], 400)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 10000)
        self.assertEqual(self._profile().deposit_paid, 0)

    def test_pay_over_remaining_rejected(self):
        self.client.force_authenticate(self.provider)
        res = self.client.post(self.DEPOSIT_URL, {'amount': 60000})
        self.assertEqual(res.data['code'], 400)
        self.assertEqual(self._profile().deposit_paid, 0)

    def test_pay_when_already_full(self):
        profile = self._profile()
        profile.deposit_paid = profile.deposit_required
        profile.save(update_fields=['deposit_paid'])
        self.client.force_authenticate(self.provider)
        res = self.client.post(self.DEPOSIT_URL, {'amount': 1000})
        self.assertEqual(res.data['code'], 400)

    def test_pay_invalid_amount(self):
        self.client.force_authenticate(self.provider)
        res = self.client.post(self.DEPOSIT_URL, {'amount': 0})
        self.assertEqual(res.data['code'], 400)

    def test_non_provider_denied(self):
        customer = make_user()
        make_wallet(customer, balance=80000)
        self.client.force_authenticate(customer)
        res = self.client.post(self.DEPOSIT_URL, {'amount': 10000})
        self.assertEqual(res.data['code'], 403)

    def test_requires_authentication(self):
        res = self.client.get(self.DEPOSIT_URL)
        self.assertEqual(res.status_code, 401)
