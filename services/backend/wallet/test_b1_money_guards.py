"""B1 资金加固的核心断言：幂等键、税额舍入、通行证配置下沉。"""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.tests.factories import make_provider, make_wallet
from wallet.models import (
    DEFAULT_PASS_DAILY_PRICES,
    DEFAULT_PASS_DELAY_SECONDS,
    IdempotencyKey,
    SystemConfig,
    Wallet,
    WithdrawRequest,
    compute_withdraw_tax,
    get_pass_daily_price,
    get_pass_daily_prices,
    get_pass_delay_seconds,
)
from wallet.services import (
    DuplicateRequestError,
    WalletAddressingError,
    claim_fund_write,
    claim_idempotency_key,
    normalize_request_id,
    require_idempotency_key,
)

User = get_user_model()


def _make_provider_account(suffix='1'):
    """建一个陪玩 legacy user 并映射出 ClubAccount。"""
    user = User.objects.create_user(
        username=f'b1prov{suffix}', password='pass1234', role=User.Role.PROVIDER,
    )
    return get_or_create_account_for_legacy_user(user), user


class ComputeWithdrawTaxTest(SimpleTestCase):
    """税额必须是 ROUND_HALF_UP，且可被人工用计算器复算。"""

    def test_half_rounds_up_not_to_even(self):
        # 银行家舍入下 round(2.5) == 2；税额必须进位成 3。
        self.assertEqual(compute_withdraw_tax(50, 5), 3)   # 2.5 -> 3
        self.assertEqual(compute_withdraw_tax(150, 5), 8)  # 7.5 -> 8

    def test_exact_values(self):
        self.assertEqual(compute_withdraw_tax(10000, 2), 200)
        self.assertEqual(compute_withdraw_tax(9999, 2), 200)  # 199.98 -> 200

    def test_zero_and_negative_inputs(self):
        self.assertEqual(compute_withdraw_tax(0, 10), 0)
        self.assertEqual(compute_withdraw_tax(1000, 0), 0)
        self.assertEqual(compute_withdraw_tax(-100, 10), 0)

    def test_tax_never_exceeds_principal(self):
        """异常税率不得击穿 actual_amount = amount - tax 的非负约束。"""
        self.assertEqual(compute_withdraw_tax(1000, 100), 1000)

    def test_result_is_int(self):
        self.assertIsInstance(compute_withdraw_tax(333, 7), int)


class NormalizeRequestIdTest(SimpleTestCase):
    def test_none_becomes_empty(self):
        self.assertEqual(normalize_request_id(None), '')

    def test_strips_whitespace(self):
        self.assertEqual(normalize_request_id('  abc  '), 'abc')

    def test_truncated_to_field_length(self):
        self.assertEqual(len(normalize_request_id('x' * 200)), 64)


class IdempotencyKeyTest(TestCase):
    def setUp(self):
        self.account, self.user = _make_provider_account('a')

    def test_first_claim_wins_second_loses(self):
        self.assertTrue(claim_idempotency_key(
            account=self.account, request_id='req-1', scope='withdraw',
        ))
        self.assertFalse(claim_idempotency_key(
            account=self.account, request_id='req-1', scope='withdraw',
        ))

    def test_same_key_is_global_across_scopes(self):
        """同一个 request_id 在任何业务上都只允许用一次。"""
        self.assertTrue(claim_idempotency_key(
            account=self.account, request_id='req-2', scope='withdraw',
        ))
        self.assertFalse(claim_idempotency_key(
            account=self.account, request_id='req-2', scope='deposit',
        ))

    def test_keys_are_scoped_per_account(self):
        other_account, _ = _make_provider_account('b')
        self.assertTrue(claim_idempotency_key(
            account=self.account, request_id='shared', scope='withdraw',
        ))
        self.assertTrue(claim_idempotency_key(
            account=other_account, request_id='shared', scope='withdraw',
        ))

    def test_missing_account_or_key_is_rejected(self):
        with self.assertRaises(WalletAddressingError):
            claim_idempotency_key(account=None, request_id='x')
        with self.assertRaises(WalletAddressingError):
            claim_idempotency_key(account=self.account, request_id='  ')

    def test_require_variant_raises_on_duplicate(self):
        require_idempotency_key(account=self.account, request_id='req-3')
        with self.assertRaises(DuplicateRequestError):
            require_idempotency_key(account=self.account, request_id='req-3')

    def test_rollback_releases_the_key(self):
        """业务失败回滚后，用户必须能用同一个键重试。"""
        try:
            with transaction.atomic():
                self.assertTrue(claim_idempotency_key(
                    account=self.account, request_id='req-4', scope='withdraw',
                ))
                raise RuntimeError('business failed')
        except RuntimeError:
            pass
        self.assertFalse(
            IdempotencyKey.objects.filter(
                account=self.account, request_id='req-4',
            ).exists()
        )
        self.assertTrue(claim_idempotency_key(
            account=self.account, request_id='req-4', scope='withdraw',
        ))


