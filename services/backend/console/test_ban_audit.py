"""ORD-4 封禁审计的 **HTTP 层** 回归测试。

与 ``console/tests_ban.py`` 分工：那边测 ban_utils / 序列化器 / DB 约束等
单元级行为；这里**只打真实路由** ``/api/admin/``，覆盖 ``_do_ban`` /
``_do_unban`` / ``BanViewSet`` 这些只有走完整请求链路才碰得到的代码。

分开写是有原因的：单元测试再多也证明不了视图接对了。上一轮 14 条单元测试
全绿，而 `_do_ban` 里的采样数当总数、老板侧越权阻断两个问题一个都没暴露，
因为根本没有一条用例发过 HTTP 请求。
"""

from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from console.ban_utils import BAN_LOCKED_FIELDS
from console.models import AccountBan, AdminAuditLog
from console.serializers import AdminUserSerializer
from orders.models import Order, OrderProvider
from users.models import EscortProfile
from orders.tests.factories import (
    make_console_user,
    make_order,
    make_provider,
    make_superuser,
    make_user,
)


def _account_for(legacy_user):
    """把工厂造出来的 legacy CustomUser 落成 ClubAccount。"""
    return get_or_create_account_for_legacy_user(legacy_user)


def _make_active_order(customer, provider, provider_account, **kwargs):
    """造一笔「占用产能」的在途订单（默认 IN_SERVICE）。

    注意 ``make_order`` 的第二个位置参数是 ``service`` 不是 ``provider``，
    必须用关键字传 provider，否则会把用户对象塞进 service 位置。
    """
    status = kwargs.pop('status', Order.Status.IN_SERVICE)
    order = make_order(customer, provider=provider, status=status, **kwargs)
    order.provider_account = provider_account
    order.save(update_fields=['provider_account'])
    return order


