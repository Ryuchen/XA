"""B3-2 T1/T2 回归：后台派单产能闸门 + 已缴押金写保护。

覆盖两件事：

* **T1 产能闸门**：``Order.MAX_CONCURRENT_ORDERS``（同时进行 3 单）此前只有
  陪玩端抢单和 C 端派单在查，后台三条派单入口（指派 ``assign`` / 转单
  ``transfer`` / 快捷派单 ``dispatch``）一路放行——客服可以把第 4、第 5 单压给
  同一个人，把全站唯一的产能保护整条绕过去。
* **T2 已缴押金写保护**：``AdminEscortSerializer`` 把 ``deposit_paid`` 放进了
  ``fields`` 却漏出 ``read_only_fields``，客服在「编辑陪玩」表单里能直接改已缴
  押金，改完账上没有任何一笔流水对得上，对账时这笔差额既无法归因也无法追责。

为什么整份都走真实 HTTP 而不是直接调视图函数或序列化器：产能校验的价值几乎
全在**放在哪一行**——放在事务外、放在扣款之后，单元测试一样全绿，线上照样
击穿、照样先扣钱再拒绝。只有打完整请求链路才能钉住位置。
"""

from club_accounts.services import get_or_create_account_for_legacy_user
from rest_framework.test import APITestCase

from orders.models import Order, OrderProvider
from users.models import EscortProfile
from wallet.models import Wallet
from orders.tests.factories import (
    make_order,
    make_provider,
    make_service,
    make_superuser,
    make_user,
    make_wallet,
)


def _account_for(legacy_user):
    """把工厂造出来的 legacy CustomUser 落成 ClubAccount。"""
    return get_or_create_account_for_legacy_user(legacy_user)


class _CapacityTestBase(APITestCase):
    """三条派单入口共用的夹具：一个超管、一个有钱老板、一个服务。"""

    def setUp(self):
        self.admin = make_superuser()
        self.boss = make_user(nickname='老板甲')
        self.boss_account = _account_for(self.boss)
        make_wallet(self.boss, balance=1000000)
        self.service = make_service(name='上分', price=10000)

    def _new_escort(self, display_name):
        """造一名可接单陪玩，返回 ``(legacy_user, account, profile)``。

        ``EscortProfile.account`` 必须显式回填：工厂只建了 legacy 侧的
        OneToOne，而后台三条派单入口全部走 ``ClubAccount.escort_profile``
        反查，不补这一步会直接 404/「陪玩不存在」，测不到产能这一层。
        """
        user = make_provider(display_name=display_name)
        account = _account_for(user)
        profile = user.escort_profile
        profile.account = account
        profile.save(update_fields=['account'])
        return user, account, profile

    def _load_active(self, provider, account, count,
                     status=Order.Status.IN_SERVICE):
        """给陪玩压 ``count`` 笔在途单（占产能）。

        直接建 Order 并回填 ``provider_account``，口径与
        ``orders.services.active_orders_for`` 的 ``provider_account`` 分支一致。
        """
        orders = []
        for _ in range(count):
            order = make_order(
                self.boss,
                service=self.service,
                provider=provider,
                status=status,
                payment_status=Order.PaymentStatus.PAID,
            )
            order.provider_account = account
            order.save(update_fields=['provider_account'])
            orders.append(order)
        return orders

    def _pending_order(self):
        """造一笔已支付、待接单的单陪订单（``assign`` 的合法入参）。"""
        return make_order(
            self.boss,
            service=self.service,
            status=Order.Status.PENDING,
            payment_status=Order.PaymentStatus.PAID,
        )


# ---------------------------------------------------------------------------
# T1-a 指派（assign）
# ---------------------------------------------------------------------------