class ClaimFundWriteTest(TestCase):
    def setUp(self):
        self.account, self.user = _make_provider_account('c')

    def test_client_key_blocks_replay(self):
        with transaction.atomic():
            allowed, _ = claim_fund_write(
                account=self.account, request_id='cid-1',
                scope='withdraw', fingerprint='100',
            )
            self.assertTrue(allowed)
        with transaction.atomic():
            allowed, reason = claim_fund_write(
                account=self.account, request_id='cid-1',
                scope='withdraw', fingerprint='100',
            )
        self.assertFalse(allowed)
        self.assertTrue(reason)

    def test_derived_key_blocks_double_submit_within_window(self):
        """旧客户端未带键时，同参数连点仍应被拦下。"""
        with transaction.atomic():
            allowed, _ = claim_fund_write(
                account=self.account, request_id=None,
                scope='withdraw', fingerprint='100|WECHAT|acct',
            )
            self.assertTrue(allowed)
        with transaction.atomic():
            allowed, reason = claim_fund_write(
                account=self.account, request_id='',
                scope='withdraw', fingerprint='100|WECHAT|acct',
            )
        self.assertFalse(allowed)
        self.assertTrue(reason)

    def test_derived_key_allows_different_parameters(self):
        with transaction.atomic():
            first, _ = claim_fund_write(
                account=self.account, request_id=None,
                scope='withdraw', fingerprint='100',
            )
        with transaction.atomic():
            second, _ = claim_fund_write(
                account=self.account, request_id=None,
                scope='withdraw', fingerprint='200',
            )
        self.assertTrue(first)
        self.assertTrue(second)

    def test_zero_window_allows_retry(self):
        """窗口过期后视为一次全新操作。"""
        with transaction.atomic():
            claim_fund_write(
                account=self.account, request_id=None,
                scope='deposit', fingerprint='500', window_seconds=0,
            )
        with transaction.atomic():
            allowed, _ = claim_fund_write(
                account=self.account, request_id=None,
                scope='deposit', fingerprint='500', window_seconds=0,
            )
        self.assertTrue(allowed)

    def test_client_cannot_forge_derived_key(self):
        with transaction.atomic():
            allowed, reason = claim_fund_write(
                account=self.account, request_id='auto:deadbeef',
                scope='withdraw', fingerprint='1',
            )
        self.assertFalse(allowed)
        self.assertIn('client_request_id', reason)

    def test_missing_account_is_rejected(self):
        allowed, reason = claim_fund_write(
            account=None, request_id='x', scope='withdraw',
        )
        self.assertFalse(allowed)
        self.assertTrue(reason)

    def test_strict_mode_requires_client_key(self):
        with self.settings(REQUIRE_CLIENT_REQUEST_ID=True):
            with transaction.atomic():
                allowed, reason = claim_fund_write(
                    account=self.account, request_id=None,
                    scope='withdraw', fingerprint='1',
                )
        self.assertFalse(allowed)
        self.assertIn('client_request_id', reason)


