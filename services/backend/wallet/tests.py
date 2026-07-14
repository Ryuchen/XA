"""钱包配置回落与提现申请的单元测试。"""

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
