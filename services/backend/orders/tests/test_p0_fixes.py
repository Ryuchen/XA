"""P0 漏洞修复回归用例（对应系统逻辑排查报告 P0-1 / P0-2 / P0-3）。

P0-1 双陪分账超额：订单实付先在两个打手之间**均分**得各自结算基数，
    再由每个打手按**自己的**抽成率从基数里算实得；绝不允许每个打手都以
    全款为基数导致总流出 > 实付。
P0-2 凭空造钱：抽成率 100% 时 provider_income=0 是合法配置，完成结算时
    不得回落成订单全额入账（那是旧代码的 `or amount` 兜底造成的造钱）。
P0-3 钱包寻址：Wallet 同时挂 user / account 两个维度，统一收口到
    ``wallet.services.get_wallet``，命中后自动回填缺失维度、两维度冲突时
    显式抛错，杜绝“查不到就建新钱包撞唯一约束 500 / 资金无法自愈”。
"""

from django.contrib.auth import get_user_model

from rest_framework.test import APITestCase

from club_accounts.models import ClubAccount, LegacyAccountMap
from orders.models import Order
from orders.settlement import (
    ProviderShare,
    SettlementError,
    aggregate_order_split,
    assert_order_conserved,
    build_provider_shares,
    compute_split,
    split_settlement_bases,
)
from orders.tests.factories import (
    make_order,
    make_provider,
    make_service,
    make_user,
    make_wallet,
)
from wallet.models import Wallet
from wallet.services import WalletAddressingError, get_wallet

User = get_user_model()


# ===========================================================================
# P0-1 双陪分账超额
# ===========================================================================
class P0DoubleProviderSplitTest(APITestCase):
    def test_double_provider_equal_base_different_rate_conserved(self):
        # 实付 1000，双陪，A 抽成 20%、B 抽成 30%
        shares = build_provider_shares(
            1000,
            [{'commission_rate': 20}, {'commission_rate': 30}],
        )
        # 基数均分
        self.assertEqual([s.settlement_base for s in shares], [500, 500])
        # 各自按自己费率从基数算实得：400 / 350
        self.assertEqual([s.provider_income for s in shares], [400, 350])

        split = aggregate_order_split(1000, shares)
        # 打手实得合计 750，平台留存 250，三者之和恒等于实付
        self.assertEqual(split.provider_income, 750)
        self.assertEqual(split.inviter_commission, 0)
        self.assertEqual(split.shop_income, 250)
        self.assertEqual(
            split.provider_income + split.inviter_commission + split.shop_income,
            1000,
        )

    def test_three_provider_base_sum_equals_amount(self):
        # 三打手均分 1000，余数给靠前：334 / 333 / 333
        bases = split_settlement_bases(1000, 3)
        self.assertEqual(bases, [334, 333, 333])
        self.assertEqual(sum(bases), 1000)
        # 任意金额均分后总和守恒
        for amount in (1, 7, 999, 100000):
            self.assertEqual(sum(split_settlement_bases(amount, 3)), amount)

    def test_aggregate_rejects_cross_row_overflow(self):
        # 复现旧 bug 的构造：两个打手各自以全款 1000 为基数（抽成 0%）
        # → 总流出 2000 > 实付 1000，聚合阶段必须拦下
        bad = [
            ProviderShare(
                settlement_base=1000, commission_type='PERCENT',
                commission_rate=0, commission_fixed=0, provider_income=1000,
            ),
            ProviderShare(
                settlement_base=1000, commission_type='PERCENT',
                commission_rate=0, commission_fixed=0, provider_income=1000,
            ),
        ]
        with self.assertRaises(SettlementError):
            aggregate_order_split(1000, bad)

    def test_assert_order_conserved_blocks_double_full_amount(self):
        # 结算前最后一道闸门：任何“两行各拿全额”的跨行超付都必须被阻断
        with self.assertRaises(SettlementError):
            assert_order_conserved(1000, [1000, 1000], 0, 0)
        # 恰好等于实付则放行
        self.assertEqual(assert_order_conserved(1000, [800, 200], 0, 0), 1000)