class WithdrawIdempotencyApiTest(APITestCase):
    """提现接口的幂等语义（含「校验失败不得吃掉幂等键」这条易错约束）。"""

    URL = '/api/wallet/withdraw/'

    def setUp(self):
        self.provider = make_provider()
        make_wallet(self.provider, balance=50000)
        self.client.force_authenticate(self.provider)

    def _post(self, **overrides):
        payload = {
            'amount': 20000,
            'payee_method': 'WECHAT',
            'payee_account': 'wx123',
            'payee_name': '李四',
        }
        payload.update(overrides)
        return self.client.post(self.URL, payload)

    def test_replay_with_same_key_is_rejected_and_money_moves_once(self):
        first = self._post(client_request_id='cid-withdraw-1')
        second = self._post(client_request_id='cid-withdraw-1')

        self.assertEqual(first.data['code'], 0)
        self.assertEqual(second.data['code'], 409)
        self.assertEqual(
            WithdrawRequest.objects.filter(user=self.provider).count(), 1,
        )
        wallet = Wallet.objects.get(user=self.provider)
        self.assertEqual(wallet.balance, 30000)
        self.assertEqual(wallet.frozen_amount, 20000)

    def test_distinct_keys_allow_two_withdrawals(self):
        self.assertEqual(self._post(client_request_id='cid-a').data['code'], 0)
        self.assertEqual(self._post(client_request_id='cid-b').data['code'], 0)
        self.assertEqual(
            WithdrawRequest.objects.filter(user=self.provider).count(), 2,
        )

    def test_validation_failure_does_not_consume_the_key(self):
        """余额不足被拒后，用户补足余额必须还能用同一个 key 重试。

        这是最容易写错的一处：视图里的 ``return Response(400)`` 是正常返回，
        ``with transaction.atomic()`` 会照常提交。若幂等键在校验之前占位，
        这一笔 key 就被永久烧掉，用户再也提不了这笔现。
        """
        rejected = self._post(amount=60000, client_request_id='cid-retry')
        self.assertEqual(rejected.data['code'], 400)
        self.assertFalse(
            IdempotencyKey.objects.filter(request_id='cid-retry').exists()
        )

        retried = self._post(amount=20000, client_request_id='cid-retry')
        self.assertEqual(retried.data['code'], 0)

    def test_legacy_client_without_key_is_still_deduped(self):
        """未带 client_request_id 的旧客户端，连点仍应只成功一次。"""
        first = self._post()
        second = self._post()
        self.assertEqual(first.data['code'], 0)
        self.assertEqual(second.data['code'], 409)
        self.assertEqual(
            WithdrawRequest.objects.filter(user=self.provider).count(), 1,
        )


class DepositIdempotencyApiTest(APITestCase):
    URL = '/api/wallet/deposit/'

    def setUp(self):
        self.provider = make_provider(deposit_required=50000)
        make_wallet(self.provider, balance=80000)
        self.client.force_authenticate(self.provider)

    def test_replay_pays_deposit_once(self):
        first = self.client.post(
            self.URL, {'amount': 30000, 'client_request_id': 'cid-dep-1'},
        )
        second = self.client.post(
            self.URL, {'amount': 30000, 'client_request_id': 'cid-dep-1'},
        )
        self.assertEqual(first.data['code'], 0)
        self.assertEqual(second.data['code'], 409)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 50000)

    def test_over_remaining_rejection_keeps_key_reusable(self):
        rejected = self.client.post(
            self.URL, {'amount': 60000, 'client_request_id': 'cid-dep-2'},
        )
        self.assertEqual(rejected.data['code'], 400)
        self.assertFalse(
            IdempotencyKey.objects.filter(request_id='cid-dep-2').exists()
        )
        ok = self.client.post(
            self.URL, {'amount': 10000, 'client_request_id': 'cid-dep-2'},
        )
        self.assertEqual(ok.data['code'], 0)


class PassConfigTest(TestCase):
    """通行证定价/延迟已下沉 SystemConfig，未配置时回落出厂默认。"""

    def test_defaults_when_unconfigured(self):
        for tier, price in DEFAULT_PASS_DAILY_PRICES.items():
            self.assertEqual(get_pass_daily_price(tier), price)
        for tier, seconds in DEFAULT_PASS_DELAY_SECONDS.items():
            self.assertEqual(get_pass_delay_seconds(tier), seconds)

    def test_no_pass_tier_uses_longest_delay(self):
        self.assertEqual(
            get_pass_delay_seconds(''), DEFAULT_PASS_DELAY_SECONDS[''],
        )

    def test_system_config_overrides_default(self):
        SystemConfig.objects.create(
            key='provider_pass_daily_price_GOLD', value='4200',
        )
        self.assertEqual(get_pass_daily_price('GOLD'), 4200)

    def test_illegal_config_falls_back(self):
        SystemConfig.objects.create(
            key='provider_pass_daily_price_SILVER', value='not-a-number',
        )
        self.assertEqual(
            get_pass_daily_price('SILVER'),
            DEFAULT_PASS_DAILY_PRICES['SILVER'],
        )

    def test_negative_config_falls_back(self):
        SystemConfig.objects.create(
            key='provider_pass_delay_seconds_BRONZE', value='-5',
        )
        self.assertEqual(
            get_pass_delay_seconds('BRONZE'),
            DEFAULT_PASS_DELAY_SECONDS['BRONZE'],
        )

    def test_price_map_covers_all_sellable_tiers(self):
        self.assertEqual(
            set(get_pass_daily_prices()), set(DEFAULT_PASS_DAILY_PRICES),
        )
