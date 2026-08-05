"""ORD-4 封禁审计接线的回归测试。

覆盖：
  1. BanRecordSerializer 字段可构建（原 source='account_id' 会在取 .fields 时炸）
  2. 权限点注册
  3. 封禁写入 AccountBan 并翻转开关
  4. can_view 不参与封禁语义
  5. pre_ban_state 快照 + 按快照回滚
  6. 在途订单硬阻断 409
  7. force 需要 user:ban_force，越权 403
  8. force 可跳过在途拦截
  9. 双陪次陪也算在途
 10. 解封幂等
 11. 单账户至多一条 ACTIVE 封禁（DB 级约束）
 12. 到期自动解封落 EXPIRED
 13. 裸 PATCH 三开关被拒
"""

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from console.ban_utils import (
    BAN_LOCKED_FIELDS,
    active_ban_for,
    apply_ban,
    lift_expired_bans,
)
from console.models import AccountBan
from console.permissions import PERMISSION_GROUPS
from console.serializers import BanRecordSerializer
from orders.models import Order, OrderProvider
from orders.services import active_orders_for, count_active_orders
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


class BanRecordSerializerTest(APITestCase):
    """必测 #1：原实现 `source='account_id'` 会在字段 bind 时抛 AssertionError。

    关键：**不能只写 `BanRecordSerializer()` 就当通过**。DRF 的 `fields` 是
    惰性 cached_property，构造函数根本不 bind 字段，断言在 `.fields` /
    `.data` 时才触发。只测构造是一条必然为绿的假测试。
    """

    def test_fields_can_be_built(self):
        serializer = BanRecordSerializer()
        # 这一行才是真正触发 bind 的地方。
        self.assertIn('account_id', serializer.fields)

    def test_data_of_real_instance(self):
        account = _account_for(make_user(nickname='被封的人'))
        ban = apply_ban(account, reason='测试封禁')
        data = BanRecordSerializer(ban).data
        self.assertEqual(data['account_id'], account.id)
        self.assertEqual(data['account_nickname'], '被封的人')
        self.assertEqual(data['status'], AccountBan.Status.ACTIVE)
        self.assertEqual(data['reason'], '测试封禁')


class BanPermissionRegistryTest(APITestCase):
    """必测 #2：新权限点必须进注册表，否则前端分配树里根本勾不到。"""

    def test_ban_perms_registered(self):
        codes = {
            code
            for group in PERMISSION_GROUPS
            for code, _label in group['permissions']
        }
        self.assertIn('user:ban', codes)
        self.assertIn('user:ban_force', codes)


class ApplyBanTest(APITestCase):
    def setUp(self):
        self.account = _account_for(make_user())

    def test_ban_flips_switches_and_records(self):
        """必测 #3：封禁落审计记录并锁死登录。"""
        ban = apply_ban(self.account, reason='刷单')
        self.account.refresh_from_db()
        self.assertEqual(ban.status, AccountBan.Status.ACTIVE)
        self.assertEqual(ban.reason, '刷单')
        self.assertFalse(self.account.is_active)
        self.assertFalse(self.account.can_login)

    def test_can_view_not_touched(self):
        """必测 #4：can_view 不属于封禁语义，封禁不许动它。"""
        self.assertNotIn('can_view', BAN_LOCKED_FIELDS)
        self.account.can_view = True
        self.account.save(update_fields=['can_view'])
        apply_ban(self.account, reason='测试')
        self.account.refresh_from_db()
        self.assertTrue(self.account.can_view)

    def test_empty_reason_rejected(self):
        with self.assertRaises(ValueError):
            apply_ban(self.account, reason='   ')

    def test_pre_ban_state_snapshot_and_restore(self):
        """必测 #5：本来就停用的账户，解封后不许被凭空恢复成正常账户。"""
        self.account.is_active = False
        self.account.can_login = True
        self.account.save(update_fields=['is_active', 'can_login'])

        ban = apply_ban(self.account, reason='停用中又被封')
        self.assertEqual(
            ban.pre_ban_state, {'is_active': False, 'can_login': True},
        )

        from console.ban_utils import lift_ban
        lift_ban(ban, reason='解封')
        self.account.refresh_from_db()
        # 回到「停用但可登录」的原始状态，而不是无脑 True。
        self.assertFalse(self.account.is_active)
        self.assertTrue(self.account.can_login)

    def test_empty_snapshot_falls_back_to_true(self):
        """历史数据（迁移前建的记录）快照为空时回落 True，保持旧行为。"""
        ban = apply_ban(self.account, reason='测试')
        AccountBan.objects.filter(pk=ban.pk).update(pre_ban_state={})
        ban.refresh_from_db()

        from console.ban_utils import lift_ban
        lift_ban(ban, reason='解封')
        self.account.refresh_from_db()
        self.assertTrue(self.account.is_active)
        self.assertTrue(self.account.can_login)

    def test_reban_inherits_snapshot(self):
        """二次封禁必须继承旧快照，否则会把「已封死」的状态当原始状态。"""
        self.account.is_active = False
        self.account.save(update_fields=['is_active'])
        first = apply_ban(self.account, reason='第一次')
        second = apply_ban(self.account, reason='第二次')

        first.refresh_from_db()
        self.assertEqual(first.status, AccountBan.Status.LIFTED)
        self.assertEqual(second.pre_ban_state, first.pre_ban_state)
        self.assertFalse(second.pre_ban_state['is_active'])

    def test_at_most_one_active_ban(self):
        """必测 #11：DB 级偏索引唯一约束兜住并发双写。"""
        apply_ban(self.account, reason='第一次')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AccountBan.objects.create(
                    account=self.account,
                    reason='并发绕过应用层',
                    status=AccountBan.Status.ACTIVE,
                )

    def test_lifted_bans_do_not_collide(self):
        """约束是偏索引，只约束 ACTIVE，历史记录可以堆叠。"""
        apply_ban(self.account, reason='一')
        apply_ban(self.account, reason='二')
        apply_ban(self.account, reason='三')
        self.assertEqual(self.account.bans.count(), 3)
        self.assertEqual(
            self.account.bans.filter(status=AccountBan.Status.ACTIVE).count(), 1,
        )