class AdminAssignCapacityTest(_CapacityTestBase):
    """``POST /api/admin/orders/<pk>/assign/`` 的产能闸门。"""

    def setUp(self):
        super().setUp()
        self.provider, self.provider_account, self.profile = self._new_escort('阿甲')
        self.client.force_authenticate(self.admin)

    def _url(self, order):
        return f'/api/admin/orders/{order.id}/assign/'

    def test_rejects_when_provider_already_at_capacity(self):
        """3 单在途的陪玩不能再被指派第 4 单。"""
        self._load_active(self.provider, self.provider_account, 3)
        order = self._pending_order()

        res = self.client.post(
            self._url(order), {'provider_id': self.provider_account.pk},
            format='json',
        )

        self.assertEqual(res.data['code'], 400)
        self.assertIn('上限', res.data['msg'])
        self.assertIn('阿甲', res.data['msg'])
        # 拒绝就必须是彻底没发生：订单不动、不留打手明细行。
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertIsNone(order.provider_id)
        self.assertIsNone(order.provider_account_id)
        self.assertFalse(OrderProvider.objects.filter(order=order).exists())

    def test_grabbed_orders_also_count_toward_capacity(self):
        """已接单未开始（GRABBED）同样占产能，不能只认 IN_SERVICE。"""
        self._load_active(
            self.provider, self.provider_account, 3,
            status=Order.Status.GRABBED,
        )
        order = self._pending_order()

        res = self.client.post(
            self._url(order), {'provider_id': self.provider_account.pk},
            format='json',
        )

        self.assertEqual(res.data['code'], 400)
        self.assertIn('上限', res.data['msg'])

    def test_allows_when_below_capacity(self):
        """2 单在途时第 3 单必须放行——闸门不能顺手把正常派单也拦了。"""
        self._load_active(self.provider, self.provider_account, 2)
        order = self._pending_order()

        res = self.client.post(
            self._url(order), {'provider_id': self.provider_account.pk},
            format='json',
        )

        self.assertEqual(res.data['code'], 0)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.GRABBED)
        self.assertEqual(order.provider_account_id, self.provider_account.pk)

    def test_capacity_is_per_escort_not_global(self):
        """甲满员不影响乙接单：名额按人算，不是全站一个池子。"""
        self._load_active(self.provider, self.provider_account, 3)
        _other, other_account, _profile = self._new_escort('阿乙')
        order = self._pending_order()

        res = self.client.post(
            self._url(order), {'provider_id': other_account.pk},
            format='json',
        )

        self.assertEqual(res.data['code'], 0)
        order.refresh_from_db()
        self.assertEqual(order.provider_account_id, other_account.pk)


# ---------------------------------------------------------------------------
# T1-b 转单（transfer）
# ---------------------------------------------------------------------------