# ===========================================================================
# P0-2 凭空造钱
# ===========================================================================
class P0NoMoneyMintingTest(APITestCase):
    def test_commission_rate_100_no_mint(self):
        # 抽成率 100%：平台拿走全部，陪玩实得 0，绝不造钱
        split = compute_split(10000, 100)
        self.assertEqual(split.provider_income, 0)
        self.assertEqual(split.shop_income, 10000)
        self.assertEqual(split.inviter_commission, 0)
        self.assertEqual(
            split.provider_income + split.inviter_commission + split.shop_income,
            10000,
        )

    def test_provider_100_rate_zero_income(self):
        shares = build_provider_shares(1000, [{'commission_rate': 100}])
        self.assertEqual(shares[0].settlement_base, 1000)
        self.assertEqual(shares[0].provider_income, 0)

    def test_complete_credits_exactly_provider_income_not_amount(self):
        # 视图层守护：完成订单入账金额必须等于拆账实得 8000，
        # 绝不可回落成订单全额 10000（旧 `or amount` 兜底造成的造钱）
        customer = make_user()
        provider = make_provider()
        make_wallet(provider, balance=0)
        service = make_service(price=10000, commission_rate=20)
        order = make_order(
            customer,
            service=service,
            provider=provider,
            status=Order.Status.IN_SERVICE,
            amount=10000,
            provider_income=8000,
            shop_income=2000,
        )
        self.client.force_authenticate(provider)
        res = self.client.post(f'/api/orders/orders/{order.id}/complete/')
        self.assertEqual(res.data['code'], 0)
        provider.wallet.refresh_from_db()
        self.assertEqual(provider.wallet.balance, 8000)

    def test_complete_zero_income_credits_nothing(self):
        # 视图层守护：provider_income=0 时陪玩实得恒为 0，不造钱
        customer = make_user()
        provider = make_provider()
        make_wallet(provider, balance=0)
        service = make_service(price=10000, commission_rate=100)
        order = make_order(
            customer,
            service=service,
            provider=provider,
            status=Order.Status.IN_SERVICE,
            amount=10000,
            provider_income=0,
            shop_income=10000,
        )
        self.client.force_authenticate(provider)
        res = self.client.post(f'/api/orders/orders/{order.id}/complete/')
        self.assertEqual(res.data['code'], 0)
        provider.wallet.refresh_from_db()
        self.assertEqual(provider.wallet.balance, 0)


# ===========================================================================
# P0-3 钱包寻址
# ===========================================================================
class P0WalletAddressingTest(APITestCase):
    def test_get_wallet_backfills_account_dimension(self):
        # 用户先于映射创建 → 钱包 account 维度为 NULL；
        # 之后补齐 LegacyAccountMap，用 user 维度访问应自动回填 account 维度，不报 500。
        user = make_user(role=User.Role.PROVIDER)
        wallet = get_wallet(user_id=user.pk)  # 仅 user 维度建出
        self.assertIsNotNone(wallet)
        self.assertIsNone(wallet.account_id)

        account = ClubAccount.objects.create_account(
            username='p0-acct-backfill',
            password='pass1234',
            account_type=ClubAccount.AccountType.PROVIDER,
        )
        LegacyAccountMap.objects.create(legacy_user_id=user.pk, account=account)

        reloaded = get_wallet(user_id=user.pk)  # 再次访问，应回填 account
        self.assertEqual(reloaded.pk, wallet.pk)
        self.assertEqual(reloaded.account_id, account.pk)

    def test_get_wallet_conflict_raises(self):
        # 脏数据：同一 user↔account 映射下，account 维度与 user 维度指向了
        # 不同的钱包行。寻址必须显式抛错，绝不静默合并/丢失资金。
        user1 = make_user(role=User.Role.PROVIDER)
        user2 = make_user(role=User.Role.PROVIDER)
        wallet1 = get_wallet(user_id=user1.pk)  # user1 → wallet1
        wallet2 = get_wallet(user_id=user2.pk)  # user2 → wallet2

        account = ClubAccount.objects.create_account(
            username='p0-acct-conflict',
            password='pass1234',
            account_type=ClubAccount.AccountType.PROVIDER,
        )
        # 映射声明 account ↔ user1，但脏数据里 account 维度指向了 wallet2(user2)
        LegacyAccountMap.objects.create(legacy_user_id=user1.pk, account=account)
        Wallet.objects.filter(pk=wallet2.pk).update(account_id=account.pk)

        with self.assertRaises(WalletAddressingError):
            get_wallet(user_id=user1.pk)

    def test_get_wallet_create_false_returns_none(self):
        # create=False 时查不到就返回 None，绝不偷偷建钱包
        self.assertIsNone(get_wallet(user_id=999999999, create=False))
