"""封禁审计（ORD-4）测试。

覆盖：序列化器回归、封禁/解封幂等、DB 唯一约束、封禁前开关快照、
在途订单硬阻断（含双陪次席）、force 逃生通道与权限、自动解封、
以及「裸 PATCH 三开关」的拦截。
"""

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from console.ban_utils import active_ban_for, apply_ban, lift_ban, lift_expired_bans
from console.models import AccountBan, AdminAuditLog
from console.serializers import BanRecordSerializer
from orders.models import Order, OrderProvider
from orders.tests.factories import (
    make_console_user,
    make_order,
    make_provider,
    make_superuser,
    make_user,
)


def account_of(legacy_user):
    """取 legacy 用户对应的 ClubAccount（工厂只建 legacy CustomUser）。"""
    return get_or_create_account_for_legacy_user(legacy_user)


class BanRecordSerializerTest(APITestCase):
    """T1 回归：`source='account_id'` 与字段名相同会让 DRF 硬断言失败。

    注意断言方式：DRF 的 ``fields`` 是 lazy ``cached_property``，那条 assert 在
    ``Field.bind()`` 里触发，``__init__`` 阶段根本不执行。所以只写
    ``BanRecordSerializer()`` 的用例在 bug 存在时**也会绿**，是假绿测试。
    必须真正访问 ``.fields`` / ``.data`` 才能把这个 bug 钉住。
    """

    def test_fields_can_be_built(self):
        serializer = BanRecordSerializer()
        # 访问 .fields 才会触发 bind；这一步在修复前抛 AssertionError。
        fields = serializer.fields
        self.assertIn('account_id', fields)

    def test_data_can_be_rendered(self):
        boss = make_user()
        account = account_of(boss)
        ban = apply_ban(account, reason='测试封禁')

        data = BanRecordSerializer(ban).data

        self.assertEqual(data['account_id'], account.id)
        self.assertEqual(data['reason'], '测试封禁')
        self.assertEqual(data['status'], AccountBan.Status.ACTIVE)


class BanPermissionRegistryTest(APITestCase):
    """T1b：新权限点必须出现在权限注册表里，否则后台无法勾选。"""

    def test_ban_permissions_registered(self):
        from console.permissions import ALL_PERMISSION_SET

        self.assertIn('user:ban', ALL_PERMISSION_SET)
        self.assertIn('user:ban_force', ALL_PERMISSION_SET)


class ApplyBanTest(APITestCase):
    """T4/T5：封禁的幂等、唯一约束与开关快照。"""

    def setUp(self):
        self.boss = make_user()
        self.account = account_of(self.boss)

    def test_apply_ban_flips_switches_and_records(self):
        ban = apply_ban(self.account, reason='违规')

        self.account.refresh_from_db()
        self.assertFalse(self.account.is_active)
        self.assertFalse(self.account.can_login)
        self.assertEqual(ban.status, AccountBan.Status.ACTIVE)

    def test_apply_ban_does_not_touch_can_view(self):
        """can_view 是死开关（全仓无鉴权消费点），已从封禁语义摘除。"""
        self.account.can_view = True
        self.account.save(update_fields=['can_view'])

        apply_ban(self.account, reason='违规')

        self.account.refresh_from_db()
        self.assertTrue(self.account.can_view)

    def test_apply_ban_twice_keeps_single_active(self):
        """幂等：连续封禁两次 → 仅 1 条 ACTIVE，旧的标 LIFTED，总数 +2 但生效仅 1。"""
        first = apply_ban(self.account, reason='第一次')
        second = apply_ban(self.account, reason='第二次')

        first.refresh_from_db()
        self.assertEqual(first.status, AccountBan.Status.LIFTED)
        self.assertEqual(second.status, AccountBan.Status.ACTIVE)
        self.assertEqual(
            AccountBan.objects.filter(
                account=self.account, status=AccountBan.Status.ACTIVE,
            ).count(),
            1,
        )

    def test_db_constraint_blocks_second_active_ban(self):
        """T4：DB 层部分唯一约束兜底。

        并发在 SQLite 测试里复现不出来（写事务实质串行），所以不写多线程用例
        （那种测试会假绿）。改为直接绕过 apply_ban 插第二条 ACTIVE，
        断言数据库自己拒绝。
        """
        apply_ban(self.account, reason='第一次')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AccountBan.objects.create(
                    account=self.account,
                    reason='并发插入',
                    status=AccountBan.Status.ACTIVE,
                )

    def test_non_active_bans_are_not_constrained(self):
        """唯一约束是部分索引：LIFTED/EXPIRED 允许多条共存。"""
        apply_ban(self.account, reason='第一次')
        apply_ban(self.account, reason='第二次')
        apply_ban(self.account, reason='第三次')

        self.assertEqual(
            AccountBan.objects.filter(
                account=self.account, status=AccountBan.Status.LIFTED,
            ).count(),
            2,
        )

    def test_apply_ban_requires_reason(self):
        with self.assertRaises(ValueError):
            apply_ban(self.account, reason='  ')


class PreBanStateSnapshotTest(APITestCase):
    """T5：解封必须按封禁前快照恢复，不能无条件写死 True。"""

    def setUp(self):
        self.boss = make_user()
        self.account = account_of(self.boss)

    def test_snapshot_recorded_on_ban(self):
        apply_ban(self.account, reason='违规')

        ban = active_ban_for(self.account)
        self.assertEqual(
            ban.pre_ban_state, {'is_active': True, 'can_login': True},
        )

    def test_lift_restores_disabled_account_to_disabled(self):
        """核心防越权用例：封禁前就已停用的账户，解封后仍须停用。"""
        self.account.is_active = False
        self.account.save(update_fields=['is_active'])

        ban = apply_ban(self.account, reason='违规')
        lift_ban(ban, reason='申诉通过')

        self.account.refresh_from_db()
        self.assertFalse(
            self.account.is_active,
            '解封不得凭空恢复账户封禁前本来就没有的权限',
        )
        self.assertTrue(self.account.can_login)

    def test_lift_restores_enabled_account_to_enabled(self):
        ban = apply_ban(self.account, reason='违规')
        lift_ban(ban, reason='申诉通过')

        self.account.refresh_from_db()
        self.assertTrue(self.account.is_active)
        self.assertTrue(self.account.can_login)

    def test_legacy_ban_without_snapshot_falls_back_to_true(self):
        """存量兼容：老记录没有快照（空 dict），解封回落到恢复 True。"""
        ban = apply_ban(self.account, reason='违规')
        AccountBan.objects.filter(pk=ban.pk).update(pre_ban_state={})
        ban.refresh_from_db()

        lift_ban(ban, reason='申诉通过')

        self.account.refresh_from_db()
        self.assertTrue(self.account.is_active)
        self.assertTrue(self.account.can_login)

    def test_lift_is_idempotent(self):
        ban = apply_ban(self.account, reason='违规')
        lift_ban(ban, reason='第一次')
        lifted_at = ban.lifted_at

        lift_ban(ban, reason='第二次')

        ban.refresh_from_db()
        self.assertEqual(ban.lifted_at, lifted_at)
        self.assertEqual(ban.lift_reason, '第一次')