class AdminTransferCapacityTest(_CapacityTestBase):
    """``POST /api/admin/orders/<pk>/transfer/`` 的产能闸门。

    转单是**批量**入口（一次可以转多名旧打手），且转入方**可能已经在本单里**，
    所以这里除了「满员要拒」还要盯两个更容易写错的点：
    计数要排除本单、同一转入方在一个批次里只占一个名额。
    """

    def setUp(self):
        super().setUp()
        self.old_a, self.old_a_account, _ = self._new_escort('旧甲')
        self.old_b, self.old_b_account, _ = self._new_escort('旧乙')
        self.new_c, self.new_c_account, _ = self._new_escort('新丙')
        self.client.force_authenticate(self.admin)

    def _service_order(self):
        """造一笔服务中订单（转单的唯一合法起点）。"""
        order = make_order(
            self.boss,
            service=self.service,
            provider=self.old_a,
            status=Order.Status.IN_SERVICE,
            payment_status=Order.PaymentStatus.PAID,
            amount=10000,
            commission_rate=20,
        )
        order.provider_account = self.old_a_account
        order.save(update_fields=['provider_account'])
        return order

    def _add_row(self, order, provider, account, base, income):
        """挂一条打手明细行。``provider_income <= settlement_base`` 是 DB 约束。"""
        return OrderProvider.objects.create(
            order=order,
            provider=provider,
            provider_account=account,
            provider_name_snapshot=provider.escort_profile.display_name,
            settlement_base=base,
            commission_type=OrderProvider.CommissionType.PERCENT,
            commission_rate=20,
            provider_income=income,
        )

    def _url(self, order):
        return f'/api/admin/orders/{order.id}/transfer/'

    @staticmethod
    def _cmd(old_account, new_account):
        """一条「全额转给新人、不罚款」的转单指令。"""
        return {
            'old_provider_id': old_account.pk,
            'new_provider_id': new_account.pk,
            'split_type': OrderProvider.CommissionType.PERCENT,
            'split_value': 0,
            'penalty_amount': 0,
        }

    def test_rejects_when_new_provider_at_capacity(self):
        """转入方已有 3 单在途 -> 整笔转单失败，旧打手原样留在单上。"""
        order = self._service_order()
        self._add_row(order, self.old_a, self.old_a_account, 10000, 8000)
        self._load_active(self.new_c, self.new_c_account, 3)

        res = self.client.post(
            self._url(order),
            {'transfers': [self._cmd(self.old_a_account, self.new_c_account)]},
            format='json',
        )

        self.assertEqual(res.data['code'], 400)
        self.assertIn('上限', res.data['msg'])
        self.assertIn('新丙', res.data['msg'])
        # 事务整体回滚：旧行没被结算、没生成新行、主打手快照未变。
        old_row = OrderProvider.objects.get(order=order, provider=self.old_a)
        self.assertIsNone(old_row.settled_at)
        self.assertEqual(old_row.provider_income, 8000)
        self.assertFalse(
            OrderProvider.objects.filter(
                order=order, provider_account=self.new_c_account,
            ).exists()
        )
        order.refresh_from_db()
        self.assertEqual(order.provider_account_id, self.old_a_account.pk)

    def test_current_order_excluded_from_new_provider_count(self):
        """转入方本来就在本单里（双陪并单）时，不能把「本单」算成额外负担。

        丙已在本单 + 另有 2 笔在途 = 名义 3 单。排除本单后只剩 2 单，
        接下本单的份额并不会让他多背一单，必须放行。少了 ``exclude_order_ids``
        这一条就会被误判成满员。
        """
        order = self._service_order()
        self._add_row(order, self.old_a, self.old_a_account, 5000, 4000)
        self._add_row(order, self.new_c, self.new_c_account, 5000, 4000)
        self._load_active(self.new_c, self.new_c_account, 2)

        res = self.client.post(
            self._url(order),
            {'transfers': [self._cmd(self.old_a_account, self.new_c_account)]},
            format='json',
        )

        self.assertEqual(res.data['code'], 0)
        merged = OrderProvider.objects.get(
            order=order, provider_account=self.new_c_account,
        )
        # 旧甲整份并入丙的行：份额 4000+4000，基数 5000+5000。
        self.assertEqual(merged.provider_income, 8000)
        self.assertEqual(merged.settlement_base, 10000)
        self.assertIsNone(merged.settled_at)

    def test_same_new_provider_in_batch_occupies_one_slot(self):
        """一个批次把两名旧打手转给同一人，只占他一个名额。

        钉死「批次内累加计数」的写法：那种实现下丙会被算成 2+1=3 而在第二条
        指令上被拒。转单是 detail action，整批指令作用于同一笔订单，一个人
        无论承接几份份额都只是这一单。
        """
        order = self._service_order()
        self._add_row(order, self.old_a, self.old_a_account, 5000, 4000)
        self._add_row(order, self.old_b, self.old_b_account, 5000, 4000)
        self._load_active(self.new_c, self.new_c_account, 2)

        res = self.client.post(
            self._url(order),
            {'transfers': [
                self._cmd(self.old_a_account, self.new_c_account),
                self._cmd(self.old_b_account, self.new_c_account),
            ]},
            format='json',
        )

        self.assertEqual(res.data['code'], 0)
        merged = OrderProvider.objects.get(
            order=order, provider_account=self.new_c_account,
        )
        self.assertEqual(merged.provider_income, 8000)
        self.assertEqual(merged.settlement_base, 10000)
        # 两名旧打手都已结算退出本单。
        for old in (self.old_a, self.old_b):
            row = OrderProvider.objects.get(order=order, provider=old)
            self.assertIsNotNone(row.settled_at)

    def test_rejects_before_any_settlement_side_effect(self):
        """满员时必须在动钱之前拒绝：旧打手不能被结算、不能被扣罚款。

        产能校验写在 ``locked_order`` 的转单循环之前就是为了这件事——写在
        循环里意味着第一名旧打手已经结算入账、罚款已扣，才发现转入方接不下。
        """
        order = self._service_order()
        self._add_row(order, self.old_a, self.old_a_account, 10000, 8000)
        make_wallet(self.old_a, balance=50000)
        self._load_active(self.new_c, self.new_c_account, 3)

        res = self.client.post(
            self._url(order),
            {'transfers': [{
                'old_provider_id': self.old_a_account.pk,
                'new_provider_id': self.new_c_account.pk,
                'split_type': OrderProvider.CommissionType.PERCENT,
                'split_value': 100,
                'penalty_amount': 3000,
            }]},
            format='json',
        )

        self.assertEqual(res.data['code'], 400)
        self.assertEqual(Wallet.objects.get(user=self.old_a).balance, 50000)