class EscortBanApiTest(APITestCase):
    """陪玩封禁：在途硬阻断 + force 逃生通道。"""

    def setUp(self):
        self.admin = make_superuser()
        self.customer = make_user()
        self.provider = make_provider()
        self.provider_account = _account_for(self.provider)
        self.profile = self.provider.escort_profile
        self.profile.account = self.provider_account
        self.profile.save(update_fields=['account'])

    def _url(self, action='ban'):
        return f'/api/admin/escorts/{self.profile.id}/{action}/'

    # --- 1. 在途硬阻断 ---------------------------------------------------
    def test_active_order_blocks_with_real_count(self):
        order = _make_active_order(
            self.customer, self.provider, self.provider_account,
        )
        audit_before = AdminAuditLog.objects.count()
        self.client.force_authenticate(self.admin)

        res = self.client.post(self._url(), {'reason': '在途也要封'}, format='json')

        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data['code'], 409)
        self.assertEqual(res.data['data']['active_order_count'], 1)
        self.assertEqual(res.data['data']['active_order_ids'], [order.id])
        self.assertFalse(res.data['data']['truncated'])
        # 拦下了就等于没发生：账户开关不动、无封禁记录、不落审计流水。
        self.provider_account.refresh_from_db()
        self.assertTrue(self.provider_account.is_active)
        self.assertTrue(self.provider_account.can_login)
        self.assertFalse(
            AccountBan.objects.filter(account=self.provider_account).exists()
        )
        self.assertEqual(AdminAuditLog.objects.count(), audit_before)

    def test_grabbed_status_also_blocks(self):
        _make_active_order(
            self.customer, self.provider, self.provider_account,
            status=Order.Status.GRABBED,
        )
        self.client.force_authenticate(self.admin)
        res = self.client.post(self._url(), {'reason': '已接单'}, format='json')
        self.assertEqual(res.status_code, 409)

    # --- 2. 采样截断时总数仍须真实 ---------------------------------------
    def test_count_is_real_total_not_sample_length(self):
        """钉死「拿 len(采样) 当总数」的 bug：25 笔在途不能提示成 20 笔。"""
        for _ in range(25):
            _make_active_order(
                self.customer, self.provider, self.provider_account,
            )
        self.client.force_authenticate(self.admin)

        res = self.client.post(self._url(), {'reason': '一堆在途'}, format='json')

        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data['data']['active_order_count'], 25)
        self.assertEqual(len(res.data['data']['active_order_ids']), 20)
        self.assertTrue(res.data['data']['truncated'])
        self.assertIn('25', res.data['msg'])

    # --- 3. 双陪次席 -----------------------------------------------------
    def test_secondary_provider_also_blocked(self):
        """次陪只挂在 OrderProvider 上，Order.provider_account 指向主陪。"""
        main = make_provider()
        main_account = _account_for(main)
        order = _make_active_order(self.customer, main, main_account)
        OrderProvider.objects.create(
            order=order,
            provider=self.provider,
            provider_account=self.provider_account,
        )
        self.client.force_authenticate(self.admin)

        res = self.client.post(self._url(), {'reason': '次陪在途'}, format='json')

        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data['data']['active_order_count'], 1)
        self.assertEqual(res.data['data']['active_order_ids'], [order.id])

    # --- 4. 非在途状态不拦 ------------------------------------------------
    def test_pending_order_does_not_block(self):
        """PENDING 还没人接，封了不会把谁甩在半空。"""
        _make_active_order(
            self.customer, self.provider, self.provider_account,
            status=Order.Status.PENDING,
        )
        self.client.force_authenticate(self.admin)
        res = self.client.post(self._url(), {'reason': '仅待接单'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['code'], 0)

    def test_completed_order_does_not_block(self):
        _make_active_order(
            self.customer, self.provider, self.provider_account,
            status=Order.Status.COMPLETED,
        )
        self.client.force_authenticate(self.admin)
        res = self.client.post(self._url(), {'reason': '已完结'}, format='json')
        self.assertEqual(res.status_code, 200)

    # --- 5. force 越权 ---------------------------------------------------
    def test_force_without_perm_403(self):
        _make_active_order(self.customer, self.provider, self.provider_account)
        operator = make_console_user(perms=['escort:view', 'user:ban'])
        audit_before = AdminAuditLog.objects.count()
        self.client.force_authenticate(operator)

        res = self.client.post(
            self._url(), {'reason': '强封', 'force': True}, format='json',
        )

        self.assertEqual(res.status_code, 403)
        self.assertFalse(
            AccountBan.objects.filter(account=self.provider_account).exists()
        )
        self.assertEqual(AdminAuditLog.objects.count(), audit_before)

    def test_force_perm_checked_before_active_orders(self):
        """无 force 权限者即使目标**没有**在途订单，也照样 403。

        若把在途检查放在权限检查之前，攻击者就能用 403/200 的差异
        反推目标当前是否在接单——一条免费的信息泄露侧信道。
        """
        operator = make_console_user(perms=['escort:view', 'user:ban'])
        self.client.force_authenticate(operator)
        res = self.client.post(
            self._url(), {'reason': '强封', 'force': True}, format='json',
        )
        self.assertEqual(res.status_code, 403)

    # --- 6. force 正常放行 ------------------------------------------------
    def test_force_with_perm_succeeds_and_audits(self):
        _make_active_order(self.customer, self.provider, self.provider_account)
        operator = make_console_user(
            perms=['escort:view', 'user:ban', 'user:ban_force'],
        )
        audit_before = AdminAuditLog.objects.count()
        self.client.force_authenticate(operator)

        res = self.client.post(
            self._url(), {'reason': '强制封禁', 'force': True}, format='json',
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['code'], 0)
        self.provider_account.refresh_from_db()
        self.assertFalse(self.provider_account.can_login)
        self.assertTrue(
            AccountBan.objects.filter(
                account=self.provider_account, status=AccountBan.Status.ACTIVE,
            ).exists()
        )
        # 成功的写操作必须留下且只留下一条审计流水。
        self.assertEqual(AdminAuditLog.objects.count(), audit_before + 1)

    # --- 8. 未绑定业务账户 ------------------------------------------------

    def _orphan_profile(self):
        """造一个 account 为空的陪玩档案，且能扛住测试环境的自动回填。

        直接「置空再发请求」是行不通的，但**不是**因为没法构造，而是因为漏了
        一步。测试环境开着 ``LEGACY_FORCE_AUTH_COMPAT``，每次鉴权都会走
        ``migrate_legacy_test_fixtures()``，它会把 account 为空的行按 legacy
        user 回填回去。

        绕开的关键在那个函数的遍历范围（``club_accounts/services.py``）::

            mapped_ids = LegacyAccountMap.objects.values_list('legacy_user_id', flat=True)
            for legacy_user in get_user_model().objects.exclude(id__in=mapped_ids):
                ...
                link_legacy_relations(legacy_user, account)

        它**只遍历还没进 LegacyAccountMap 的 user**。所以先给 orphan 建好映射，
        它就被 ``exclude`` 掉了，回填循环再也够不到它名下的 EscortProfile。

        （鉴权里另有一次 ``link_legacy_relations(request.user, account)``，但那次
        的过滤条件是 ``legacy_field: legacy_user``，打的是发请求的 admin 自己，
        跟 orphan 无关。）

        剩下的就是用 ``queryset.update()`` 而不是 ``instance.save()`` 置空——
        前者直接写 DB 行，后者只改内存对象、DB 里那行仍是回填后的值。

        这样构造出来的是**真实的 DB 状态**，请求照常走完整路由：URL 里的 pk、
        queryset 过滤、对象级权限、get_object_or_404 一个都没被短路。生产环境
        ``LEGACY_FORCE_AUTH_COMPAT=False`` 且该字段 ``null=True``，孤儿档案本就
        是可达状态，所以这条守卫是真需要，也确实能被真实请求走到。
        """
        orphan = make_provider()
        # 关键第一步：先建映射，把 orphan 挡在回填循环的遍历范围之外。
        get_or_create_account_for_legacy_user(orphan)

        profile = orphan.escort_profile
        # 关键第二步：绕过 save()，直接改 DB 行。
        EscortProfile.objects.filter(pk=profile.pk).update(account=None)

        profile.refresh_from_db()
        # 构造不成立就当场炸，别退化成一条测了个寂寞的绿灯。
        assert profile.account_id is None, (
            '孤儿档案构造失败：account 仍被回填，'
            '请检查 migrate_legacy_test_fixtures 的 exclude 逻辑是否变了'
        )
        return profile

    def test_escort_without_account_400_not_500(self):
        profile = self._orphan_profile()
        self.client.force_authenticate(self.admin)

        res = self.client.post(
            f'/api/admin/escorts/{profile.id}/ban/',
            {'reason': '无账户'}, format='json',
        )

        self.assertEqual(res.status_code, 400)
        self.assertIn('业务账户', res.data['msg'])
        # 请求跑完 account 仍为空 —— 证明 400 是真守卫拦的，
        # 不是「回填后照常封禁」碰巧也返回了 400。
        profile.refresh_from_db()
        self.assertIsNone(profile.account_id)

    def test_unban_escort_without_account_400(self):
        profile = self._orphan_profile()
        self.client.force_authenticate(self.admin)

        res = self.client.post(
            f'/api/admin/escorts/{profile.id}/unban/', {}, format='json',
        )

        self.assertEqual(res.status_code, 400)
        profile.refresh_from_db()
        self.assertIsNone(profile.account_id)


class BossBanApiTest(APITestCase):
    """老板封禁：**不做在途阻断**，改为回传 orphaned_order_*。"""

    def setUp(self):
        self.admin = make_superuser()
        self.boss = make_user(nickname='老板甲')
        self.boss_account = _account_for(self.boss)
        self.boss_account.account_type = ClubAccount.AccountType.BOSS
        self.boss_account.save(update_fields=['account_type'])

    def _url(self, action='ban'):
        return f'/api/admin/users/{self.boss_account.id}/{action}/'

    # --- 7. 老板有在途也照封，但要把孤儿单告诉客服 -----------------------
    def test_boss_with_active_orders_still_banned(self):
        provider = make_provider()
        provider_account = _account_for(provider)
        order = _make_active_order(self.boss, provider, provider_account)
        order.customer_account = self.boss_account
        order.save(update_fields=['customer_account'])
        self.client.force_authenticate(self.admin)

        res = self.client.post(self._url(), {'reason': '盗刷'}, format='json')

        # 放行，不弹 409——理由见 _do_ban docstring（避免客服养成无脑 force）。
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['code'], 0)
        self.boss_account.refresh_from_db()
        self.assertFalse(self.boss_account.can_login)
        # 但必须把「刚被你搞成单边」的订单摆到客服面前。
        self.assertEqual(res.data['data']['orphaned_order_count'], 1)
        self.assertEqual(res.data['data']['orphaned_order_ids'], [order.id])
        self.assertFalse(res.data['data']['orphaned_truncated'])

    def test_boss_without_active_orders_has_no_orphan_fields(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post(self._url(), {'reason': '无在途'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertNotIn('orphaned_order_ids', res.data['data'])

    def test_boss_orphan_list_truncated_but_count_real(self):
        provider = make_provider()
        provider_account = _account_for(provider)
        for _ in range(22):
            order = _make_active_order(self.boss, provider, provider_account)
            order.customer_account = self.boss_account
            order.save(update_fields=['customer_account'])
        self.client.force_authenticate(self.admin)

        res = self.client.post(self._url(), {'reason': '批量'}, format='json')

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data']['orphaned_order_count'], 22)
        self.assertEqual(len(res.data['data']['orphaned_order_ids']), 20)
        self.assertTrue(res.data['data']['orphaned_truncated'])

    # --- 基础流程 ---------------------------------------------------------
    def test_ban_success(self):
        audit_before = AdminAuditLog.objects.count()
        self.client.force_authenticate(self.admin)
        res = self.client.post(self._url(), {'reason': '恶意退单'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.boss_account.refresh_from_db()
        self.assertFalse(self.boss_account.is_active)
        self.assertFalse(self.boss_account.can_login)
        self.assertTrue(
            AccountBan.objects.filter(
                account=self.boss_account, status=AccountBan.Status.ACTIVE,
            ).exists()
        )
        # 这条断言同时是「超管路径下审计中间件确实工作」的证据。
        # 没有它，别处那些「被拦时 AdminAuditLog 数量不变」的断言就是空转——
        # 因为中间件对超管压根不记账的话，不变是必然的，测不出任何东西。
        self.assertEqual(AdminAuditLog.objects.count(), audit_before + 1)
        log = AdminAuditLog.objects.latest('id')
        self.assertEqual(log.resource, 'users')
        self.assertEqual(log.status_code, 200)

    def test_ban_requires_reason(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post(self._url(), {}, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertFalse(AccountBan.objects.filter(account=self.boss_account).exists())

    def test_ban_rejects_blank_reason(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post(self._url(), {'reason': '   '}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_ban_rejects_past_expiry(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post(self._url(), {
            'reason': '限时封禁',
            'expires_at': (timezone.now() - timedelta(hours=1)).isoformat(),
        }, format='json')
        self.assertEqual(res.status_code, 400)

    def test_ban_accepts_future_expiry(self):
        self.client.force_authenticate(self.admin)
        expires = timezone.now() + timedelta(days=3)
        res = self.client.post(self._url(), {
            'reason': '限时封禁', 'expires_at': expires.isoformat(),
        }, format='json')
        self.assertEqual(res.status_code, 200)
        ban = AccountBan.objects.get(account=self.boss_account)
        self.assertIsNotNone(ban.expires_at)

    def test_unban_restores_and_is_idempotent(self):
        self.client.force_authenticate(self.admin)
        self.client.post(self._url(), {'reason': '封'}, format='json')

        first = self.client.post(self._url('unban'), {'reason': '解'}, format='json')
        self.assertEqual(first.status_code, 200)
        self.boss_account.refresh_from_db()
        self.assertTrue(self.boss_account.can_login)

        # 再点一次不该报错。
        second = self.client.post(self._url('unban'), {'reason': '再解'}, format='json')
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data['code'], 0)

    # --- 10. 权限 ---------------------------------------------------------
    def test_user_edit_perm_cannot_ban(self):
        """只有编辑权限的运营不能封人——否则封禁权限点形同虚设。"""
        operator = make_console_user(perms=['user:view', 'user:edit'])
        self.client.force_authenticate(operator)
        res = self.client.post(self._url(), {'reason': '越权'}, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertFalse(AccountBan.objects.filter(account=self.boss_account).exists())

    def test_user_ban_perm_allows(self):
        operator = make_console_user(perms=['user:view', 'user:ban'])
        self.client.force_authenticate(operator)
        res = self.client.post(self._url(), {'reason': '正常封禁'}, format='json')
        self.assertEqual(res.status_code, 200)

    def test_unban_requires_ban_perm(self):
        operator = make_console_user(perms=['user:view', 'user:edit'])
        self.client.force_authenticate(operator)
        res = self.client.post(self._url('unban'), {}, format='json')
        self.assertEqual(res.status_code, 403)


class BanAuditListApiTest(APITestCase):
    """``GET /api/admin/bans/`` 只读审计列表。"""

    def setUp(self):
        self.admin = make_superuser()
        self.a = _account_for(make_user(nickname='甲'))
        self.b = _account_for(make_user(nickname='乙'))
        self.client.force_authenticate(self.admin)
        # 通过服务层直接造数据，避免依赖 ban 接口。
        from console.ban_utils import apply_ban
        self.ban_a = apply_ban(self.a, reason='甲被封')
        self.ban_b = apply_ban(self.b, reason='乙被封')

    def test_list_returns_all(self):
        res = self.client.get('/api/admin/bans/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['total'], 2)

    def test_serializer_renders_account_id(self):
        """顺带钉死 source='account_id' 那个 bug 在真实响应链路上的表现。"""
        res = self.client.get('/api/admin/bans/')
        ids = {row['account_id'] for row in res.data['data']['list']}
        self.assertEqual(ids, {self.a.id, self.b.id})

    def test_filter_by_account_id(self):
        res = self.client.get(f'/api/admin/bans/?account_id={self.a.id}')
        self.assertEqual(res.data['data']['total'], 1)
        self.assertEqual(res.data['data']['list'][0]['reason'], '甲被封')

    def test_filter_by_status(self):
        res = self.client.get('/api/admin/bans/?status=active')
        self.assertEqual(res.data['data']['total'], 2)
        res = self.client.get('/api/admin/bans/?status=lifted')
        self.assertEqual(res.data['data']['total'], 0)

    def test_retrieve(self):
        res = self.client.get(f'/api/admin/bans/{self.ban_a.id}/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['data']['reason'], '甲被封')

    def test_is_read_only(self):
        """审计表不许从 HTTP 层写入，否则审计自身可被篡改。"""
        res = self.client.post('/api/admin/bans/', {'reason': 'x'}, format='json')
        self.assertEqual(res.status_code, 405)

    def test_requires_view_perm(self):
        nobody = make_console_user(perms=[])
        self.client.force_authenticate(nobody)
        res = self.client.get('/api/admin/bans/')
        self.assertEqual(res.status_code, 403)


class ReadOnlyViewSetsRejectWritesTest(APITestCase):
    """只读 ViewSet 的写请求必须是 405，不能是 500。

    ``EnvelopeViewSetMixin`` 排在 MRO 前面，``create``/``update``/``destroy``
    因此始终存在于类上；DRF 的 SimpleRouter 靠 ``hasattr`` 决定要不要把
    POST/PUT/DELETE 映射进 URL，于是 console 里十几个 ReadOnlyModelViewSet
    全都被挂上了写路由，请求打进来再因为缺 ``perform_create`` 抛
    AttributeError —— 对外表现是 500。

    这是存量问题，不是 ORD-4 引入的，但既然被这轮测试照出来就一并修掉：
    mixin 里先确认子类真的混入了对应的 DRF 写 mixin，没有就老实返 405。
    """

    READ_ONLY_LIST_URLS = [
        '/api/admin/bans/',
        '/api/admin/audit-logs/',
        '/api/admin/orders/',
        '/api/admin/wallets/',
        '/api/admin/transactions/',
        '/api/admin/recharge-records/',
    ]

    def setUp(self):
        self.client.force_authenticate(make_superuser())

    def test_post_returns_405(self):
        for url in self.READ_ONLY_LIST_URLS:
            with self.subTest(url=url):
                res = self.client.post(url, {}, format='json')
                self.assertEqual(res.status_code, 405)

    def test_read_still_works(self):
        """确认上面那条不是因为整个端点挂了才 405。"""
        for url in self.READ_ONLY_LIST_URLS:
            with self.subTest(url=url):
                res = self.client.get(url)
                self.assertEqual(res.status_code, 200)

    def test_writable_viewset_unaffected(self):
        """可写 ViewSet 不能被误伤：公告仍然可以正常创建。"""
        res = self.client.post('/api/admin/announcements/', {
            'title': '测试公告', 'content': '正文',
        }, format='json')
        self.assertIn(res.status_code, (200, 201))


class RawSwitchPatchBlockedTest(APITestCase):
    """裸 PATCH 三个开关必须被拒，否则封禁流程存在无审计后门。"""

    def setUp(self):
        self.admin = make_superuser()
        self.boss = make_user()
        self.account = _account_for(self.boss)
        self.account.account_type = ClubAccount.AccountType.BOSS
        self.account.save(update_fields=['account_type'])
        self.url = f'/api/admin/users/{self.account.id}/'
        self.client.force_authenticate(self.admin)

    def test_patch_is_active_rejected(self):
        res = self.client.patch(self.url, {'is_active': False}, format='json')
        self.assertEqual(res.status_code, 400)
        self.account.refresh_from_db()
        self.assertTrue(self.account.is_active)
        # 没有走封禁流程，自然也不该有封禁记录。
        self.assertFalse(AccountBan.objects.filter(account=self.account).exists())

    def test_patch_can_login_rejected(self):
        res = self.client.patch(self.url, {'can_login': False}, format='json')
        self.assertEqual(res.status_code, 400)
        self.account.refresh_from_db()
        self.assertTrue(self.account.can_login)

    def test_patch_can_view_rejected(self):
        res = self.client.patch(self.url, {'can_view': False}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_patch_same_value_allowed(self):
        """后台表单整体提交会带上这三个字段的当前值，值没变就不能拦，
        否则改个昵称都会保存失败。"""
        res = self.client.patch(self.url, {
            'nickname': '改个昵称',
            'is_active': self.account.is_active,
            'can_login': self.account.can_login,
            'can_view': self.account.can_view,
        }, format='json')
        self.assertEqual(res.status_code, 200)
        self.account.refresh_from_db()
        self.assertEqual(self.account.nickname, '改个昵称')

    def test_patch_other_fields_still_work(self):
        res = self.client.patch(self.url, {'real_name': '张三'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.account.refresh_from_db()
        self.assertEqual(self.account.real_name, '张三')

    def test_ban_action_still_flips_switches(self):
        """正门必须通：validate 拦的是裸 PATCH，不能把封禁接口也拦死。"""
        res = self.client.post(f'{self.url}ban/', {'reason': '走正门'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.account.refresh_from_db()
        self.assertFalse(self.account.can_login)
        self.assertFalse(self.account.is_active)

    def test_can_view_rejection_does_not_claim_ban_ownership(self):
        """can_view 不归封禁管，报错就不能说「由封禁流程管理」。

        文案说谎的代价是客服照着提示去点封禁/解封，发现根本改不了 can_view，
        然后回头找研发——一次误导换一次工单，比拦截本身贵。
        """
        res = self.client.patch(self.url, {'can_view': False}, format='json')
        self.assertEqual(res.status_code, 400)
        # console_exception_handler 把 DRF 错误包成 {code,msg,errors}。
        self.assertNotIn('封禁', str(res.data['errors']['can_view'][0]))
        self.assertNotIn('封禁', res.data['msg'])

    def test_ban_managed_fields_stay_in_sync_with_ban_utils(self):
        """序列化器拦的「封禁开关」必须与 ban_utils 真正翻的那组完全一致。

        这两处一旦漂移（一边锁 A、一边拦 A+B），要么放开了后门，要么报错文案
        开始说谎。用例钉死同源，改一边就会红。
        """
        self.assertEqual(
            tuple(AdminUserSerializer.BAN_MANAGED_FIELDS),
            tuple(BAN_LOCKED_FIELDS),
        )
        # 封禁那组必须全部落在「禁止裸 PATCH」名单里，不能有漏网的。
        for field in BAN_LOCKED_FIELDS:
            self.assertIn(field, AdminUserSerializer.LOCKED_SWITCH_REASONS)
