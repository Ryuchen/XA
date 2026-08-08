"""DP-1 押金退还的验收断言。

覆盖：四件事同生共死、两道平台侧护栏、状态校验、幂等（peek / claim）、
权限与绑定前置、缴→退恒等式、以及 active_order_count 只软提示不拦截。

金额一律内部账务单位整数（10 = 1 兴安币）。
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.models import Sum
from rest_framework.test import APITestCase

from club_accounts.services import (
    get_or_create_account_for_legacy_user,
    link_legacy_relations,
)
from console.deposit_refund import refund_deposit
from console.views import EscortViewSet
from orders.models import Order
from orders.tests.factories import (
    make_console_user,
    make_order,
    make_provider,
    make_service,
    make_superuser,
    make_user,
    make_wallet,
)
from users.models import EscortProfile
from wallet.models import IdempotencyKey, Transaction, Wallet, get_platform_wallet

User = get_user_model()


def bind_account(user):
    """给 legacy 用户补一个 ClubAccount 并回填到陪玩档案 / 钱包。

    ``make_provider`` 只建 legacy ``CustomUser``，而押金退还全链路以
    ``ClubAccount`` 为主维度，测试里必须显式补齐这一层映射。
    """
    account = get_or_create_account_for_legacy_user(user)
    link_legacy_relations(user, account)
    EscortProfile.objects.filter(user=user).update(account=account)
    Wallet.objects.filter(user=user).update(account=account)
    return account


def fund_platform_deposit_pool(amount):
    """往平台钱包里注入一笔「押金性质」的余额，模拟此前已收过押金。

    只加余额不记 ``DEPOSIT_INCOME`` 流水的话，护栏②（押金池）会拦下退还，
    因此两件事必须一起做 —— 这正是生产上缴纳侧的落库形态。
    """
    platform = get_platform_wallet()
    before = platform.balance
    platform.balance += amount
    platform.save(update_fields=['balance'])
    Transaction.objects.create(
        wallet=platform,
        amount=amount,
        tx_type=Transaction.TxType.DEPOSIT_INCOME,
        balance_before=before,
        balance_after=platform.balance,
        status=Transaction.Status.SUCCESS,
        remark='收取押金：测试夹具',
    )
    return platform


class DepositRefundApiTest(APITestCase):
    """后台押金退还接口 ``POST /api/admin/escorts/<id>/deposit-refund/``。"""

    def setUp(self):
        self.admin = make_superuser()
        self.provider = make_provider(deposit_required=5000, deposit_paid=3000)
        self.account = bind_account(self.provider)
        make_wallet(self.provider, balance=1000)
        Wallet.objects.filter(user=self.provider).update(account=self.account)
        fund_platform_deposit_pool(3000)
        self.client.force_authenticate(self.admin)

    @property
    def url(self):
        return f'/api/admin/escorts/{self.provider.escort_profile.id}/deposit-refund/'

    def _post(self, **overrides):
        payload = {'amount': 1000, 'reason': '离职结算'}
        payload.update(overrides)
        return self.client.post(self.url, payload)

    # ---- ① 四件事同生共死 ----
    def test_refund_moves_all_four_things(self):
        res = self._post(amount=1000, reason='离职结算')
        self.assertEqual(res.data['code'], 0)

        wallet = Wallet.objects.get(user=self.provider)
        self.assertEqual(wallet.balance, 2000)  # 1000 + 1000

        profile = self.provider.escort_profile
        profile.refresh_from_db()
        self.assertEqual(profile.deposit_paid, 2000)  # 3000 - 1000

        escort_tx = Transaction.objects.get(
            wallet=wallet, tx_type=Transaction.TxType.DEPOSIT,
        )
        self.assertEqual(escort_tx.amount, 1000)
        self.assertEqual(escort_tx.balance_before, 1000)
        self.assertEqual(escort_tx.balance_after, 2000)
        self.assertIn('离职结算', escort_tx.remark)
        self.assertEqual(escort_tx.operator_id, self.admin.id)

        platform = get_platform_wallet()
        self.assertEqual(platform.balance, 2000)  # 3000 - 1000
        platform_tx = Transaction.objects.filter(
            wallet=platform, tx_type=Transaction.TxType.DEPOSIT_INCOME,
        ).order_by('-id').first()
        self.assertEqual(platform_tx.amount, -1000)
        self.assertEqual(platform_tx.balance_after, 2000)

        data = res.data['data']
        self.assertEqual(data['deposit_paid'], 2000)
        self.assertEqual(data['deposit_required'], 5000)
        self.assertEqual(data['deposit_remaining'], 3000)
        self.assertEqual(data['balance'], 2000)

    # ---- ② 平台钱包总余额不足 ----
    def test_rejects_when_platform_balance_insufficient(self):
        platform = get_platform_wallet()
        platform.balance = 500
        platform.save(update_fields=['balance'])

        res = self._post(amount=1000)
        self.assertEqual(res.data['code'], 400)
        self.assertIn('平台钱包余额不足', res.data['msg'])
        self._assert_nothing_moved()

    # ---- ③ 平台押金池不足（总余额够，但不是押金的钱）----
    def test_rejects_when_deposit_pool_insufficient(self):
        platform = get_platform_wallet()
        # 清空押金流水，改为等额的抽成收入：总余额仍然够，押金池为 0。
        Transaction.objects.filter(
            wallet=platform, tx_type=Transaction.TxType.DEPOSIT_INCOME,
        ).delete()
        Transaction.objects.create(
            wallet=platform,
            amount=3000,
            tx_type=Transaction.TxType.SHOP_INCOME,
            balance_before=0,
            balance_after=3000,
            status=Transaction.Status.SUCCESS,
            remark='平台抽成',
        )

        res = self._post(amount=1000)
        self.assertEqual(res.data['code'], 400)
        self.assertIn('平台押金账户余额不足', res.data['msg'])
        self._assert_nothing_moved()

    # ---- ④ 超过已缴押金 ----
    def test_rejects_amount_over_deposit_paid(self):
        fund_platform_deposit_pool(10000)
        res = self._post(amount=5000)
        self.assertEqual(res.data['code'], 400)
        self.assertIn('退还金额超过已缴押金', res.data['msg'])
        # 文案按兴安币展示：3000 内部单位 = 300 兴安币
        self.assertIn('300', res.data['msg'])
        self._assert_nothing_moved()

    # ---- ⑤ 无已缴押金 ----
    def test_rejects_when_no_deposit_paid(self):
        EscortProfile.objects.filter(user=self.provider).update(deposit_paid=0)
        res = self._post(amount=1000)
        self.assertEqual(res.data['code'], 400)
        self.assertEqual(res.data['msg'], '该陪玩无已缴押金')

    # ---- ⑥ 原因必填 ----
    def test_rejects_blank_reason(self):
        res = self._post(reason='   ')
        self.assertEqual(res.data['code'], 400)
        self.assertEqual(res.data['msg'], '请填写退还原因')
        self._assert_nothing_moved()

    def test_rejects_non_positive_amount(self):
        for bad in (0, -100, 'abc'):
            res = self._post(amount=bad)
            self.assertEqual(res.data['code'], 400, bad)
            self.assertEqual(res.data['msg'], '金额非法')

    # ---- ⑦ peek 命中（重放已成功请求）----
    def test_replay_with_same_key_hits_peek_409(self):
        first = self._post(amount=1000, client_request_id='cid-refund-1')
        self.assertEqual(first.data['code'], 0)

        second = self._post(amount=1000, client_request_id='cid-refund-1')
        self.assertEqual(second.data['code'], 409)

        # 钱只动了一次
        wallet = Wallet.objects.get(user=self.provider)
        self.assertEqual(wallet.balance, 2000)
        self.assertEqual(
            Transaction.objects.filter(
                wallet=wallet, tx_type=Transaction.TxType.DEPOSIT,
            ).count(), 1,
        )

    # ---- ⑧ claim 命中（同参数连点，未带客户端键的降级窗口）----
    def test_derived_key_blocks_rapid_duplicate(self):
        fund_platform_deposit_pool(10000)
        first = self._post(amount=500)
        second = self._post(amount=500)
        self.assertEqual(first.data['code'], 0)
        self.assertEqual(second.data['code'], 409)
        self.assertEqual(
            Transaction.objects.filter(
                wallet__user=self.provider, tx_type=Transaction.TxType.DEPOSIT,
            ).count(), 1,
        )

    def test_校验失败不吃掉幂等键(self):
        """金额超额被拒后，运营改小金额必须能用同一个键重试。"""
        bad = self._post(amount=99999, client_request_id='cid-retry')
        self.assertEqual(bad.data['code'], 400)
        self.assertFalse(
            IdempotencyKey.objects.filter(request_id='cid-retry').exists()
        )
        good = self._post(amount=1000, client_request_id='cid-retry')
        self.assertEqual(good.data['code'], 0)

    # ---- ⑨ 无权限 ----
    def test_requires_permission(self):
        operator = make_console_user(perms=['escort:view', 'escort:edit'])
        self.client.force_authenticate(operator)
        res = self.client.post(self.url, {'amount': 1000, 'reason': 'x'})
        self.assertEqual(res.status_code, 403)
        self._assert_nothing_moved()

    def test_granted_permission_passes(self):
        operator = make_console_user(perms=['escort:view', 'escort:deposit_refund'])
        self.client.force_authenticate(operator)
        res = self.client.post(self.url, {'amount': 1000, 'reason': '离职结算'})
        self.assertEqual(res.data['code'], 0)

    # ---- ⑩ 未绑定业务账户 ----
    def test_unbound_profile_returns_403(self):
        """未绑定业务账户的陪玩不允许退押金。

        测试环境开着 ``LEGACY_FORCE_AUTH_COMPAT``，后台鉴权兼容层会在**每次**
        请求里跑 ``migrate_legacy_test_fixtures()``，把全站 legacy 用户统统补上
        ClubAccount 并回填 ``EscortProfile.account`` —— 也就是说在测试库里
        「未绑定」这个状态活不过一次请求。生产环境该兼容层是关的，视图这道
        前置分支仍是真实防线，因此这里用打桩的 ``get_object`` 精确驱动该分支，
        真正未绑定的落库场景由 ``RefundDepositServiceTest`` 在服务层覆盖。
        """
        lonely = make_provider(deposit_paid=1000)
        profile = lonely.escort_profile
        profile.account = None  # 只改内存态：兼容层落库的绑定不影响本分支判定

        with patch.object(EscortViewSet, 'get_object', return_value=profile):
            res = self.client.post(
                f'/api/admin/escorts/{profile.id}/deposit-refund/',
                {'amount': 100, 'reason': 'x'},
            )
        self.assertEqual(res.data['code'], 403)
        self.assertEqual(res.data['msg'], '该陪玩未绑定业务账户')
        # 前置分支拦下后不得动任何钱
        self.assertFalse(
            Transaction.objects.filter(wallet__user=lonely).exists(),
        )

    def test_missing_profile_returns_404(self):
        res = self.client.post(
            '/api/admin/escorts/999999/deposit-refund/',
            {'amount': 100, 'reason': 'x'},
        )
        self.assertEqual(res.data['code'], 404)
        self.assertEqual(res.data['msg'], '陪玩不存在')

    # ---- ⑫ active_order_count 是软提示，不拦截 ----
    def test_active_orders_do_not_block_refund(self):
        boss = make_user()
        service = make_service()
        make_order(
            boss,
            service=service,
            provider=self.provider,
            status=Order.Status.IN_SERVICE,
            provider_account=self.account,
        )
        make_order(
            boss,
            service=service,
            provider=self.provider,
            status=Order.Status.GRABBED,
            provider_account=self.account,
        )
        res = self._post(amount=1000)
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['active_order_count'], 2)

    def _assert_nothing_moved(self):
        """校验失败的分支必须一分钱都没动。"""
        wallet = Wallet.objects.get(user=self.provider)
        self.assertEqual(wallet.balance, 1000)
        profile = self.provider.escort_profile
        profile.refresh_from_db()
        self.assertEqual(profile.deposit_paid, 3000)
        self.assertFalse(
            Transaction.objects.filter(
                wallet=wallet, tx_type=Transaction.TxType.DEPOSIT,
            ).exists()
        )


class DepositPayRefundIdentityTest(APITestCase):
    """⑪ 缴→退恒等式：``sum(DEPOSIT) == -sum(DEPOSIT_INCOME)`` 必须始终成立。"""

    def setUp(self):
        self.admin = make_superuser()
        self.provider = make_provider(deposit_required=5000, deposit_paid=0)
        self.account = bind_account(self.provider)
        make_wallet(self.provider, balance=5000)
        Wallet.objects.filter(user=self.provider).update(account=self.account)

    @staticmethod
    def _sum(tx_type):
        total = Transaction.objects.filter(
            tx_type=tx_type, status=Transaction.Status.SUCCESS,
        ).aggregate(total=Sum('amount'))['total']
        return total or 0

    def test_identity_holds_after_pay_then_refund(self):
        # 缴纳：陪玩自己走 C 端接口
        self.client.force_authenticate(self.provider)
        pay = self.client.post('/api/wallet/deposit/', {'amount': 3000})
        self.assertEqual(pay.data['code'], 0)
        self.assertEqual(self._sum(Transaction.TxType.DEPOSIT), -3000)
        self.assertEqual(self._sum(Transaction.TxType.DEPOSIT_INCOME), 3000)

        # 退还：运营在后台退一半
        self.client.force_authenticate(self.admin)
        refund = self.client.post(
            f'/api/admin/escorts/{self.provider.escort_profile.id}/deposit-refund/',
            {'amount': 1200, 'reason': '部分退还'},
        )
        self.assertEqual(refund.data['code'], 0)

        deposit_sum = self._sum(Transaction.TxType.DEPOSIT)
        income_sum = self._sum(Transaction.TxType.DEPOSIT_INCOME)
        self.assertEqual(deposit_sum, -1800)
        self.assertEqual(income_sum, 1800)
        # 恒等式：两侧互为相反数，且等于当前在缴押金总额
        self.assertEqual(deposit_sum, -income_sum)
        profile = self.provider.escort_profile
        profile.refresh_from_db()
        self.assertEqual(income_sum, profile.deposit_paid)

    def test_full_refund_zeroes_out_both_sides(self):
        self.client.force_authenticate(self.provider)
        self.client.post('/api/wallet/deposit/', {'amount': 3000})

        self.client.force_authenticate(self.admin)
        res = self.client.post(
            f'/api/admin/escorts/{self.provider.escort_profile.id}/deposit-refund/',
            {'amount': 3000, 'reason': '全额退还'},
        )
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(self._sum(Transaction.TxType.DEPOSIT), 0)
        self.assertEqual(self._sum(Transaction.TxType.DEPOSIT_INCOME), 0)

        profile = self.provider.escort_profile
        profile.refresh_from_db()
        self.assertEqual(profile.deposit_paid, 0)
        wallet = Wallet.objects.get(user=self.provider)
        self.assertEqual(wallet.balance, 5000)  # 钱原样回到陪玩手里
        self.assertEqual(get_platform_wallet().balance, 0)


class RefundDepositServiceTest(APITestCase):
    """直接调用服务函数的边界：不经过路由层的前置分支。"""

    def setUp(self):
        self.admin = make_superuser()
        self.admin_account = get_or_create_account_for_legacy_user(self.admin)
        self.provider = make_provider(deposit_required=5000, deposit_paid=2000)
        self.account = bind_account(self.provider)
        make_wallet(self.provider, balance=0)
        Wallet.objects.filter(user=self.provider).update(account=self.account)
        fund_platform_deposit_pool(2000)

    class _Req:
        """最小请求替身：服务函数只用到 account / legacy_user。"""

        def __init__(self, account, legacy_user):
            self.account = account
            self.legacy_user = legacy_user

    def test_missing_profile_returns_404(self):
        req = self._Req(self.admin_account, self.admin)
        code, msg, data = refund_deposit(req, 999999, 100, '理由', None)
        self.assertEqual(code, 404)
        self.assertEqual(msg, '陪玩不存在')
        self.assertIsNone(data)

    def test_unbound_profile_returns_403(self):
        """真正落库的「未绑定业务账户」场景：服务函数自身必须拦下。

        服务层不经过后台鉴权兼容层，``EscortProfile.account`` 为空的状态能
        原样保留到事务里，因此这里能真实覆盖生产上的那道防线。
        """
        lonely = make_provider(deposit_paid=1000)
        EscortProfile.objects.filter(user=lonely).update(account=None)
        Wallet.objects.filter(user=lonely).update(account=None)

        req = self._Req(self.admin_account, self.admin)
        code, msg, data = refund_deposit(
            req, lonely.escort_profile.pk, 100, '理由', None,
        )
        self.assertEqual(code, 403)
        self.assertEqual(msg, '该陪玩未绑定业务账户')
        self.assertIsNone(data)
        # 押金没动，也没占用幂等键
        self.assertEqual(
            EscortProfile.objects.get(user=lonely).deposit_paid, 1000,
        )
        self.assertFalse(Transaction.objects.filter(wallet__user=lonely).exists())

    def test_success_returns_overview(self):
        req = self._Req(self.admin_account, self.admin)
        code, msg, data = refund_deposit(
            req, self.provider.escort_profile.pk, 500, '理由', 'svc-cid-1',
        )
        self.assertEqual(code, 0)
        self.assertEqual(msg, '押金退还成功')
        self.assertEqual(data['deposit_paid'], 1500)
        self.assertEqual(data['balance'], 500)
        self.assertEqual(data['active_order_count'], 0)