# ---------------------------------------------------------------------------
# T1-c 快捷派单（dispatch）
# ---------------------------------------------------------------------------

class AdminQuickDispatchCapacityTest(_CapacityTestBase):
    """``POST /api/admin/orders/dispatch/`` 的产能闸门。

    这条路径特殊在**先扣老板的钱再建单**，所以校验位置比另外两条更敏感：
    放晚一步就是"钱扣了、单没建、还回了个 400"。
    """

    URL = '/api/admin/orders/dispatch/'

    def setUp(self):
        super().setUp()
        self.provider, self.provider_account, self.profile = self._new_escort('阿甲')
        self.client.force_authenticate(self.admin)

    def test_rejects_at_capacity_without_charging_boss(self):
        """满员时拒绝，且老板一分钱不能少、订单一笔不能多。"""
        self._load_active(self.provider, self.provider_account, 3)
        order_count = Order.objects.count()

        res = self.client.post(self.URL, {
            'customer_id': self.boss.id,
            'service_id': self.service.id,
            'game_rounds': 1,
            'provider_id': self.provider.id,
        }, format='json')

        self.assertEqual(res.data['code'], 400)
        self.assertIn('上限', res.data['msg'])
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 1000000)
        self.assertEqual(Order.objects.count(), order_count)

    def test_allows_when_below_capacity(self):
        """2 单在途仍可代派第 3 单，正常扣款建单。"""
        self._load_active(self.provider, self.provider_account, 2)

        res = self.client.post(self.URL, {
            'customer_id': self.boss.id,
            'service_id': self.service.id,
            'game_rounds': 1,
            'provider_id': self.provider.id,
        }, format='json')

        self.assertEqual(res.data['code'], 0)
        order = Order.objects.get(id=res.data['data']['id'])
        self.assertEqual(order.status, Order.Status.GRABBED)
        self.assertEqual(order.provider_account_id, self.provider_account.pk)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 990000)

    def test_double_escort_rejects_when_second_provider_at_capacity(self):
        """双陪时两名打手都要过闸：第二名满员同样要拦，且不能扣款。

        钉死「只校验主打手」的写法——那种实现下第二名满员会被放过去。
        """
        _second, second_account, _profile = self._new_escort('阿乙')
        self._load_active(_second, second_account, 3)
        order_count = Order.objects.count()

        res = self.client.post(self.URL, {
            'customer_id': self.boss.id,
            'service_id': self.service.id,
            'game_rounds': 1,
            'escort_mode': Order.EscortMode.DOUBLE,
            'providers': [
                {'provider_id': self.provider.id},
                {'provider_id': _second.id},
            ],
        }, format='json')

        self.assertEqual(res.data['code'], 400)
        self.assertIn('阿乙', res.data['msg'])
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 1000000)
        self.assertEqual(Order.objects.count(), order_count)

    def test_double_escort_providers_do_not_share_slots(self):
        """双陪的两名打手各算各的名额，不能互相顶掉。"""
        _second, second_account, _profile = self._new_escort('阿乙')
        self._load_active(self.provider, self.provider_account, 2)
        self._load_active(_second, second_account, 2)

        res = self.client.post(self.URL, {
            'customer_id': self.boss.id,
            'service_id': self.service.id,
            'game_rounds': 1,
            'escort_mode': Order.EscortMode.DOUBLE,
            'providers': [
                {'provider_id': self.provider.id},
                {'provider_id': _second.id},
            ],
        }, format='json')

        self.assertEqual(res.data['code'], 0)
        order = Order.objects.get(id=res.data['data']['id'])
        self.assertEqual(
            OrderProvider.objects.filter(order=order).count(), 2,
        )