class LiftExpiredBansTest(APITestCase):
    def test_expired_ban_marked_expired_not_lifted(self):
        """必测 #12：到期自动解封落 EXPIRED，与人工 LIFTED 区分开。"""
        account = _account_for(make_user())
        ban = apply_ban(
            account, reason='限时封禁',
            expires_at=timezone.now() + timedelta(hours=1),
        )
        # 直接改库模拟时间流逝，避免 sleep。
        AccountBan.objects.filter(pk=ban.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        self.assertEqual(lift_expired_bans(), 1)
        ban.refresh_from_db()
        account.refresh_from_db()
        self.assertEqual(ban.status, AccountBan.Status.EXPIRED)
        self.assertTrue(account.is_active)
        self.assertTrue(account.can_login)

    def test_unexpired_ban_untouched(self):
        account = _account_for(make_user())
        apply_ban(
            account, reason='还没到期',
            expires_at=timezone.now() + timedelta(days=1),
        )
        self.assertEqual(lift_expired_bans(), 0)

    def test_permanent_ban_untouched(self):
        account = _account_for(make_user())
        apply_ban(account, reason='永久封禁', expires_at=None)
        self.assertEqual(lift_expired_bans(), 0)

    def test_idempotent(self):
        account = _account_for(make_user())
        ban = apply_ban(account, reason='限时')
        AccountBan.objects.filter(pk=ban.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        self.assertEqual(lift_expired_bans(), 1)
        self.assertEqual(lift_expired_bans(), 0)

    def test_management_command(self):
        from io import StringIO
        from django.core.management import call_command

        account = _account_for(make_user())
        ban = apply_ban(account, reason='限时')
        AccountBan.objects.filter(pk=ban.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        out = StringIO()
        call_command('lift_expired_bans', stdout=out)
        self.assertIn('1', out.getvalue())
        ban.refresh_from_db()
        self.assertEqual(ban.status, AccountBan.Status.EXPIRED)


class ActiveOrdersForTest(APITestCase):
    """必测 #9：抽出来的 active_orders_for 必须与 count_active_orders 同口径。"""

    def setUp(self):
        self.customer = make_user()
        self.main = make_provider()
        self.second = make_provider()
        self.order = make_order(self.customer, provider=self.main)
        self.order.status = Order.Status.IN_SERVICE
        self.order.provider_account = _account_for(self.main)
        self.order.customer_account = _account_for(self.customer)
        self.order.save(update_fields=[
            'status', 'provider_account', 'customer_account',
        ])

    def test_main_provider_counted(self):
        account = _account_for(self.main)
        self.assertEqual(count_active_orders(account), 1)
        self.assertEqual(list(active_orders_for(account)), [self.order])

    def test_secondary_provider_counted(self):
        """双陪次陪走 OrderProvider 中间表，也必须算在途。"""
        second_account = _account_for(self.second)
        OrderProvider.objects.create(
            order=self.order,
            provider=self.second,
            provider_account=second_account,
        )
        self.assertEqual(count_active_orders(second_account), 1)
        self.assertEqual(list(active_orders_for(second_account)), [self.order])

    def test_no_dimension_returns_empty(self):
        self.assertEqual(count_active_orders(), 0)
        self.assertEqual(list(active_orders_for()), [])


class BanApiTest(APITestCase):
    """ban / unban action 的接口级测试。"""

    def setUp(self):
        self.admin = make_superuser()
        self.boss = make_user(nickname='老板甲')
        self.boss_account = _account_for(self.boss)
        self.boss_account.account_type = ClubAccount.AccountType.BOSS
        self.boss_account.save(update_fields=['account_type'])

    def _url(self, action):
        return f'/api/admin/users/{self.boss_account.id}/{action}/'

    def test_ban_success(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post(self._url('ban'), {'reason': '恶意退单'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['code'], 0)
        self.boss_account.refresh_from_db()
        self.assertFalse(self.boss_account.can_login)
        self.assertTrue(
            AccountBan.objects.filter(
                account=self.boss_account, status=AccountBan.Status.ACTIVE,
            ).exists()
        )

    def test_ban_requires_reason(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post(self._url('ban'), {}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_ban_rejects_past_expiry(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post(self._url('ban'), {
            'reason': '限时',
            'expires_at': (timezone.now() - timedelta(hours=1)).isoformat(),
        }, format='json')
        self.assertEqual(res.status_code, 400)

    def test_unban_idempotent(self):
        """必测 #10：没有生效封禁时解封不报错。"""
        self.client.force_authenticate(self.admin)
        res = self.client.post(self._url('unban'), {'reason': '误封'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['code'], 0)

    def test_ban_then_unban_restores(self):
        self.client.force_authenticate(self.admin)
        self.client.post(self._url('ban'), {'reason': '封'}, format='json')
        res = self.client.post(self._url('unban'), {'reason': '解'}, format='json')
        self.assertEqual(res.status_code, 200)
        self.boss_account.refresh_from_db()
        self.assertTrue(self.boss_account.can_login)
        self.assertIsNone(active_ban_for(self.boss_account))

    def test_requires_ban_perm(self):
        """只有 user:view 的运营不能封人。"""
        viewer = make_console_user(perms=['user:view'])
        self.client.force_authenticate(viewer)
        res = self.client.post(self._url('ban'), {'reason': '越权'}, format='json')
        self.assertEqual(res.status_code, 403)

    def test_ban_perm_allows(self):
        operator = make_console_user(perms=['user:view', 'user:ban'])
        self.client.force_authenticate(operator)
        res = self.client.post(self._url('ban'), {'reason': '正常封禁'}, format='json')
        self.assertEqual(res.status_code, 200)


class BanBlockedByActiveOrderTest(APITestCase):
    """必测 #6/#7/#8：在途订单硬阻断与 force 越权。"""

    def setUp(self):
        self.admin = make_superuser()
        self.customer = make_user()
        self.provider = make_provider()
        self.provider_account = _account_for(self.provider)
        self.profile = self.provider.escort_profile
        self.profile.account = self.provider_account
        self.profile.save(update_fields=['account'])

        self.order = make_order(self.customer, provider=self.provider)
        self.order.status = Order.Status.IN_SERVICE
        self.order.provider_account = self.provider_account
        self.order.save(update_fields=['status', 'provider_account'])

    def _url(self, action):
        return f'/api/admin/escorts/{self.profile.id}/{action}/'

    def test_active_order_blocks_with_409(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post(self._url('ban'), {'reason': '在途也要封'}, format='json')
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data['code'], 409)
        self.assertIn(self.order.id, res.data['data']['active_order_ids'])
        # 拦截即未发生，不许留下封禁记录。
        self.assertFalse(AccountBan.objects.filter(account=self.provider_account).exists())

    def test_force_without_perm_403(self):
        operator = make_console_user(perms=['escort:view', 'user:ban'])
        self.client.force_authenticate(operator)
        res = self.client.post(self._url('ban'), {
            'reason': '强封', 'force': True,
        }, format='json')
        self.assertEqual(res.status_code, 403)
        self.assertFalse(AccountBan.objects.filter(account=self.provider_account).exists())

    def test_force_with_perm_succeeds(self):
        operator = make_console_user(
            perms=['escort:view', 'user:ban', 'user:ban_force'],
        )
        self.client.force_authenticate(operator)
        res = self.client.post(self._url('ban'), {
            'reason': '强封', 'force': True,
        }, format='json')
        self.assertEqual(res.status_code, 200)
        self.provider_account.refresh_from_db()
        self.assertFalse(self.provider_account.can_login)

    def test_finished_order_does_not_block(self):
        self.order.status = Order.Status.COMPLETED
        self.order.save(update_fields=['status'])
        self.client.force_authenticate(self.admin)
        res = self.client.post(self._url('ban'), {'reason': '已完结可封'}, format='json')
        self.assertEqual(res.status_code, 200)

    def test_escort_without_account_rejected(self):
        orphan = make_provider()
        profile = orphan.escort_profile
        profile.account = None
        profile.save(update_fields=['account'])
        self.client.force_authenticate(self.admin)
        res = self.client.post(
            f'/api/admin/escorts/{profile.id}/ban/', {'reason': '无账户'}, format='json',
        )
        self.assertEqual(res.status_code, 400)


class BanAuditListTest(APITestCase):
    """BanViewSet 只读审计列表。"""

    def setUp(self):
        self.admin = make_superuser()
        self.a = _account_for(make_user(nickname='甲'))
        self.b = _account_for(make_user(nickname='乙'))
        apply_ban(self.a, reason='甲被封')
        apply_ban(self.b, reason='乙被封')

    def test_list(self):
        self.client.force_authenticate(self.admin)
        res = self.client.get('/api/admin/bans/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['total'], 2)

    def test_filter_by_account(self):
        self.client.force_authenticate(self.admin)
        res = self.client.get(f'/api/admin/bans/?account_id={self.a.id}')
        self.assertEqual(res.data['data']['total'], 1)
        self.assertEqual(res.data['data']['list'][0]['reason'], '甲被封')

    def test_filter_by_status(self):
        self.client.force_authenticate(self.admin)
        res = self.client.get('/api/admin/bans/?status=lifted')
        self.assertEqual(res.data['data']['total'], 0)
        res = self.client.get('/api/admin/bans/?status=active')
        self.assertEqual(res.data['data']['total'], 2)

    def test_read_only(self):
        """审计表不许从 HTTP 层写入，否则审计本身可被篡改。"""
        self.client.force_authenticate(self.admin)
        res = self.client.post('/api/admin/bans/', {'reason': 'x'}, format='json')
        self.assertEqual(res.status_code, 405)

    def test_requires_perm(self):
        nobody = make_console_user(perms=[])
        self.client.force_authenticate(nobody)
        res = self.client.get('/api/admin/bans/')
        self.assertEqual(res.status_code, 403)


class RawPatchBlockedTest(APITestCase):
    """必测 #13：裸 PATCH 三个开关必须被拒，否则封禁流程有后门。"""

    def setUp(self):
        self.admin = make_superuser()
        self.boss = make_user()
        self.account = _account_for(self.boss)
        self.account.account_type = ClubAccount.AccountType.BOSS
        self.account.save(update_fields=['account_type'])
        self.url = f'/api/admin/users/{self.account.id}/'

    def test_patch_is_active_rejected(self):
        self.client.force_authenticate(self.admin)
        res = self.client.patch(self.url, {'is_active': False}, format='json')
        self.assertEqual(res.status_code, 400)
        self.account.refresh_from_db()
        self.assertTrue(self.account.is_active)

    def test_patch_can_login_rejected(self):
        self.client.force_authenticate(self.admin)
        res = self.client.patch(self.url, {'can_login': False}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_patch_can_view_rejected(self):
        self.client.force_authenticate(self.admin)
        res = self.client.patch(self.url, {'can_view': False}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_patch_unchanged_switch_allowed(self):
        """后台表单整体提交会带上这三个字段的当前值，值没变就不能拦。"""
        self.client.force_authenticate(self.admin)
        res = self.client.patch(self.url, {
            'nickname': '改个昵称',
            'is_active': self.account.is_active,
            'can_login': self.account.can_login,
            'can_view': self.account.can_view,
        }, format='json')
        self.assertEqual(res.status_code, 200)
        self.account.refresh_from_db()
        self.assertEqual(self.account.nickname, '改个昵称')

    def test_ban_action_still_works(self):
        """正门必须通：封禁接口不受 validate 拦截影响。"""
        self.client.force_authenticate(self.admin)
        res = self.client.post(
            f'{self.url}ban/', {'reason': '走正门'}, format='json',
        )
        self.assertEqual(res.status_code, 200)
        self.account.refresh_from_db()
        self.assertFalse(self.account.can_login)