# ---------------------------------------------------------------------------
# T2 已缴押金写保护
# ---------------------------------------------------------------------------

class AdminEscortDepositFieldTest(APITestCase):
    """``PATCH /api/admin/escorts/<pk>/`` 不得直改 ``deposit_paid``。

    为什么是「报错」而不是「静默忽略」：前端编辑陪玩表单整体提交，每次保存都
    会无条件回传 ``deposit_paid`` 的当前值。若把字段设成 read_only，DRF 会悄悄
    丢弃这个键——客服改完押金点保存、页面提示成功、值却纹丝不动，现场只会
    归因成「系统抽风」。所以走 ``validate()`` 显式拒绝，让失败可见可追。
    """

    def setUp(self):
        self.admin = make_superuser()
        self.provider = make_provider(display_name='阿甲')
        self.account = _account_for(self.provider)
        self.profile = self.provider.escort_profile
        self.profile.account = self.account
        self.profile.deposit_required = 50000
        self.profile.deposit_paid = 30000
        self.profile.save(update_fields=[
            'account', 'deposit_required', 'deposit_paid',
        ])
        self.client.force_authenticate(self.admin)

    def _url(self):
        return f'/api/admin/escorts/{self.profile.id}/'

    def test_changing_deposit_paid_is_rejected(self):
        res = self.client.patch(
            self._url(), {'deposit_paid': 99999}, format='json',
        )

        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data['code'], 400)
        self.assertIn('deposit_paid', res.data['errors'])
        self.assertIn('缴纳/退还流程', res.data['msg'])
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.deposit_paid, 30000)

    def test_zeroing_deposit_paid_is_rejected(self):
        """改小同样要拦——「清零重置」是最容易被当成运维手段的越权写法。"""
        res = self.client.patch(
            self._url(), {'deposit_paid': 0}, format='json',
        )

        self.assertEqual(res.status_code, 400)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.deposit_paid, 30000)

    def test_echoing_same_deposit_paid_still_saves_other_fields(self):
        """表单整体提交：带上原值的 ``deposit_paid`` 不能让改昵称失败。"""
        res = self.client.patch(self._url(), {
            'display_name': '阿甲改名',
            'deposit_paid': 30000,
        }, format='json')

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['code'], 0)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.display_name, '阿甲改名')
        self.assertEqual(self.profile.deposit_paid, 30000)

    def test_deposit_required_remains_editable(self):
        """应缴押金是运营策略参数不是资金余额，改它不动钱，必须放行。"""
        res = self.client.patch(
            self._url(), {'deposit_required': 80000}, format='json',
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['code'], 0)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.deposit_required, 80000)
        self.assertEqual(self.profile.deposit_paid, 30000)

    def test_status_and_verify_flags_unaffected(self):
        """闸门只针对资金字段，别顺手把普通字段一起锁了。"""
        res = self.client.patch(self._url(), {
            'status': EscortProfile.Status.OFFLINE,
            'is_verified': True,
        }, format='json')

        self.assertEqual(res.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.status, EscortProfile.Status.OFFLINE)
        self.assertTrue(self.profile.is_verified)
